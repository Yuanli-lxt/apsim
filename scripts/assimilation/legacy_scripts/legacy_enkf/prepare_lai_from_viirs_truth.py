from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Tuple

import numpy as np
import pandas as pd


def load_viirs_lai(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    date_col = cols.get("date")
    lai_col = cols.get("lai")
    if date_col is None or lai_col is None:
        raise KeyError(f"VIIRS CSV must contain date and LAI columns. Existing: {list(df.columns)}")

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col], errors="coerce")
    out["lai_raw"] = pd.to_numeric(df[lai_col], errors="coerce")
    out = out.dropna(subset=["date", "lai_raw"]).sort_values("date").reset_index(drop=True)
    return out


def load_truth_xls_wide(xls_path: Path, sheet_name: str = "Sheet1") -> pd.DataFrame:
    df = pd.read_excel(xls_path, sheet_name=sheet_name, engine="xlrd")
    if df.empty:
        raise ValueError(f"Truth xls sheet {sheet_name!r} is empty.")

    date_cols = [str(c).strip() for c in df.columns]
    lai_vals = pd.to_numeric(df.iloc[0].values, errors="coerce")
    truth = pd.DataFrame(
        {
            "date": pd.to_datetime(date_cols, format="%Y%m%d", errors="coerce"),
            "lai_truth": lai_vals,
        }
    )
    truth = truth.dropna(subset=["date", "lai_truth"]).sort_values("date").reset_index(drop=True)
    if truth.empty:
        raise ValueError("No valid truth points were parsed from xls.")
    return truth


def pair_truth_and_viirs(truth: pd.DataFrame, viirs: pd.DataFrame, tol_days: int) -> pd.DataFrame:
    left = truth.sort_values("date").rename(columns={"date": "date_truth"})
    right = viirs.sort_values("date")[["date", "lai_raw"]].rename(columns={"date": "date_viirs"})
    pairs = pd.merge_asof(
        left,
        right,
        left_on="date_truth",
        right_on="date_viirs",
        direction="nearest",
        tolerance=pd.Timedelta(days=max(0, int(tol_days))),
    )
    pairs = pairs.dropna(subset=["lai_truth", "lai_raw", "date_viirs"]).reset_index(drop=True)
    return pairs


def _fit_linear_closed_form(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    x_mean = float(np.mean(x))
    y_mean = float(np.mean(y))
    var_x = float(np.mean((x - x_mean) ** 2))
    if var_x <= 1e-12:
        return 1.0, y_mean - x_mean
    cov_xy = float(np.mean((x - x_mean) * (y - y_mean)))
    slope = cov_xy / var_x
    intercept = y_mean - slope * x_mean
    return float(slope), float(intercept)


def fit_huber_linear(x: np.ndarray, y: np.ndarray, n_iter: int = 8, c: float = 2.5) -> Tuple[float, float]:
    slope, intercept = _fit_linear_closed_form(x, y)
    for _ in range(n_iter):
        pred = intercept + slope * x
        res = y - pred
        mad = float(np.median(np.abs(res - np.median(res)))) + 1e-9
        scale = 1.4826 * mad
        r = np.abs(res) / max(scale, 1e-9)
        w = np.ones_like(r)
        mask = r > c
        w[mask] = c / r[mask]

        sw = float(np.sum(w))
        if sw <= 1e-12:
            break
        xw = float(np.sum(w * x) / sw)
        yw = float(np.sum(w * y) / sw)
        var_x = float(np.sum(w * (x - xw) ** 2) / sw)
        if var_x <= 1e-12:
            break
        cov_xy = float(np.sum(w * (x - xw) * (y - yw)) / sw)
        slope = cov_xy / var_x
        intercept = yw - slope * xw
    return float(slope), float(intercept)


def fit_ratio(x: np.ndarray, y: np.ndarray) -> float:
    denom = float(np.sum(x * x))
    if denom <= 1e-12:
        return 1.0
    return float(np.sum(x * y) / denom)


def choose_calibration_model(pairs: pd.DataFrame) -> Dict[str, float]:
    if len(pairs) < 1:
        return {
            "model": "identity",
            "slope": 1.0,
            "intercept": 0.0,
            "rmse_linear": np.nan,
            "rmse_ratio": np.nan,
            "rmse_best": np.nan,
        }

    x = pairs["lai_raw"].to_numpy(dtype=float)
    y = pairs["lai_truth"].to_numpy(dtype=float)

    if len(pairs) >= 2:
        slope_l, intercept_l = fit_huber_linear(x, y)
    else:
        slope_l, intercept_l = 1.0, float(y[0] - x[0])

    yhat_l = intercept_l + slope_l * x
    rmse_l = float(np.sqrt(np.mean((yhat_l - y) ** 2)))

    slope_r = fit_ratio(x, y)
    yhat_r = slope_r * x
    rmse_r = float(np.sqrt(np.mean((yhat_r - y) ** 2)))

    # choose by fit quality; allow negative slope if it clearly fits better
    choose_linear = rmse_l <= rmse_r * 1.02
    if choose_linear:
        model = "huber_linear"
        slope = float(slope_l)
        intercept = float(intercept_l)
        rmse_best = rmse_l
    else:
        model = "ratio"
        slope = float(max(slope_r, 1e-6))
        intercept = 0.0
        rmse_best = rmse_r

    return {
        "model": model,
        "slope": slope,
        "intercept": intercept,
        "rmse_linear": rmse_l,
        "rmse_ratio": rmse_r,
        "rmse_best": rmse_best,
        "n_pairs": float(len(pairs)),
    }


def temporal_ratio_interpolation(viirs: pd.DataFrame, pairs: pd.DataFrame, eps: float = 1e-6) -> pd.DataFrame:
    p = pairs.copy()
    p["factor"] = p["lai_truth"] / np.maximum(p["lai_raw"], eps)
    p = p.replace([np.inf, -np.inf], np.nan).dropna(subset=["factor"]).sort_values("date_viirs")
    if p.empty:
        out = viirs.copy()
        out["factor"] = 1.0
        out["lai"] = out["lai_raw"]
        return out

    # Guard against extreme anchors from noisy single points.
    p["factor"] = p["factor"].clip(lower=0.2, upper=12.0)

    anchor = (
        p[["date_viirs", "factor"]]
        .drop_duplicates("date_viirs", keep="last")
        .rename(columns={"date_viirs": "date"})
        .set_index("date")
        .sort_index()
    )
    full = viirs.copy().set_index("date").sort_index()
    full = full.join(anchor, how="left")
    full["factor"] = full["factor"].interpolate(method="time").ffill().bfill()
    full["lai"] = full["lai_raw"] * full["factor"]
    return full.reset_index()


def evaluate_temporal_ratio(pairs: pd.DataFrame) -> Dict[str, float]:
    if pairs.empty:
        return {"rmse_temporal_ratio": np.nan}
    tmp = temporal_ratio_interpolation(
        viirs=pairs[["date_viirs", "lai_raw"]].rename(columns={"date_viirs": "date"}),
        pairs=pairs,
    )
    pred = tmp["lai"].to_numpy(dtype=float)
    y = pairs["lai_truth"].to_numpy(dtype=float)
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    return {"rmse_temporal_ratio": rmse}


def apply_calibration(
    viirs: pd.DataFrame, calib: Dict[str, float], min_lai: float = 0.0, max_lai: float = 8.0
) -> pd.DataFrame:
    out = viirs.copy()
    out["lai"] = calib["intercept"] + calib["slope"] * out["lai_raw"]
    out["lai"] = out["lai"].clip(lower=min_lai, upper=max_lai)
    return out


def estimate_obs_std(pairs: pd.DataFrame, calib: Dict[str, float], floor_std: float = 0.12) -> float:
    if pairs.empty:
        return floor_std
    pred = calib["intercept"] + calib["slope"] * pairs["lai_raw"].to_numpy(dtype=float)
    err = pred - pairs["lai_truth"].to_numpy(dtype=float)
    rmse = float(np.sqrt(np.mean(err**2)))
    return float(max(rmse, floor_std))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calibrate VIIRS LAI using sparse truth xls and export assimilation-ready LAI csv."
    )
    parser.add_argument("--viirs-csv", default="mission/LAI_VIIRS_VNP15A2H_Point_2024_2025.csv")
    parser.add_argument("--truth-xls", default="mission/LAI真值.xls")
    parser.add_argument("--truth-sheet", default="Sheet1")
    parser.add_argument("--tol-days", type=int, default=5)
    parser.add_argument("--min-lai", type=float, default=0.0)
    parser.add_argument("--max-lai", type=float, default=8.0)
    parser.add_argument("--out-prefix", default="mission/lai_viirs_calibrated_for_assim")
    args = parser.parse_args()

    viirs_path = Path(args.viirs_csv)
    truth_path = Path(args.truth_xls)
    out_prefix = Path(args.out_prefix)

    viirs = load_viirs_lai(viirs_path)
    truth = load_truth_xls_wide(truth_path, sheet_name=args.truth_sheet)
    pairs = pair_truth_and_viirs(truth, viirs, tol_days=args.tol_days)
    if pairs.empty:
        raise RuntimeError("No matched truth-VIIRS pairs under tolerance; cannot calibrate.")

    calib = choose_calibration_model(pairs)
    temporal_eval = evaluate_temporal_ratio(pairs)

    # Prefer temporal ratio interpolation for sparse-truth calibration unless
    # global regression is clearly better.
    use_temporal = True
    if np.isfinite(calib.get("rmse_best", np.nan)) and np.isfinite(temporal_eval.get("rmse_temporal_ratio", np.nan)):
        use_temporal = temporal_eval["rmse_temporal_ratio"] <= calib["rmse_best"] * 1.03

    if use_temporal:
        calibrated = temporal_ratio_interpolation(viirs, pairs)
        calibrated["model_used"] = "temporal_ratio_interp"
        calibrated["lai"] = calibrated["lai"].clip(lower=args.min_lai, upper=args.max_lai)
        # Evaluate temporal model at paired dates
        paired_pred = temporal_ratio_interpolation(
            viirs=pairs[["date_viirs", "lai_raw"]].rename(columns={"date_viirs": "date"}).copy(),
            pairs=pairs,
        )
        pair_pred = paired_pred["lai"].to_numpy(dtype=float)
        rmse_used = float(np.sqrt(np.mean((pair_pred - pairs["lai_truth"].to_numpy(dtype=float)) ** 2)))
        model_used = "temporal_ratio_interp"
        slope_used = np.nan
        intercept_used = np.nan
    else:
        calibrated = apply_calibration(viirs, calib, min_lai=args.min_lai, max_lai=args.max_lai)
        calibrated["model_used"] = calib["model"]
        rmse_used = float(calib["rmse_best"])
        model_used = str(calib["model"])
        slope_used = float(calib["slope"])
        intercept_used = float(calib["intercept"])

    std_value = float(max(rmse_used, 0.20))
    calibrated["std"] = std_value

    assim = calibrated[["date", "lai", "std"]].copy()
    assim["date"] = pd.to_datetime(assim["date"]).dt.strftime("%Y-%m-%d")

    pairs_out = pairs.copy()
    if model_used == "temporal_ratio_interp":
        paired_pred = temporal_ratio_interpolation(
            viirs=pairs[["date_viirs", "lai_raw"]].rename(columns={"date_viirs": "date"}).copy(),
            pairs=pairs,
        )
        pairs_out["lai_pred_calibrated"] = paired_pred["lai"].to_numpy(dtype=float)
    else:
        pairs_out["lai_pred_calibrated"] = intercept_used + slope_used * pairs_out["lai_raw"]
    pairs_out["error_after_calib"] = pairs_out["lai_pred_calibrated"] - pairs_out["lai_truth"]
    pairs_out["abs_error_after_calib"] = pairs_out["error_after_calib"].abs()
    pairs_out["date_truth"] = pd.to_datetime(pairs_out["date_truth"]).dt.strftime("%Y-%m-%d")
    pairs_out["date_viirs"] = pd.to_datetime(pairs_out["date_viirs"]).dt.strftime("%Y-%m-%d")

    truth_out = truth.copy()
    truth_out["date"] = pd.to_datetime(truth_out["date"]).dt.strftime("%Y-%m-%d")

    out_dir = out_prefix.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    assim_path = out_prefix.with_name(out_prefix.name + "_obs.csv")
    full_path = out_prefix.with_name(out_prefix.name + "_full.csv")
    pairs_path = out_prefix.with_name(out_prefix.name + "_pairs.csv")
    truth_path_out = out_prefix.with_name(out_prefix.name + "_truth_long.csv")
    diag_path = out_prefix.with_name(out_prefix.name + "_diagnostics.json")

    assim.to_csv(assim_path, index=False, encoding="utf-8-sig")
    calibrated.to_csv(full_path, index=False, encoding="utf-8-sig")
    pairs_out.to_csv(pairs_path, index=False, encoding="utf-8-sig")
    truth_out.to_csv(truth_path_out, index=False, encoding="utf-8-sig")

    diagnostics = {
        **{k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in calib.items()},
        **{k: (float(v) if isinstance(v, (int, float, np.floating)) else v) for k, v in temporal_eval.items()},
        "model_used": model_used,
        "slope_used": slope_used,
        "intercept_used": intercept_used,
        "rmse_used": rmse_used,
        "obs_std_assigned": float(std_value),
        "tol_days": int(args.tol_days),
        "viirs_csv": str(viirs_path),
        "truth_xls": str(truth_path),
        "truth_sheet": args.truth_sheet,
        "output_obs_csv": str(assim_path),
    }
    with open(diag_path, "w", encoding="utf-8") as f:
        json.dump(diagnostics, f, ensure_ascii=False, indent=2)

    print("Calibration finished.")
    print(f"Assimilation obs csv: {assim_path}")
    print(f"Diagnostics json: {diag_path}")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
