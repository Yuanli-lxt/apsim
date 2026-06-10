from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


@dataclass
class ParameterSpec:
    name: str
    selector_type: str
    selector: str
    mode: str
    prior_mean: float
    prior_std: float
    min_value: float
    max_value: float


@dataclass
class ReportSpec:
    output_pattern: str
    date_col: str
    lai_col: str
    biomass_col: Optional[str] = None
    yield_col: Optional[str] = None
    date_format: Optional[str] = None
    state_col: Optional[str] = None
    crop_lai_map: Optional[Dict[str, str]] = None
    crop_biomass_map: Optional[Dict[str, str]] = None
    crop_yield_map: Optional[Dict[str, str]] = None


@dataclass
class EnKFSpec:
    n_ensemble: int
    inflation: float
    process_noise_frac: float
    random_seed: int


@dataclass
class Var4DSpec:
    max_iter: int
    init_step_frac: float
    step_shrink: float
    min_step_frac: float
    improvement_tol: float
    prior_weight: float
    multistart: int
    random_seed: int


@dataclass
class SatelliteCorrectionSpec:
    truth_csv: str
    satellite_csv: str
    satellite_date_col: str
    satellite_lai_col: str
    satellite_std_col: Optional[str]
    join_tolerance_days: int
    method: str
    anchor_truth_points: bool
    min_lai: float
    max_lai: float
    default_std: float


@dataclass
class ExperimentSpec:
    methods: List[str]
    obs_source: str
    max_obs: Optional[int]


@dataclass
class TruthValidationSpec:
    enabled: bool
    excel_path: str
    sheet_name: Optional[str]
    crop_name: Optional[str]
    plot_id: Optional[str]


@dataclass
class Config:
    template_apsim: str
    apsim_exe: str
    workspace: str
    report: ReportSpec
    enkf: EnKFSpec
    var4d: Var4DSpec
    satellite_correction: SatelliteCorrectionSpec
    experiment: ExperimentSpec
    truth_validation: TruthValidationSpec
    parameters: List[ParameterSpec]


def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    var4d_raw = raw.get("var4d", {})
    sat_raw = raw["satellite_correction"]
    exp_raw = raw.get("experiment", {})

    truth_raw = raw.get("truth_validation", {})

    return Config(
        template_apsim=raw["template_apsim"],
        apsim_exe=raw["apsim_exe"],
        workspace=raw["workspace"],
        report=ReportSpec(**raw["report"]),
        enkf=EnKFSpec(**raw["enkf"]),
        var4d=Var4DSpec(
            max_iter=int(var4d_raw.get("max_iter", 25)),
            init_step_frac=float(var4d_raw.get("init_step_frac", 0.12)),
            step_shrink=float(var4d_raw.get("step_shrink", 0.55)),
            min_step_frac=float(var4d_raw.get("min_step_frac", 0.01)),
            improvement_tol=float(var4d_raw.get("improvement_tol", 1e-4)),
            prior_weight=float(var4d_raw.get("prior_weight", 1.0)),
            multistart=int(var4d_raw.get("multistart", 2)),
            random_seed=int(var4d_raw.get("random_seed", 2026)),
        ),
        satellite_correction=SatelliteCorrectionSpec(
            truth_csv=sat_raw["truth_csv"],
            satellite_csv=sat_raw["satellite_csv"],
            satellite_date_col=sat_raw.get("satellite_date_col", "date"),
            satellite_lai_col=sat_raw.get("satellite_lai_col", "lai"),
            satellite_std_col=sat_raw.get("satellite_std_col"),
            join_tolerance_days=int(sat_raw.get("join_tolerance_days", 5)),
            method=str(sat_raw.get("method", "linear")).lower(),
            anchor_truth_points=bool(sat_raw.get("anchor_truth_points", True)),
            min_lai=float(sat_raw.get("min_lai", 0.0)),
            max_lai=float(sat_raw.get("max_lai", 12.0)),
            default_std=float(sat_raw.get("default_std", 0.25)),
        ),
        experiment=ExperimentSpec(
            methods=[str(m).lower() for m in exp_raw.get("methods", ["open_loop", "enkf", "4dvar", "enkf_4dvar"])],
            obs_source=str(exp_raw.get("obs_source", "corrected_satellite")).lower(),
            max_obs=exp_raw.get("max_obs"),
        ),
        truth_validation=TruthValidationSpec(
            enabled=bool(truth_raw.get("enabled", False)),
            excel_path=str(truth_raw.get("excel_path", "")).strip(),
            sheet_name=truth_raw.get("sheet_name"),
            crop_name=truth_raw.get("crop_name"),
            plot_id=truth_raw.get("plot_id"),
        ),
        parameters=[ParameterSpec(**p) for p in raw["parameters"]],
    )


def is_number(text: Optional[str]) -> bool:
    if text is None:
        return False
    try:
        float(str(text).strip())
        return True
    except Exception:
        return False


def normalize_date_column(s: pd.Series, date_format: Optional[str]) -> pd.Series:
    s = s.astype(str).str.strip()
    if date_format:
        return pd.to_datetime(s, format=date_format, errors="coerce")
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        dt = pd.to_datetime(s, format=fmt, errors="coerce")
        if dt.notna().any():
            return dt
    return pd.to_datetime(s, format="mixed", errors="coerce")


def _find_column_case_insensitive(df: pd.DataFrame, candidates: Sequence[str], kind: str) -> str:
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    for candidate in candidates:
        key = str(candidate).strip().lower()
        if key in cols:
            return cols[key]
    normalized = {"".join(ch for ch in str(c).lower() if ch.isalnum()): str(c).strip() for c in df.columns}
    for candidate in candidates:
        key = "".join(ch for ch in str(candidate).lower() if ch.isalnum())
        if key in normalized:
            return normalized[key]
    raise KeyError(f"{kind} column not found. Tried {list(candidates)}. Existing columns: {list(df.columns)}")


def get_matching_elements(root: ET.Element, selector_type: str, selector: str) -> List[ET.Element]:
    selector_low = selector.lower()
    matched: List[ET.Element] = []
    if selector_type == "path":
        matched = root.findall(selector)
    elif selector_type == "tag_contains":
        for elem in root.iter():
            tag_hit = selector_low in str(elem.tag).lower()
            attr_hit = any(selector_low in str(v).lower() for v in elem.attrib.values())
            if tag_hit or attr_hit:
                matched.append(elem)
    else:
        raise ValueError(f"Unsupported selector_type: {selector_type}")
    return matched


def get_first_numeric_element(root: ET.Element, selector_type: str, selector: str) -> ET.Element:
    matches = get_matching_elements(root, selector_type, selector)
    numeric_matches = [m for m in matches if is_number(m.text)]
    if not numeric_matches:
        raise ValueError(f"No numeric XML element matched selector_type={selector_type!r}, selector={selector!r}.")
    return numeric_matches[0]


def read_base_parameter_values(template_apsim: str, parameters: Sequence[ParameterSpec]) -> Dict[str, float]:
    tree = ET.parse(template_apsim)
    root = tree.getroot()
    base = {}
    for p in parameters:
        elem = get_first_numeric_element(root, p.selector_type, p.selector)
        base[p.name] = float(str(elem.text).strip())
    return base


def apply_parameter_values(
    src_apsim: str,
    dst_apsim: str,
    parameters: Sequence[ParameterSpec],
    base_values: Dict[str, float],
    state_vector: np.ndarray,
) -> None:
    tree = ET.parse(src_apsim)
    root = tree.getroot()
    for i, p in enumerate(parameters):
        elem = get_first_numeric_element(root, p.selector_type, p.selector)
        x = float(state_vector[i])
        new_value = base_values[p.name] * x if p.mode == "multiply" else x
        if p.mode not in ("multiply", "set"):
            raise ValueError(f"Unsupported mode: {p.mode}")
        elem.text = f"{new_value:.10g}"
    tree.write(dst_apsim, encoding="utf-8")


def run_apsim_classic(apsim_exe: str, apsim_file: str, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    proc = subprocess.run([apsim_exe, apsim_file], cwd=cwd, capture_output=True, text=False, check=False)

    def _decode(buf: bytes) -> str:
        try:
            return buf.decode("utf-8")
        except Exception:
            return buf.decode("gbk", errors="ignore")

    stdout = _decode(proc.stdout or b"")
    stderr = _decode(proc.stderr or b"")
    return proc.returncode, stdout, stderr


def find_output_file(run_dir: str, pattern: str) -> Path:
    files = sorted(Path(run_dir).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No output file matched pattern {pattern!r} in {run_dir}")
    return files[0]


def read_apsim_output_table(path: str) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    header_idx, sep = None, r"\s+"
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue
        parts_tab = [c.strip() for c in s.split("\t") if c.strip()]
        parts_ws = s.split()
        for parts, candidate_sep in ((parts_tab, "\t"), (parts_ws, r"\s+")):
            if len(parts) < 2:
                continue
            cand = [p.lower() for p in parts]
            if any(x in cand for x in ["date", "day", "year", "clock.today"]) or any("lai" in x for x in cand):
                header_idx = i
                sep = candidate_sep
                break
        if header_idx is not None:
            break
    if header_idx is None:
        preview = "".join(lines[:20])
        raise ValueError(f"Could not find a valid APSIM table header in output file: {path}\n{preview}")
    df = pd.read_csv(path, sep=sep, engine="python", skiprows=header_idx, comment="!", skip_blank_lines=True)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def get_date_column(df: pd.DataFrame, report: ReportSpec) -> str:
    candidates = [report.date_col, "date", "Date", "clock.today", "Clock.Today", "today", "Today"]
    return _find_column_case_insensitive(df, candidates, "Date")


def get_lai_column(df: pd.DataFrame, report: ReportSpec) -> str:
    candidates = [
        report.lai_col,
        "lai",
        "LAI",
        "wheatlai",
        "WheatLAI",
        "wheat_lai",
        "Wheat_LAI",
        "wheat.lai",
        "Wheat.LAI",
        "crop.lai",
        "Crop.LAI",
    ]
    return _find_column_case_insensitive(df, candidates, "LAI")


def normalize_crop_key(value: object) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    if not s or s in ("nan", "none", "null"):
        return ""
    if "小麦" in s or "wheat" in s:
        return "wheat"
    if "玉米" in s or "maize" in s or "corn" in s:
        return "maize"
    return s


def get_state_column(df: pd.DataFrame, report: ReportSpec) -> Optional[str]:
    candidates = [report.state_col, "currentState", "currentstate", "state", "State"]
    candidates = [c for c in candidates if c]
    try:
        return _find_column_case_insensitive(df, candidates, "state")
    except Exception:
        return None


def get_state_on_date(df: pd.DataFrame, report: ReportSpec, obs_date: pd.Timestamp) -> str:
    state_col = get_state_column(df, report)
    if not state_col:
        return ""
    date_col = get_date_column(df, report)
    tmp = df[[date_col, state_col]].copy()
    tmp[date_col] = normalize_date_column(tmp[date_col], report.date_format)
    tmp = tmp.dropna(subset=[date_col])
    if tmp.empty:
        return ""
    tmp["_date_norm"] = tmp[date_col].dt.normalize()
    d = pd.Timestamp(obs_date).normalize()
    exact = tmp[tmp["_date_norm"] == d]
    if not exact.empty:
        return normalize_crop_key(exact.iloc[-1][state_col])
    past = tmp[tmp["_date_norm"] <= d]
    if not past.empty:
        return normalize_crop_key(past.iloc[-1][state_col])
    idx = (tmp["_date_norm"] - d).abs().idxmin()
    return normalize_crop_key(tmp.loc[idx, state_col])


def _get_crop_map(report: ReportSpec, kind: str) -> Dict[str, str]:
    if kind == "lai":
        m = report.crop_lai_map or {}
    elif kind == "biomass":
        m = report.crop_biomass_map or {}
    elif kind == "yield":
        m = report.crop_yield_map or {}
    else:
        m = {}
    out: Dict[str, str] = {}
    for k, v in m.items():
        kk = normalize_crop_key(k)
        if kk and str(v).strip():
            out[kk] = str(v).strip()
    return out


def find_metric_column_for_crop(
    df: pd.DataFrame, report: ReportSpec, crop_key: str, kind: str, preferred: Optional[str]
) -> Optional[str]:
    crop_map = _get_crop_map(report, kind)
    ckey = normalize_crop_key(crop_key)
    if ckey and ckey in crop_map:
        try:
            return _find_column_case_insensitive(df, [crop_map[ckey]], f"{kind}-{ckey}")
        except Exception:
            pass
    return find_metric_column(df, preferred, kind)


def choose_lai_column_for_obs(
    df: pd.DataFrame, report: ReportSpec, obs_date: pd.Timestamp, obs_crop: Optional[object]
) -> str:
    lai_like = [str(c).strip() for c in df.columns if "lai" in str(c).lower()]
    crop_key = normalize_crop_key(obs_crop)
    if not crop_key:
        crop_key = get_state_on_date(df, report, obs_date)

    crop_map = _get_crop_map(report, "lai")
    if crop_key and crop_key in crop_map:
        try:
            col = _find_column_case_insensitive(df, [crop_map[crop_key]], f"lai-{crop_key}")
            return col
        except Exception:
            pass

    if crop_key:
        heur = [
            f"{crop_key}lai",
            f"{crop_key}_lai",
            f"{crop_key}.lai",
            f"{crop_key}LAI",
            f"{crop_key.upper()}LAI",
        ]
        try:
            return _find_column_case_insensitive(df, heur, f"lai-{crop_key}")
        except Exception:
            pass

    col = get_lai_column(df, report)

    # Guardrail: if we are in maize period but only wheat LAI is available, dual-crop
    # assimilation is not physically valid.
    if crop_key == "maize":
        col_low = col.lower()
        has_maize_lai = any(("maize" in c.lower() or "corn" in c.lower()) and "lai" in c.lower() for c in lai_like)
        generic_ok = any(c.lower() in ("lai", "crop.lai") for c in lai_like)
        if ("wheat" in col_low) and (not has_maize_lai) and (not generic_ok):
            raise KeyError(
                "Maize LAI column not found in APSIM output. Current file only exposes wheat LAI-like columns. "
                "Please add maize.lai (e.g., alias MaizeLAI/maizelai) in the APSIM report variables."
            )
    return col


def extract_lai_for_single_obs(
    df: pd.DataFrame, report: ReportSpec, obs_date: pd.Timestamp, obs_crop: Optional[object]
) -> float:
    col = choose_lai_column_for_obs(df, report, obs_date=obs_date, obs_crop=obs_crop)
    return float(extract_series_on_dates(df, report, col, pd.Series([obs_date]))[0])


def extract_lai_vector(df: pd.DataFrame, report: ReportSpec, obs_df: pd.DataFrame) -> np.ndarray:
    preds: List[float] = []
    for _, r in obs_df.iterrows():
        obs_date = pd.Timestamp(r["date"])
        obs_crop = r["crop"] if "crop" in obs_df.columns else None
        preds.append(extract_lai_for_single_obs(df, report, obs_date, obs_crop))
    return np.array(preds, dtype=float)


def extract_series_on_dates(df: pd.DataFrame, report: ReportSpec, value_col: str, dates: pd.Series) -> np.ndarray:
    date_col = get_date_column(df, report)
    tmp = df.copy()
    tmp[date_col] = normalize_date_column(tmp[date_col], report.date_format)
    tmp = tmp.dropna(subset=[date_col])
    if tmp.empty:
        raise ValueError("No valid dates found in APSIM output.")
    tmp["_date_norm"] = tmp[date_col].dt.normalize()
    obs_dates = pd.to_datetime(dates).dt.normalize()
    preds = np.zeros(len(obs_dates), dtype=float)
    for i, d in enumerate(obs_dates):
        exact = tmp[tmp["_date_norm"] == d]
        if not exact.empty:
            preds[i] = float(exact.iloc[-1][value_col])
            continue
        past = tmp[tmp["_date_norm"] <= d]
        if not past.empty:
            preds[i] = float(past.iloc[-1][value_col])
            continue
        idx = (tmp["_date_norm"] - d).abs().idxmin()
        preds[i] = float(tmp.loc[idx, value_col])
    return preds


def run_single_model(cfg: Config, state_vector: np.ndarray, base_values: Dict[str, float], run_dir: Path) -> pd.DataFrame:
    actual_dir = run_dir
    if actual_dir.exists():
        actual_dir = run_dir.parent / f"{run_dir.name}_{random.randint(1000, 999999)}"
    actual_dir.mkdir(parents=True, exist_ok=True)
    template_apsim = os.path.abspath(cfg.template_apsim)
    apsim_exe = os.path.abspath(cfg.apsim_exe)
    run_model_path = actual_dir / Path(template_apsim).name
    apply_parameter_values(template_apsim, str(run_model_path), cfg.parameters, base_values, state_vector)
    code, stdout, stderr = run_apsim_classic(apsim_exe, str(run_model_path), cwd=str(actual_dir))
    if code != 0:
        raise RuntimeError(f"APSIM run failed in {actual_dir}\nstdout:\n{stdout}\n\nstderr:\n{stderr}")
    out_file = find_output_file(str(actual_dir), cfg.report.output_pattern)
    return read_apsim_output_table(str(out_file))


def load_truth_observations(path: str, default_std: float) -> pd.DataFrame:
    df = pd.read_csv(path)
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    date_col = cols.get("date")
    lai_col = cols.get("lai") or cols.get("lai_obs") or cols.get("laiobs")
    std_col = cols.get("std") or cols.get("lai_std") or cols.get("obs_std")
    if date_col is None or lai_col is None:
        raise KeyError(f"Truth CSV must contain date and lai columns. Existing columns: {list(df.columns)}")
    out = pd.DataFrame()
    out["date"] = normalize_date_column(df[date_col], None)
    out["lai"] = pd.to_numeric(df[lai_col], errors="coerce")
    out["std"] = pd.to_numeric(df[std_col], errors="coerce") if std_col else np.nan
    out = out.dropna(subset=["date", "lai"]).sort_values("date").reset_index(drop=True)
    out["std"] = out["std"].fillna(default_std).clip(lower=0.03)
    out["source"] = "truth"
    return out


def load_satellite_observations(spec: SatelliteCorrectionSpec) -> pd.DataFrame:
    df = pd.read_csv(spec.satellite_csv)
    date_col = _find_column_case_insensitive(df, [spec.satellite_date_col, "date", "Date"], "satellite date")
    lai_col = _find_column_case_insensitive(df, [spec.satellite_lai_col, "lai", "LAI"], "satellite lai")
    std_col = None
    if spec.satellite_std_col:
        try:
            std_col = _find_column_case_insensitive(df, [spec.satellite_std_col], "satellite std")
        except Exception:
            std_col = None
    out = pd.DataFrame()
    out["date"] = normalize_date_column(df[date_col], None)
    out["lai"] = pd.to_numeric(df[lai_col], errors="coerce")
    out["std"] = pd.to_numeric(df[std_col], errors="coerce") if std_col else np.nan
    out = out.dropna(subset=["date", "lai"]).sort_values("date").reset_index(drop=True)
    out["std"] = out["std"].fillna(spec.default_std).clip(lower=0.03)
    out["source"] = "satellite_raw"
    return out


def calibrate_satellite_with_truth(
    truth_df: pd.DataFrame, sat_df: pd.DataFrame, spec: SatelliteCorrectionSpec
) -> Tuple[pd.DataFrame, pd.DataFrame, Dict[str, float]]:
    min_positive_slope = 0.05
    truth_anchor_std_cap = max(0.05, 0.7 * spec.default_std)

    truth_tmp = truth_df[["date", "lai"]].rename(columns={"lai": "lai_truth"}).sort_values("date")
    sat_tmp = sat_df[["date", "lai"]].rename(columns={"lai": "lai_sat"}).sort_values("date")
    tol = pd.Timedelta(days=max(0, spec.join_tolerance_days))
    pairs = pd.merge_asof(truth_tmp, sat_tmp, on="date", direction="nearest", tolerance=tol)
    pairs = pairs.dropna(subset=["lai_truth", "lai_sat"])

    x = pairs["lai_sat"].values.astype(float) if len(pairs) > 0 else np.array([], dtype=float)
    y = pairs["lai_truth"].values.astype(float) if len(pairs) > 0 else np.array([], dtype=float)
    x_mean = float(np.mean(x)) if len(x) > 0 else 0.0
    y_mean = float(np.mean(y)) if len(y) > 0 else 0.0

    if len(pairs) >= 2 and np.std(pairs["lai_sat"], ddof=0) > 1e-8:
        if spec.method == "ratio":
            denom = float(np.dot(x, x))
            slope = float(np.dot(x, y) / denom) if denom > 0 else 1.0
            intercept = 0.0
        else:
            var_x = float(np.mean((x - x_mean) ** 2))
            if var_x <= 1e-12:
                slope = 1.0
                intercept = y_mean - slope * x_mean
            else:
                cov_xy = float(np.mean((x - x_mean) * (y - y_mean)))
                slope = cov_xy / var_x
                intercept = y_mean - slope * x_mean
    elif len(pairs) == 1 and abs(float(pairs.iloc[0]["lai_sat"])) > 1e-8:
        slope = float(pairs.iloc[0]["lai_truth"] / pairs.iloc[0]["lai_sat"])
        intercept = 0.0
    else:
        slope, intercept = 1.0, 0.0

    slope_was_clipped = bool(slope < min_positive_slope)
    if slope_was_clipped:
        slope = min_positive_slope
        if spec.method == "ratio":
            intercept = 0.0
        else:
            intercept = y_mean - slope * x_mean

    corrected = sat_df.copy()
    corrected["lai_raw"] = corrected["lai"]
    corrected["lai"] = (intercept + slope * corrected["lai_raw"]).clip(lower=spec.min_lai, upper=spec.max_lai)
    corrected["source"] = "satellite_corrected"
    if len(pairs) > 0:
        residuals = pairs["lai_truth"] - (intercept + slope * pairs["lai_sat"])
        fit_std = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else spec.default_std
        fit_std = max(fit_std, 0.05)
    else:
        fit_std = max(spec.default_std, 0.05)
    sat_std = pd.to_numeric(corrected["std"], errors="coerce").fillna(spec.default_std)
    corrected["std"] = np.maximum(sat_std, fit_std).clip(lower=0.03)

    if spec.anchor_truth_points:
        truth_anchor = truth_df[["date", "lai", "std"]].copy()
        truth_std = pd.to_numeric(truth_anchor["std"], errors="coerce").fillna(spec.default_std)
        truth_anchor["std"] = truth_std.clip(lower=0.03, upper=truth_anchor_std_cap)
        truth_anchor["source"] = "truth_anchor"
        truth_anchor["lai_raw"] = np.nan
        truth_dates = set(truth_anchor["date"].dt.normalize())
        corrected = corrected[~corrected["date"].dt.normalize().isin(truth_dates)]
        corrected = pd.concat([corrected, truth_anchor], ignore_index=True)
    corrected = corrected.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    cal_diag = {
        "n_truth_points": float(len(truth_df)),
        "n_pairs_for_fit": float(len(pairs)),
        "fit_slope": float(slope),
        "fit_intercept": float(intercept),
        "fit_residual_std": float(fit_std),
        "slope_was_clipped_to_positive": slope_was_clipped,
        "min_positive_slope": float(min_positive_slope),
        "truth_anchor_std_cap": float(truth_anchor_std_cap),
    }
    return corrected, pairs, cal_diag


def _match_first_column(df: pd.DataFrame, patterns: Sequence[str]) -> Optional[str]:
    cols = [str(c).strip() for c in df.columns]
    for p in patterns:
        for c in cols:
            if p in c:
                return c
    return None


def _read_excel_table_with_header(path: str, sheet_name: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=sheet_name, header=None)
    header_idx = None
    for i in range(len(raw)):
        vals = [str(v).strip() for v in raw.iloc[i].tolist()]
        if "日期" in vals and (
            any("总生物量" in x for x in vals)
            or any("产量(kg/ha)" in x for x in vals)
            or any("产量" in x for x in vals)
        ):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"Failed to locate header row in sheet: {sheet_name}")

    cols = [str(v).strip() for v in raw.iloc[header_idx].tolist()]
    data = raw.iloc[header_idx + 1 :].copy()
    data.columns = cols
    data = data.dropna(how="all").reset_index(drop=True)
    data.columns = [str(c).strip() for c in data.columns]
    return data


def load_truth_validation_from_excel(spec: TruthValidationSpec) -> pd.DataFrame:
    if not spec.excel_path:
        raise ValueError("truth_validation.excel_path is empty.")
    if not os.path.isfile(spec.excel_path):
        raise FileNotFoundError(f"Truth Excel not found: {spec.excel_path}")

    xls = pd.ExcelFile(spec.excel_path)
    candidates: List[str] = []
    if spec.sheet_name and spec.sheet_name in xls.sheet_names:
        candidates.append(spec.sheet_name)
    if "汇总" in xls.sheet_names and "汇总" not in candidates:
        candidates.append("汇总")
    candidates.extend([s for s in xls.sheet_names if s not in candidates])

    last_err = None
    table = None
    for s in candidates:
        try:
            t = _read_excel_table_with_header(spec.excel_path, s)
            if _match_first_column(t, ["日期"]) is not None and (
                _match_first_column(t, ["总生物量"]) is not None or _match_first_column(t, ["产量(kg/ha)"]) is not None
            ):
                table = t
                break
        except Exception as e:
            last_err = e
            continue
    if table is None:
        raise RuntimeError(f"Failed to parse truth table from Excel. Last error: {last_err}")

    date_col = _match_first_column(table, ["日期"])
    crop_col = _match_first_column(table, ["作物"])
    plot_col = _match_first_column(table, ["区号"])
    stage_col = _match_first_column(table, ["生育期"])
    biomass_col = _match_first_column(table, ["总生物量"])
    yield_col = _match_first_column(table, ["产量(kg/ha)", "产量"])

    if date_col is None:
        raise KeyError("Cannot find 日期 column in truth Excel.")

    out = pd.DataFrame()
    out["date"] = normalize_date_column(table[date_col], None)
    out["crop"] = table[crop_col].astype(str).str.strip() if crop_col else ""
    out["plot_id"] = table[plot_col].astype(str).str.strip() if plot_col else ""
    out["stage"] = table[stage_col].astype(str).str.strip() if stage_col else ""
    out["biomass_truth"] = pd.to_numeric(table[biomass_col], errors="coerce") if biomass_col else np.nan
    out["yield_truth"] = pd.to_numeric(table[yield_col], errors="coerce") if yield_col else np.nan
    out = out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    if spec.crop_name:
        out = out[out["crop"].astype(str) == str(spec.crop_name)].reset_index(drop=True)
    if spec.plot_id:
        out = out[out["plot_id"].astype(str) == str(spec.plot_id)].reset_index(drop=True)
    return out


def summarize_matchups(obs_col: str, pred_col: str, match: pd.DataFrame, prefix: str) -> Dict[str, float]:
    if match.empty:
        return {
            f"{prefix}_n": 0.0,
            f"{prefix}_rmse": np.nan,
            f"{prefix}_mae": np.nan,
            f"{prefix}_bias": np.nan,
        }
    err = (match[pred_col] - match[obs_col]).values.astype(float)
    return {
        f"{prefix}_n": float(len(match)),
        f"{prefix}_rmse": float(np.sqrt(np.mean(err**2))),
        f"{prefix}_mae": float(np.mean(np.abs(err))),
        f"{prefix}_bias": float(np.mean(err)),
    }


def get_model_series(df_out: pd.DataFrame, report: ReportSpec, value_col: str) -> pd.DataFrame:
    date_col = get_date_column(df_out, report)
    tmp = df_out[[date_col, value_col]].copy()
    tmp[date_col] = normalize_date_column(tmp[date_col], report.date_format)
    tmp[value_col] = pd.to_numeric(tmp[value_col], errors="coerce")
    tmp = tmp.dropna(subset=[date_col, value_col]).sort_values(date_col)
    if tmp.empty:
        return pd.DataFrame(columns=["date", "value"])
    tmp["date"] = tmp[date_col].dt.normalize()
    # APSIM output may contain duplicated dates; keep the last record of each day.
    tmp = tmp.groupby("date", as_index=False)[value_col].last()
    return tmp.rename(columns={value_col: "value"})


def evaluate_biomass_and_yield(
    df_out: pd.DataFrame,
    cfg: Config,
    truth_df: pd.DataFrame,
    method_root: Path,
) -> Dict[str, float]:
    result: Dict[str, float] = {}
    biomass_col = find_metric_column(df_out, cfg.report.biomass_col, "biomass")
    yield_col = find_metric_column(df_out, cfg.report.yield_col, "yield")

    bio_truth = truth_df.dropna(subset=["biomass_truth"]).copy()
    if biomass_col and not bio_truth.empty:
        bio_truth["biomass_pred"] = extract_series_on_dates(df_out, cfg.report, biomass_col, bio_truth["date"])
        bio_truth["error"] = bio_truth["biomass_pred"] - bio_truth["biomass_truth"]
        bio_truth.to_csv(method_root / "biomass_truth_matchups.csv", index=False, encoding="utf-8-sig")
        result.update(summarize_matchups("biomass_truth", "biomass_pred", bio_truth, "biomass_truth"))
    else:
        result.update({"biomass_truth_n": 0.0, "biomass_truth_rmse": np.nan, "biomass_truth_mae": np.nan, "biomass_truth_bias": np.nan})

    y_truth = truth_df.dropna(subset=["yield_truth"]).copy()
    if yield_col and not y_truth.empty:
        y_truth["yield_pred"] = extract_series_on_dates(df_out, cfg.report, yield_col, y_truth["date"])
        y_truth["error"] = y_truth["yield_pred"] - y_truth["yield_truth"]
        y_truth.to_csv(method_root / "yield_truth_matchups.csv", index=False, encoding="utf-8-sig")
        # Most cases only have harvest yield; use latest truth row for final comparison.
        y_last = y_truth.sort_values("date").iloc[-1]
        result["yield_truth_value"] = float(y_last["yield_truth"])
        result["yield_pred_at_truth_date"] = float(y_last["yield_pred"])
        result["yield_truth_abs_error"] = float(abs(y_last["error"]))
    else:
        result["yield_truth_value"] = np.nan
        result["yield_pred_at_truth_date"] = np.nan
        result["yield_truth_abs_error"] = np.nan

    return result


def generate_truth_validation_plots(
    workspace: Path,
    cfg: Config,
    method_outputs: Dict[str, Dict[str, object]],
    truth_df: pd.DataFrame,
) -> None:
    bio_truth = truth_df.dropna(subset=["biomass_truth"]).copy()
    y_truth = truth_df.dropna(subset=["yield_truth"]).copy()

    width, height = 1600, 1000
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # Layout
    top = (80, 70, width - 60, 620)       # biomass panel: x0,y0,x1,y1
    bottom = (80, 690, width - 60, 960)   # yield panel

    def draw_axes(rect, title, y_label):
        x0, y0, x1, y1 = rect
        draw.rectangle(rect, outline=(120, 120, 120), width=1)
        draw.text((x0, y0 - 24), title, fill=(20, 20, 20), font=font)
        draw.text((x0 + 4, y0 + 4), y_label, fill=(70, 70, 70), font=font)
        return x0 + 60, y0 + 20, x1 - 20, y1 - 40  # plotting area

    p1 = draw_axes(top, "Biomass Time Series: Methods vs Truth", "Biomass (kg/ha)")
    p2 = draw_axes(bottom, "Final Yield Comparison", "Yield (kg/ha)")

    series_by_method: Dict[str, pd.DataFrame] = {}
    all_dates: List[pd.Timestamp] = []
    all_bio_values: List[float] = []
    colors = [
        (78, 121, 167),
        (242, 142, 43),
        (225, 87, 89),
        (118, 183, 178),
        (89, 161, 79),
    ]

    for method, out in method_outputs.items():
        if out.get("status") != "ok" or "_df_final" not in out:
            continue
        df_final = out["_df_final"]  # type: ignore[assignment]
        biomass_col = find_metric_column(df_final, cfg.report.biomass_col, "biomass")
        if not biomass_col:
            continue
        series = get_model_series(df_final, cfg.report, biomass_col)
        if series.empty:
            continue
        series_by_method[method] = series
        all_dates.extend(list(series["date"]))
        all_bio_values.extend(list(series["value"].astype(float)))

    if not bio_truth.empty:
        all_dates.extend(list(pd.to_datetime(bio_truth["date"]).dt.normalize()))
        all_bio_values.extend(list(pd.to_numeric(bio_truth["biomass_truth"], errors="coerce").dropna()))

    if all_dates and all_bio_values:
        dmin = min(all_dates)
        dmax = max(all_dates)
        if dmin == dmax:
            dmax = dmin + pd.Timedelta(days=1)
        vmin = 0.0
        vmax = max(all_bio_values) * 1.08
        vmax = max(vmax, 1.0)

        x0, y0, x1, y1 = p1

        def map_x(d: pd.Timestamp) -> int:
            t = (d - dmin).total_seconds() / max((dmax - dmin).total_seconds(), 1.0)
            return int(x0 + t * (x1 - x0))

        def map_y(v: float) -> int:
            t = (v - vmin) / max(vmax - vmin, 1e-9)
            return int(y1 - t * (y1 - y0))

        for i, (method, series) in enumerate(series_by_method.items()):
            pts = []
            for _, r in series.iterrows():
                d = pd.Timestamp(r["date"]).normalize()
                v = float(r["value"])
                pts.append((map_x(d), map_y(v)))
            if len(pts) >= 2:
                draw.line(pts, fill=colors[i % len(colors)], width=2)
            # legend
            lx = x0 + 12
            ly = y0 + 14 + 16 * i
            draw.line([(lx, ly + 6), (lx + 18, ly + 6)], fill=colors[i % len(colors)], width=2)
            draw.text((lx + 24, ly), method, fill=(35, 35, 35), font=font)

        if not bio_truth.empty:
            for _, r in bio_truth.iterrows():
                d = pd.Timestamp(r["date"]).normalize()
                v = float(r["biomass_truth"])
                cx, cy = map_x(d), map_y(v)
                draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(0, 0, 0), outline=(0, 0, 0))
            draw.text((x1 - 180, y0 + 10), "black dots: truth", fill=(20, 20, 20), font=font)

        # x-axis labels
        for frac in (0.0, 0.5, 1.0):
            xd = dmin + (dmax - dmin) * frac
            xx = int(x0 + frac * (x1 - x0))
            draw.line([(xx, y1), (xx, y1 + 4)], fill=(120, 120, 120), width=1)
            draw.text((xx - 30, y1 + 8), xd.strftime("%Y-%m-%d"), fill=(60, 60, 60), font=font)

    # Yield panel
    method_names: List[str] = []
    pred_values: List[float] = []
    for method, out in method_outputs.items():
        if out.get("status") != "ok":
            continue
        val = out.get("yield_pred_at_truth_date", np.nan)
        if isinstance(val, (int, float)) and np.isfinite(val):
            method_names.append(method)
            pred_values.append(float(val))

    x0, y0, x1, y1 = p2
    if method_names:
        y_truth_value = np.nan
        if not y_truth.empty:
            y_truth_value = float(y_truth.sort_values("date").iloc[-1]["yield_truth"])
        ymax = max(pred_values + ([y_truth_value] if np.isfinite(y_truth_value) else []))
        ymax = max(ymax * 1.12, 1.0)

        n = len(method_names)
        for i, (name, val) in enumerate(zip(method_names, pred_values)):
            cx = x0 + int((i + 0.5) * (x1 - x0) / n)
            ty = int(y1 - (val / ymax) * (y1 - y0))
            draw.line([(cx, y1), (cx, ty)], fill=(78, 121, 167), width=4)
            draw.ellipse((cx - 4, ty - 4, cx + 4, ty + 4), fill=(78, 121, 167))
            draw.text((cx - 30, y1 + 8), name, fill=(40, 40, 40), font=font)
            draw.text((cx - 20, ty - 16), f"{val:.0f}", fill=(40, 40, 40), font=font)

        if np.isfinite(y_truth_value):
            ty = int(y1 - (y_truth_value / ymax) * (y1 - y0))
            draw.line([(x0, ty), (x1, ty)], fill=(225, 87, 89), width=2)
            draw.text((x1 - 190, ty - 14), f"truth={y_truth_value:.0f}", fill=(225, 87, 89), font=font)

    img.save(workspace / "truth_validation_biomass_timeseries_and_yield.png", format="PNG")

def find_metric_column(df: pd.DataFrame, preferred: Optional[str], kind: str) -> Optional[str]:
    if kind == "yield":
        defaults = [
            "yield",
            "Yield",
            "WheatYield",
            "MaizeYield",
            "wheat.yield",
            "Wheat.Yield",
            "maize.yield",
            "Maize.Yield",
            "grain_yield",
            "grainyield",
            "grain_wt",
            "grainwt",
        ]
    elif kind == "biomass":
        defaults = [
            "biomass",
            "Biomass",
            "WheatBio",
            "MaizeBio",
            "wheat.biomass",
            "Wheat.Biomass",
            "maize.biomass",
            "Maize.Biomass",
            "agb",
            "aboveground",
            "topwt",
            "dm_green",
        ]
    else:
        defaults = []
    candidates = [preferred] if preferred else []
    candidates.extend(defaults)
    try:
        return _find_column_case_insensitive(df, [c for c in candidates if c], kind)
    except Exception:
        return None


def extract_last_and_peak(df: pd.DataFrame, col: Optional[str]) -> Tuple[float, float]:
    if not col:
        return np.nan, np.nan
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return np.nan, np.nan
    return float(s.iloc[-1]), float(s.max())


def summarize_terminal_metrics(df_out: pd.DataFrame, report: ReportSpec) -> Dict[str, float]:
    yield_col = find_metric_column(df_out, report.yield_col, "yield")
    biomass_col = find_metric_column(df_out, report.biomass_col, "biomass")
    y_last, y_peak = extract_last_and_peak(df_out, yield_col)
    b_last, b_peak = extract_last_and_peak(df_out, biomass_col)
    return {"yield_last": y_last, "yield_peak": y_peak, "biomass_last": b_last, "biomass_peak": b_peak}


def clip_state_vector(x: np.ndarray, parameters: Sequence[ParameterSpec]) -> np.ndarray:
    y = np.array(x, dtype=float).copy()
    for j, p in enumerate(parameters):
        y[j] = np.clip(y[j], p.min_value, p.max_value)
    return y


def clip_ensemble(X: np.ndarray, parameters: Sequence[ParameterSpec]) -> np.ndarray:
    Y = X.copy()
    for j, p in enumerate(parameters):
        Y[:, j] = np.clip(Y[:, j], p.min_value, p.max_value)
    return Y


def sample_initial_ensemble(parameters: Sequence[ParameterSpec], n_ensemble: int, rng: np.random.Generator) -> np.ndarray:
    X = np.zeros((n_ensemble, len(parameters)), dtype=float)
    for j, p in enumerate(parameters):
        X[:, j] = rng.normal(p.prior_mean, p.prior_std, size=n_ensemble)
        X[:, j] = np.clip(X[:, j], p.min_value, p.max_value)
    return X


def enkf_update_scalar_obs(
    Xf: np.ndarray,
    yf: np.ndarray,
    y_obs: float,
    obs_std: float,
    inflation: float,
    parameters: Sequence[ParameterSpec],
    process_noise_frac: float,
    rng: np.random.Generator,
) -> np.ndarray:
    Xf = Xf.copy()
    n, _ = Xf.shape
    if n < 2:
        raise ValueError("Ensemble size must be at least 2")
    xm = Xf.mean(axis=0, keepdims=True)
    Xf = xm + inflation * (Xf - xm)
    yfm = float(np.mean(yf))
    A = Xf - Xf.mean(axis=0, keepdims=True)
    dy = (yf - yfm).reshape(-1, 1)
    Pxy = (A.T @ dy) / (n - 1)
    Pyy = float(((dy.T @ dy) / (n - 1)).item()) + obs_std**2
    K = (Pxy / Pyy).reshape(-1)

    Xa = np.zeros_like(Xf)
    for i in range(n):
        y_pert = y_obs + rng.normal(0.0, obs_std)
        Xa[i, :] = Xf[i, :] + K * (y_pert - yf[i])
    spread = Xa.std(axis=0, ddof=1)
    noise = rng.normal(0.0, 1.0, size=Xa.shape) * (process_noise_frac * np.maximum(spread, 1e-12))
    Xa = Xa + noise
    return clip_ensemble(Xa, parameters)


def build_lai_matchups(df_out: pd.DataFrame, report: ReportSpec, obs_df: pd.DataFrame) -> pd.DataFrame:
    y_pred = extract_lai_vector(df_out, report, obs_df)
    match = pd.DataFrame()
    match["date"] = pd.to_datetime(obs_df["date"]).dt.strftime("%Y-%m-%d")
    match["lai_obs"] = obs_df["lai"].astype(float).values
    match["lai_pred"] = y_pred
    match["error"] = match["lai_pred"] - match["lai_obs"]
    match["abs_error"] = match["error"].abs()
    return match


def summarize_lai_matchups(match: pd.DataFrame) -> Dict[str, float]:
    if match.empty:
        return {"lai_rmse": np.nan, "lai_mae": np.nan, "lai_bias": np.nan}
    err = match["error"].values.astype(float)
    return {
        "lai_rmse": float(np.sqrt(np.mean(err**2))),
        "lai_mae": float(np.mean(np.abs(err))),
        "lai_bias": float(np.mean(err)),
    }


def run_open_loop_method(cfg: Config, base_values: Dict[str, float], obs_df: pd.DataFrame, run_root: Path) -> Dict[str, object]:
    x0 = np.array([p.prior_mean for p in cfg.parameters], dtype=float)
    df_out = run_single_model(cfg, x0, base_values, run_root / "open_loop_run")
    match = build_lai_matchups(df_out, cfg.report, obs_df)
    match.to_csv(run_root / "lai_matchups.csv", index=False, encoding="utf-8-sig")

    summary = summarize_lai_matchups(match)
    summary.update(summarize_terminal_metrics(df_out, cfg.report))
    summary["method"] = "open_loop"
    summary["status"] = "ok"
    summary["final_state"] = json.dumps({p.name: float(x0[i]) for i, p in enumerate(cfg.parameters)}, ensure_ascii=False)
    summary["_df_final"] = df_out
    return summary


def run_enkf_method(cfg: Config, base_values: Dict[str, float], obs_df: pd.DataFrame, run_root: Path) -> Dict[str, object]:
    rng = np.random.default_rng(cfg.enkf.random_seed)
    random.seed(cfg.enkf.random_seed)
    X = sample_initial_ensemble(cfg.parameters, cfg.enkf.n_ensemble, rng)
    hist_rows = []

    for k, row in obs_df.iterrows():
        obs_date = pd.Timestamp(row["date"])
        y_obs = float(row["lai"])
        obs_std = max(float(row["std"]), 0.03)
        obs_crop = row["crop"] if "crop" in obs_df.columns else None
        y_pred = np.zeros(cfg.enkf.n_ensemble, dtype=float)

        for i in range(cfg.enkf.n_ensemble):
            ens_dir = run_root / f"step_{k:03d}" / f"ens_{i:03d}"
            df_out = run_single_model(cfg, X[i, :], base_values, ens_dir)
            y_pred[i] = extract_lai_for_single_obs(df_out, cfg.report, obs_date, obs_crop)

        prior_mean = float(np.mean(y_pred))
        prior_std = float(np.std(y_pred, ddof=1)) if len(y_pred) > 1 else 0.0
        X = enkf_update_scalar_obs(
            Xf=X,
            yf=y_pred,
            y_obs=y_obs,
            obs_std=obs_std,
            inflation=cfg.enkf.inflation,
            parameters=cfg.parameters,
            process_noise_frac=cfg.enkf.process_noise_frac,
            rng=rng,
        )

        row_out = {
            "step": int(k),
            "date": obs_date.strftime("%Y-%m-%d"),
            "lai_obs": y_obs,
            "lai_prior_mean": prior_mean,
            "lai_prior_std": prior_std,
        }
        for j, p in enumerate(cfg.parameters):
            row_out[f"post_{p.name}_mean"] = float(X[:, j].mean())
            row_out[f"post_{p.name}_std"] = float(X[:, j].std(ddof=1)) if cfg.enkf.n_ensemble > 1 else 0.0
        hist_rows.append(row_out)

    hist = pd.DataFrame(hist_rows)
    hist.to_csv(run_root / "assimilation_history.csv", index=False, encoding="utf-8-sig")
    final_state = X.mean(axis=0)
    df_final = run_single_model(cfg, final_state, base_values, run_root / "final_posterior_mean")
    match = build_lai_matchups(df_final, cfg.report, obs_df)
    match.to_csv(run_root / "lai_matchups.csv", index=False, encoding="utf-8-sig")

    summary = summarize_lai_matchups(match)
    summary.update(summarize_terminal_metrics(df_final, cfg.report))
    summary["method"] = "enkf"
    summary["status"] = "ok"
    summary["final_state"] = json.dumps({p.name: float(final_state[i]) for i, p in enumerate(cfg.parameters)}, ensure_ascii=False)
    summary["_posterior_mean"] = final_state
    summary["_posterior_std"] = np.std(X, axis=0, ddof=1) if cfg.enkf.n_ensemble > 1 else np.full_like(final_state, 0.05)
    summary["_df_final"] = df_final
    return summary


def coordinate_search(
    x0: np.ndarray,
    lower: np.ndarray,
    upper: np.ndarray,
    cfg_var: Var4DSpec,
    objective_fn,
) -> Tuple[np.ndarray, float, pd.DataFrame]:
    x = np.clip(x0.copy(), lower, upper)
    param_range = np.maximum(upper - lower, 1e-6)
    step = cfg_var.init_step_frac * param_range
    step_floor = cfg_var.min_step_frac * param_range
    fx = float(objective_fn(x))
    trace = [{"iter": 0, "objective": fx, "step_mean": float(np.mean(step))}]

    for it in range(1, cfg_var.max_iter + 1):
        improved = False
        for j in range(len(x)):
            for sign in (1.0, -1.0):
                cand = x.copy()
                cand[j] = np.clip(cand[j] + sign * step[j], lower[j], upper[j])
                if np.isclose(cand[j], x[j]):
                    continue
                fc = float(objective_fn(cand))
                if fc + cfg_var.improvement_tol < fx:
                    x = cand
                    fx = fc
                    improved = True
        if not improved:
            step = step * cfg_var.step_shrink
            if np.all(step <= step_floor):
                trace.append({"iter": it, "objective": fx, "step_mean": float(np.mean(step))})
                break
        trace.append({"iter": it, "objective": fx, "step_mean": float(np.mean(step))})

    return x, fx, pd.DataFrame(trace)


def run_4dvar_method(
    cfg: Config,
    base_values: Dict[str, float],
    obs_df: pd.DataFrame,
    run_root: Path,
    init_state: np.ndarray,
    prior_mean: np.ndarray,
    prior_std: np.ndarray,
    method_name: str,
) -> Dict[str, object]:
    lower = np.array([p.min_value for p in cfg.parameters], dtype=float)
    upper = np.array([p.max_value for p in cfg.parameters], dtype=float)
    prior_std = np.maximum(prior_std, 1e-3)
    rng = np.random.default_rng(cfg.var4d.random_seed)

    lai_obs = obs_df["lai"].values.astype(float)
    lai_std = np.maximum(obs_df["std"].values.astype(float), 0.03)
    eval_dir = run_root / "var_evals"
    eval_counter = {"idx": 0}
    eval_rows = []

    def objective(x: np.ndarray) -> float:
        idx = eval_counter["idx"]
        eval_counter["idx"] += 1
        this_dir = eval_dir / f"eval_{idx:05d}"
        df_out = run_single_model(cfg, x, base_values, this_dir)
        pred = extract_lai_vector(df_out, cfg.report, obs_df)
        innov = (pred - lai_obs) / lai_std
        j_obs = float(np.dot(innov, innov))
        dz = (x - prior_mean) / prior_std
        j_prior = float(cfg.var4d.prior_weight * np.dot(dz, dz))
        j = j_obs + j_prior
        eval_rows.append(
            {
                "eval_id": idx,
                "j_total": j,
                "j_obs": j_obs,
                "j_prior": j_prior,
                "state_json": json.dumps({p.name: float(x[i]) for i, p in enumerate(cfg.parameters)}, ensure_ascii=False),
            }
        )
        return j

    starts = [clip_state_vector(init_state, cfg.parameters)]
    for _ in range(max(0, cfg.var4d.multistart - 1)):
        perturb = rng.normal(0.0, 0.5, size=len(init_state)) * prior_std
        starts.append(clip_state_vector(init_state + perturb, cfg.parameters))

    best_x: Optional[np.ndarray] = None
    best_j = np.inf
    traces = []
    for s_idx, start in enumerate(starts):
        x_opt, j_opt, trace = coordinate_search(start, lower, upper, cfg.var4d, objective)
        trace["start_idx"] = s_idx
        traces.append(trace)
        if j_opt < best_j:
            best_j = j_opt
            best_x = x_opt
    if best_x is None:
        raise RuntimeError("4DVar failed to produce a valid solution.")

    pd.concat(traces, ignore_index=True).to_csv(run_root / "search_trace.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(eval_rows).to_csv(run_root / "objective_evaluations.csv", index=False, encoding="utf-8-sig")

    df_final = run_single_model(cfg, best_x, base_values, run_root / "final_state_run")
    match = build_lai_matchups(df_final, cfg.report, obs_df)
    match.to_csv(run_root / "lai_matchups.csv", index=False, encoding="utf-8-sig")

    summary = summarize_lai_matchups(match)
    summary.update(summarize_terminal_metrics(df_final, cfg.report))
    summary["method"] = method_name
    summary["status"] = "ok"
    summary["j_best"] = float(best_j)
    summary["final_state"] = json.dumps({p.name: float(best_x[i]) for i, p in enumerate(cfg.parameters)}, ensure_ascii=False)
    summary["_df_final"] = df_final
    return summary


def prepare_assimilation_observations(cfg: Config, workspace: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    truth = load_truth_observations(cfg.satellite_correction.truth_csv, cfg.satellite_correction.default_std)
    sat = load_satellite_observations(cfg.satellite_correction)
    corrected, pairs, cal_diag = calibrate_satellite_with_truth(truth, sat, cfg.satellite_correction)

    truth.to_csv(workspace / "truth_observations_loaded.csv", index=False, encoding="utf-8-sig")
    sat.to_csv(workspace / "satellite_observations_loaded.csv", index=False, encoding="utf-8-sig")
    corrected.to_csv(workspace / "corrected_satellite_observations.csv", index=False, encoding="utf-8-sig")
    pairs.to_csv(workspace / "truth_satellite_pairs_used_for_calibration.csv", index=False, encoding="utf-8-sig")
    with open(workspace / "satellite_calibration_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(cal_diag, f, ensure_ascii=False, indent=2)
    return truth, corrected


def select_obs_for_assimilation(cfg: Config, truth: pd.DataFrame, corrected: pd.DataFrame) -> pd.DataFrame:
    source = cfg.experiment.obs_source
    if source == "truth":
        obs = truth.copy()
    elif source in ("corrected_satellite", "satellite_corrected", "corrected"):
        obs = corrected.copy()
    else:
        raise ValueError("experiment.obs_source must be 'truth' or 'corrected_satellite'")
    obs = obs.sort_values("date").reset_index(drop=True)
    if cfg.experiment.max_obs is not None:
        obs = obs.iloc[: int(cfg.experiment.max_obs)].reset_index(drop=True)
    return obs[["date", "lai", "std", "source"]].copy()


def infer_model_date_window(
    cfg: Config, base_values: Dict[str, float], workspace: Path
) -> Tuple[pd.Timestamp, pd.Timestamp]:
    probe_state = np.array([p.prior_mean for p in cfg.parameters], dtype=float)
    probe_df = run_single_model(cfg, probe_state, base_values, workspace / "_simulation_window_probe")
    date_col = get_date_column(probe_df, cfg.report)
    dates = normalize_date_column(probe_df[date_col], cfg.report.date_format).dropna().dt.normalize()
    if dates.empty:
        raise RuntimeError("Could not infer simulation date window from APSIM output.")
    return pd.Timestamp(dates.min()), pd.Timestamp(dates.max())


def filter_obs_to_window(obs_df: pd.DataFrame, date_start: pd.Timestamp, date_end: pd.Timestamp) -> pd.DataFrame:
    obs = obs_df.copy()
    obs["date"] = pd.to_datetime(obs["date"], errors="coerce").dt.normalize()
    obs = obs.dropna(subset=["date"])
    mask = (obs["date"] >= pd.Timestamp(date_start).normalize()) & (obs["date"] <= pd.Timestamp(date_end).normalize())
    return obs.loc[mask].sort_values("date").reset_index(drop=True)


def standardize_method_name(name: str) -> str:
    n = str(name).lower().strip()
    mapping = {
        "openloop": "open_loop",
        "open_loop": "open_loop",
        "enkf": "enkf",
        "4dvar": "4dvar",
        "var4d": "4dvar",
        "enkf+4dvar": "enkf_4dvar",
        "enkf_4dvar": "enkf_4dvar",
        "hybrid": "enkf_4dvar",
    }
    if n not in mapping:
        raise ValueError(f"Unsupported method: {name}")
    return mapping[n]


def run_comparison(config_path: str) -> pd.DataFrame:
    cfg = load_config(config_path)
    template_apsim = os.path.abspath(cfg.template_apsim)
    apsim_exe = os.path.abspath(cfg.apsim_exe)
    workspace = Path(cfg.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    if not os.path.isfile(template_apsim):
        raise FileNotFoundError(f"Template APSIM file not found: {template_apsim}")
    if not os.path.isfile(apsim_exe):
        raise FileNotFoundError(f"APSIM executable not found: {apsim_exe}")
    if not os.path.isfile(cfg.satellite_correction.truth_csv):
        raise FileNotFoundError(f"Truth CSV not found: {cfg.satellite_correction.truth_csv}")
    if not os.path.isfile(cfg.satellite_correction.satellite_csv):
        raise FileNotFoundError(f"Satellite CSV not found: {cfg.satellite_correction.satellite_csv}")

    base_values = read_base_parameter_values(template_apsim, cfg.parameters)
    sim_start, sim_end = infer_model_date_window(cfg, base_values, workspace)

    truth, corrected = prepare_assimilation_observations(cfg, workspace)
    obs_df_all = select_obs_for_assimilation(cfg, truth, corrected)
    obs_df = filter_obs_to_window(obs_df_all, sim_start, sim_end)
    if obs_df.empty:
        raise RuntimeError(
            f"No assimilation observations remain after filtering to simulation window [{sim_start.date()} .. {sim_end.date()}]."
        )

    obs_df_all.to_csv(workspace / "assimilation_observations_before_window_filter.csv", index=False, encoding="utf-8-sig")
    obs_df.to_csv(workspace / "assimilation_observations_used.csv", index=False, encoding="utf-8-sig")
    with open(workspace / "assimilation_window_filter_diagnostics.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "sim_window_start": str(sim_start.date()),
                "sim_window_end": str(sim_end.date()),
                "n_obs_before_filter": int(len(obs_df_all)),
                "n_obs_after_filter": int(len(obs_df)),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    methods = [standardize_method_name(m) for m in cfg.experiment.methods]
    summaries = []
    method_outputs: Dict[str, Dict[str, object]] = {}
    method_roots: Dict[str, Path] = {}
    enkf_result: Optional[Dict[str, object]] = None

    for method in methods:
        method_root = workspace / method
        method_root.mkdir(parents=True, exist_ok=True)
        method_roots[method] = method_root
        try:
            if method == "open_loop":
                out = run_open_loop_method(cfg, base_values, obs_df, method_root)
                method_outputs[method] = out
                summaries.append({k: v for k, v in out.items() if not str(k).startswith("_")})
            elif method == "enkf":
                enkf_result = run_enkf_method(cfg, base_values, obs_df, method_root)
                method_outputs[method] = enkf_result
                summaries.append({k: v for k, v in enkf_result.items() if not str(k).startswith("_")})
            elif method == "4dvar":
                init_state = np.array([p.prior_mean for p in cfg.parameters], dtype=float)
                prior_std = np.array([max(p.prior_std, 0.05) for p in cfg.parameters], dtype=float)
                out = run_4dvar_method(
                    cfg, base_values, obs_df, method_root, init_state, init_state.copy(), prior_std, "4dvar"
                )
                method_outputs[method] = out
                summaries.append({k: v for k, v in out.items() if not str(k).startswith("_")})
            elif method == "enkf_4dvar":
                if enkf_result is None:
                    temp_root = workspace / "_internal_enkf_for_hybrid"
                    temp_root.mkdir(parents=True, exist_ok=True)
                    enkf_result = run_enkf_method(cfg, base_values, obs_df, temp_root)
                init_state = np.array(enkf_result["_posterior_mean"], dtype=float)
                prior_mean = np.array(enkf_result["_posterior_mean"], dtype=float)
                prior_std = np.maximum(np.array(enkf_result["_posterior_std"], dtype=float), 0.03)
                out = run_4dvar_method(
                    cfg, base_values, obs_df, method_root, init_state, prior_mean, prior_std, "enkf_4dvar"
                )
                method_outputs[method] = out
                summaries.append({k: v for k, v in out.items() if not str(k).startswith("_")})
            else:
                raise ValueError(f"Unsupported method: {method}")
        except Exception as e:
            method_outputs[method] = {"method": method, "status": "failed", "error": str(e)}
            summaries.append(
                {
                    "method": method,
                    "status": "failed",
                    "error": str(e),
                    "lai_rmse": np.nan,
                    "lai_mae": np.nan,
                    "lai_bias": np.nan,
                    "yield_last": np.nan,
                    "yield_peak": np.nan,
                    "biomass_last": np.nan,
                    "biomass_peak": np.nan,
                    "final_state": "",
                }
            )

    summary_df = pd.DataFrame(summaries)
    if "method" in summary_df.columns:
        summary_df = summary_df.set_index("method", drop=False)

    if cfg.truth_validation.enabled and cfg.truth_validation.excel_path:
        truth_validate = load_truth_validation_from_excel(cfg.truth_validation)
        truth_validate.to_csv(workspace / "truth_validation_loaded_from_excel.csv", index=False, encoding="utf-8-sig")

        for method, out in method_outputs.items():
            if out.get("status") != "ok" or "_df_final" not in out:
                continue
            df_final = out["_df_final"]  # type: ignore[assignment]
            method_root = method_roots[method]
            metrics = evaluate_biomass_and_yield(df_final, cfg, truth_validate, method_root)
            for k, v in metrics.items():
                out[k] = v
                if method in summary_df.index:
                    summary_df.loc[method, k] = v

        generate_truth_validation_plots(workspace, cfg, method_outputs, truth_validate)

    summary_df.insert(0, "obs_source", cfg.experiment.obs_source)
    summary_df.insert(1, "n_obs", len(obs_df))
    summary_df = summary_df.reset_index(drop=True)
    summary_df.to_csv(workspace / "method_comparison_summary.csv", index=False, encoding="utf-8-sig")
    return summary_df


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sparse-truth calibration of Sentinel LAI + multi-method APSIM assimilation comparison"
    )
    parser.add_argument("config", help="Path to JSON config")
    args = parser.parse_args()
    out = run_comparison(args.config)
    print("Method comparison completed. Summary:")
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()
