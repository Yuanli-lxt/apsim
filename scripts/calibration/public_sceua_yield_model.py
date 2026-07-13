"""SCE-UA calibration using only public Shandong city statistics and weather.

This is a transparent regional statistical yield emulator, not an APSIM
parameter calibration. Aggregate public yields do not uniquely identify APSIM
cultivar, soil and management parameters. The emulator provides a reproducible
public-data benchmark and tests the SCE-UA implementation before coupling the
optimizer to the much more expensive APSIM runner.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "shandong_public"
YEARBOOK = RAW / "statistics" / "yearbook_city_crops"
WEATHER = RAW / "weather" / "nasa_power"
CITIES = RAW / "boundaries" / "shandong_prefecture_datav.geojson"
OUT = ROOT / "outputs" / "calibration" / "public_sceua_shandong"
FIG = ROOT / "outputs" / "figures" / "shandong_public_validation"


def norm_text(value: object) -> str:
    return re.sub(r"\s+", "", str(value))


def find_crop_col(raw: pd.DataFrame, crop: str) -> int:
    for col in range(raw.shape[1]):
        text = "".join(norm_text(raw.iat[row, col]) for row in range(min(10, raw.shape[0])) if pd.notna(raw.iat[row, col]))
        if crop in text:
            return col
    raise ValueError(f"Cannot locate {crop} columns")


def parse_yearbook(path: Path, data_year: int) -> pd.DataFrame:
    raw = pd.read_excel(path, header=None)
    w = find_crop_col(raw, "小麦")
    m = find_crop_col(raw, "玉米")
    start = next(i for i in range(len(raw)) if "全省" in norm_text(raw.iat[i, 0]))
    rows = []
    for i in range(start + 1, len(raw)):
        city = norm_text(raw.iat[i, 0])
        if not city.endswith("市"):
            continue
        vals = [pd.to_numeric(raw.iat[i, j], errors="coerce") for j in [w, w + 1, w + 2, m, m + 1, m + 2]]
        rows.append([data_year, city, *vals])
    out = pd.DataFrame(rows, columns=[
        "year", "city_cn", "wheat_area_ha", "wheat_production_t", "wheat_yield_kg_ha",
        "maize_area_ha", "maize_production_t", "maize_yield_kg_ha",
    ])
    # Laiwu was merged into Jinan in 2019; harmonise the historical series to
    # the current 16-city geography using area-weighted yield.
    if "莱芜市" in set(out.city_cn):
        for crop in ["wheat", "maize"]:
            area = out.loc[out.city_cn.isin(["济南市", "莱芜市"]), f"{crop}_area_ha"].sum()
            prod = out.loc[out.city_cn.isin(["济南市", "莱芜市"]), f"{crop}_production_t"].sum()
            out.loc[out.city_cn == "济南市", f"{crop}_area_ha"] = area
            out.loc[out.city_cn == "济南市", f"{crop}_production_t"] = prod
            out.loc[out.city_cn == "济南市", f"{crop}_yield_kg_ha"] = prod * 1000 / area
        out = out[out.city_cn != "莱芜市"]
    return out


def load_statistics() -> pd.DataFrame:
    frames = []
    for yearbook_year in range(2019, 2026):
        table = "13-10" if yearbook_year == 2019 else ("13-10-0" if yearbook_year == 2020 else "13-09")
        path = YEARBOOK / f"shandong_yearbook_{yearbook_year}_{table}.xls"
        frames.append(parse_yearbook(path, yearbook_year - 1))
    return pd.concat(frames, ignore_index=True)


def read_power_csv(path: Path, variable: str) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("LAT,LON"))
    data = pd.read_csv(path, skiprows=header)
    data[variable] = pd.to_numeric(data[variable], errors="coerce").replace(-999, np.nan)
    return data


def weather_features() -> pd.DataFrame:
    cities = gpd.read_file(CITIES)
    centers = {r["name"]: (float(r["center"][0]), float(r["center"][1])) for _, r in cities.iterrows()}
    rows = []
    for year in range(2018, 2025):
        variables = {v: read_power_csv(WEATHER / str(year) / f"{v}.csv", v)
                     for v in ["T2M_MAX", "T2M_MIN", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN"]}
        for city, (lon, lat) in centers.items():
            series = {}
            for var, frame in variables.items():
                coords = frame[["LAT", "LON"]].drop_duplicates()
                idx = ((coords.LAT - lat) ** 2 + (coords.LON - lon) ** 2).idxmin()
                plat, plon = coords.loc[idx, ["LAT", "LON"]]
                sub = frame[(frame.LAT == plat) & (frame.LON == plon)].copy()
                sub["date"] = pd.to_datetime(sub.YEAR.astype(int).astype(str) + sub.DOY.astype(int).astype(str).str.zfill(3), format="%Y%j")
                series[var] = sub.set_index("date")[var]
            daily = pd.DataFrame(series)
            daily["tmean"] = (daily.T2M_MAX + daily.T2M_MIN) / 2
            seasons = {"wheat": daily[(daily.index.month >= 1) & (daily.index.month <= 6)],
                       "maize": daily[(daily.index.month >= 6) & (daily.index.month <= 9)]}
            for crop, s in seasons.items():
                rows.append({
                    "year": year, "city_cn": city, "crop": crop,
                    "temp_mean": s.tmean.mean(), "temp_max_mean": s.T2M_MAX.mean(),
                    "rain_sum": s.PRECTOTCORR.sum(), "rad_mean": s.ALLSKY_SFC_SW_DWN.mean(),
                    "heat_days": float((s.T2M_MAX > 32).sum()),
                })
    return pd.DataFrame(rows)


class SCEUA:
    def __init__(self, bounds: np.ndarray, seed: int = 42, complexes: int = 3):
        self.bounds = np.asarray(bounds, float)
        self.rng = np.random.default_rng(seed)
        self.ngs = complexes
        self.n = len(bounds)
        self.npg = 2 * self.n + 1
        self.nps = self.n + 1

    def _sample(self, n: int) -> np.ndarray:
        lo, hi = self.bounds[:, 0], self.bounds[:, 1]
        return lo + self.rng.random((n, self.n)) * (hi - lo)

    def optimize(self, objective, max_evals: int = 1500) -> tuple[np.ndarray, pd.DataFrame]:
        npt = self.ngs * self.npg
        pop = self._sample(npt)
        score = np.array([objective(x) for x in pop])
        evals = npt
        history = []
        while evals < max_evals:
            order = np.argsort(score); pop, score = pop[order], score[order]
            history.append({"evaluations": evals, "best_objective": float(score[0])})
            for g in range(self.ngs):
                idx = np.arange(g, npt, self.ngs)[:self.npg]
                comp, cs = pop[idx].copy(), score[idx].copy()
                for _ in range(self.npg):
                    ranks = np.arange(1, self.npg + 1)
                    prob = 2 * (self.npg + 1 - ranks) / (self.npg * (self.npg + 1))
                    chosen = np.sort(self.rng.choice(self.npg, self.nps, replace=False, p=prob))
                    simplex, ss = comp[chosen].copy(), cs[chosen].copy()
                    so = np.argsort(ss); simplex, ss = simplex[so], ss[so]
                    centroid = simplex[:-1].mean(axis=0)
                    candidate = 2 * centroid - simplex[-1]
                    if np.any(candidate < self.bounds[:, 0]) or np.any(candidate > self.bounds[:, 1]):
                        candidate = (centroid + self._sample(1)[0]) / 2
                    val = objective(candidate); evals += 1
                    if val >= ss[-1]:
                        candidate = (centroid + simplex[-1]) / 2
                        val = objective(candidate); evals += 1
                    if val >= ss[-1]:
                        candidate = self._sample(1)[0]
                        val = objective(candidate); evals += 1
                    comp[chosen[-1]], cs[chosen[-1]] = candidate, val
                    co = np.argsort(cs); comp, cs = comp[co], cs[co]
                    if evals >= max_evals:
                        break
                pop[idx], score[idx] = comp, cs
                if evals >= max_evals:
                    break
        order = np.argsort(score)
        return pop[order[0]], pd.DataFrame(history)


def metrics(group: pd.DataFrame) -> dict:
    y, p = group.yield_obs.to_numpy(), group.yield_pred.to_numpy()
    return {"n": len(group), "RMSE": mean_squared_error(y, p) ** 0.5,
            "MAE": mean_absolute_error(y, p), "Bias": float(np.mean(p - y)),
            "R2": r2_score(y, p) if len(group) > 1 else np.nan}


def fit_crop(data: pd.DataFrame, crop: str) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    d = data[data.crop == crop].copy()
    train = d.year <= 2022
    feature_cols = ["temp_mean", "temp_max_mean", "rain_sum", "rad_mean", "heat_days"]
    means, stds = d.loc[train, feature_cols].mean(), d.loc[train, feature_cols].std().replace(0, 1)
    for col in feature_cols:
        d[f"z_{col}"] = (d[col] - means[col]) / stds[col]
    baseline = d.loc[train].groupby("city_cn").yield_obs.mean()
    d["baseline"] = d.city_cn.map(baseline)
    d["trend"] = (d.year - 2020) / 4
    xcols = [f"z_{c}" for c in feature_cols] + ["trend"]
    X = d[xcols].to_numpy(float)
    base = d.baseline.to_numpy(float)
    y = d.yield_obs.to_numpy(float)
    train_idx = np.flatnonzero(train.to_numpy())

    def predict(theta: np.ndarray) -> np.ndarray:
        return theta[0] * base + X @ theta[1:]

    def objective(theta: np.ndarray) -> float:
        residual = predict(theta)[train_idx] - y[train_idx]
        return float(np.sqrt(np.mean(residual ** 2)) + 0.0005 * np.sum(theta[1:] ** 2))

    bounds = np.array([[0.7, 1.3]] + [[-1200, 1200]] * len(xcols))
    best, history = SCEUA(bounds, seed=2026 + (0 if crop == "wheat" else 1)).optimize(objective)
    d["yield_pred"] = predict(best)
    d["split"] = np.select([d.year <= 2022, d.year == 2023], ["train", "validation"], default="test")
    result_metrics = {split: metrics(g) for split, g in d.groupby("split")}
    result_metrics["parameters"] = {name: float(value) for name, value in zip(["baseline_scale", *xcols], best)}
    return d, result_metrics, history


def plot_validation(results: pd.DataFrame) -> None:
    mpl.rcParams.update({"font.family": "sans-serif", "font.sans-serif": ["Microsoft YaHei", "Arial", "DejaVu Sans"],
                         "svg.fonttype": "none", "pdf.fonttype": 42, "font.size": 7,
                         "axes.spines.right": False, "axes.spines.top": False, "legend.frameon": False})
    FIG.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(6.8, 3.0), constrained_layout=True)
    colors = {"train": "#9E9E9E", "validation": "#E6A15A", "test": "#287D5B"}
    for ax, crop, title in zip(axes, ["wheat", "maize"], ["冬小麦", "玉米"]):
        d = results[results.crop == crop]
        lo = min(d.yield_obs.min(), d.yield_pred.min()) * 0.95
        hi = max(d.yield_obs.max(), d.yield_pred.max()) * 1.05
        for split, g in d.groupby("split"):
            ax.scatter(g.yield_obs, g.yield_pred, s=18, alpha=0.78, color=colors[split], label=split)
        ax.plot([lo, hi], [lo, hi], "--", color="#555555", lw=0.8)
        test = d[d.split == "test"]
        mt = metrics(test)
        ax.text(0.04, 0.96, f"2024独立测试\n$R^2$={mt['R2']:.2f}\nRMSE={mt['RMSE']:.0f} kg/ha",
                transform=ax.transAxes, va="top")
        ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel="统计单产 (kg/ha)", ylabel="模型单产 (kg/ha)")
        ax.set_title(title, weight="bold")
    axes[1].legend(loc="lower right")
    fig.suptitle("公开数据SCE-UA区域单产模型：时间独立验证", fontsize=9, weight="bold")
    base = FIG / "public_sceua_temporal_validation"
    fig.savefig(base.with_suffix(".svg"), bbox_inches="tight")
    fig.savefig(base.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(base.with_suffix(".tiff"), dpi=600, bbox_inches="tight")
    fig.savefig(base.with_suffix(".png"), dpi=220, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stats = load_statistics()
    weather = weather_features()
    long = []
    for crop in ["wheat", "maize"]:
        cols = ["year", "city_cn", f"{crop}_yield_kg_ha"]
        part = stats[cols].rename(columns={f"{crop}_yield_kg_ha": "yield_obs"})
        part["crop"] = crop
        long.append(part)
    dataset = pd.concat(long, ignore_index=True).merge(weather, on=["year", "city_cn", "crop"], how="inner")
    outputs, summary = [], {}
    for crop in ["wheat", "maize"]:
        fitted, crop_metrics, history = fit_crop(dataset, crop)
        outputs.append(fitted)
        summary[crop] = crop_metrics
        history.to_csv(OUT / f"{crop}_sceua_convergence.csv", index=False)
    result = pd.concat(outputs, ignore_index=True)
    result.to_csv(OUT / "predictions.csv", index=False, encoding="utf-8-sig")
    (OUT / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    plot_validation(result)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
