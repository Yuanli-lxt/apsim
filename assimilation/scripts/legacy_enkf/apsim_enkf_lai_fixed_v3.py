from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd


# ============================================================
# APSIM Classic + EnKF (LAI assimilation via parameter augmentation)
# ------------------------------------------------------------
# Why this design?
# APSIM 7.10 Classic can be run safely from Python via APSIM.exe.
# However, directly overwriting an internal crop state such as LAI midway
# through the simulation is brittle in Classic because the state is not
# exposed as a stable public API. This script therefore uses a robust and
# common workaround: assimilate LAI observations by updating an ensemble of
# uncertain model parameters, then rerun APSIM.
#
# In practice, this is often the most stable way to do sequential
# assimilation with APSIM Classic when you control the .apsim XML but not
# the internal runtime state object.
# ============================================================


@dataclass
class ParameterSpec:
    name: str
    selector_type: str            # "path" or "tag_contains"
    selector: str
    mode: str                     # "multiply" or "set"
    prior_mean: float
    prior_std: float
    min_value: float
    max_value: float


@dataclass
class ReportSpec:
    output_pattern: str           # e.g. "*.out"
    date_col: str                 # e.g. "date"
    lai_col: str                  # e.g. "lai"
    date_format: Optional[str] = None


@dataclass
class EnKFSpec:
    n_ensemble: int
    inflation: float
    process_noise_frac: float     # relative noise after each analysis step
    random_seed: int


@dataclass
class Config:
    template_apsim: str
    apsim_exe: str
    workspace: str
    observations_csv: str
    report: ReportSpec
    enkf: EnKFSpec
    parameters: List[ParameterSpec]


# -----------------------------
# Utility: config loading
# -----------------------------
def load_config(path: str) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        template_apsim=raw["template_apsim"],
        apsim_exe=raw["apsim_exe"],
        workspace=raw["workspace"],
        observations_csv=raw["observations_csv"],
        report=ReportSpec(**raw["report"]),
        enkf=EnKFSpec(**raw["enkf"]),
        parameters=[ParameterSpec(**p) for p in raw["parameters"]],
    )


# -----------------------------
# XML helpers
# -----------------------------
def is_number(text: Optional[str]) -> bool:
    if text is None:
        return False
    try:
        float(str(text).strip())
        return True
    except Exception:
        return False


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
        raise ValueError(
            f"No numeric XML element matched selector_type={selector_type!r}, selector={selector!r}."
        )
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
        if p.mode == "multiply":
            new_value = base_values[p.name] * x
        elif p.mode == "set":
            new_value = x
        else:
            raise ValueError(f"Unsupported mode: {p.mode}")
        elem.text = f"{new_value:.10g}"

    tree.write(dst_apsim, encoding="utf-8")


# -----------------------------
# APSIM running + output reading
# -----------------------------
def run_apsim_classic(apsim_exe: str, apsim_file: str, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    proc = subprocess.run(
        [apsim_exe, apsim_file],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def find_output_file(run_dir: str, pattern: str) -> Path:
    files = sorted(Path(run_dir).glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"No output file matched pattern {pattern!r} in {run_dir}")
    return files[0]


def read_apsim_output_table(path: str) -> pd.DataFrame:
    """
    Robust reader for APSIM Classic .out files.

    APSIM 7.10 .out files often contain several metadata lines before the real
    table header, for example a first line like:
        ApsimVersion = 7.10 r4221
    This function automatically searches for the first plausible header row and
    then reads the table from there.
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    header_idx = None
    sep: str = r"\s+"

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            continue

        # Candidate header tokens
        parts_tab = [c.strip() for c in s.split("	") if c.strip()]
        parts_ws = s.split()

        for parts, candidate_sep in ((parts_tab, "	"), (parts_ws, r"\s+")):
            if len(parts) < 2:
                continue
            cand = [p.lower() for p in parts]
            looks_like_header = (
                any(x in cand for x in ["date", "day", "year", "clock.today"]) or
                any("lai" in x for x in cand)
            )
            if looks_like_header:
                header_idx = i
                sep = candidate_sep
                break
        if header_idx is not None:
            break

    if header_idx is None:
        preview = ''.join(lines[:20])
        raise ValueError(
            f"Could not find a valid APSIM table header in output file: {path}\n"
            f"First 20 lines were:\n{preview}"
        )

    df = pd.read_csv(
        path,
        sep=sep,
        engine="python",
        skiprows=header_idx,
        comment="!",
        skip_blank_lines=True,
    )
    df.columns = [str(c).strip() for c in df.columns]
    return df


def normalize_date_column(s: pd.Series, date_format: Optional[str]) -> pd.Series:
    s = s.astype(str).str.strip()
    if date_format:
        return pd.to_datetime(s, format=date_format, errors="coerce")

    # Try the common formats in this workflow first to avoid pandas fallback warnings.
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        dt = pd.to_datetime(s, format=fmt, errors="coerce")
        if dt.notna().any():
            return dt

    # Last resort: mixed parsing, still suppressing the noisy infer-format warning path.
    return pd.to_datetime(s, format="mixed", errors="coerce")


def _find_column_case_insensitive(df: pd.DataFrame, candidates: Sequence[str], kind: str) -> str:
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}
    for candidate in candidates:
        key = str(candidate).strip().lower()
        if key in cols:
            return cols[key]

    # relaxed matching for APSIM outputs like wheatlai / wheat_lai / Wheat.LAI
    normalized = {
        ''.join(ch for ch in str(c).lower() if ch.isalnum()): str(c).strip()
        for c in df.columns
    }
    for candidate in candidates:
        key = ''.join(ch for ch in str(candidate).lower() if ch.isalnum())
        if key in normalized:
            return normalized[key]

    # special heuristic for LAI-like columns
    if kind.lower() == 'lai':
        lai_like = []
        for c in df.columns:
            ck = ''.join(ch for ch in str(c).lower() if ch.isalnum())
            if 'lai' in ck:
                lai_like.append(str(c).strip())
        if len(lai_like) == 1:
            return lai_like[0]

    raise KeyError(f"{kind} column not found. Tried {list(candidates)}. Existing columns: {list(df.columns)}")


def extract_lai_on_date(df: pd.DataFrame, report: ReportSpec, obs_date: pd.Timestamp) -> float:
    date_candidates = [report.date_col, "date", "Date", "clock.today", "Clock.Today", "today", "Today"]
    lai_candidates = [report.lai_col, "lai", "LAI", "wheatlai", "WheatLAI", "wheat_lai", "Wheat_LAI", "wheat.lai", "Wheat.LAI", "crop.lai", "Crop.LAI"]

    date_col = _find_column_case_insensitive(df, date_candidates, "Date")
    lai_col = _find_column_case_insensitive(df, lai_candidates, "LAI")

    tmp = df.copy()
    tmp[date_col] = normalize_date_column(tmp[date_col], report.date_format)
    tmp = tmp.dropna(subset=[date_col])
    if tmp.empty:
        raise ValueError("No valid dates found in APSIM output.")

    # Exact date preferred; otherwise nearest past date.
    obs_date = pd.Timestamp(obs_date).normalize()
    tmp["_date_norm"] = tmp[date_col].dt.normalize()

    exact = tmp[tmp["_date_norm"] == obs_date]
    if not exact.empty:
        return float(exact.iloc[-1][lai_col])

    past = tmp[tmp["_date_norm"] <= obs_date]
    if not past.empty:
        return float(past.iloc[-1][lai_col])

    # Fallback: nearest date
    idx = (tmp["_date_norm"] - obs_date).abs().idxmin()
    return float(tmp.loc[idx, lai_col])


# -----------------------------
# EnKF core (parameter augmentation)
# -----------------------------
def clip_state(x: np.ndarray, parameters: Sequence[ParameterSpec]) -> np.ndarray:
    y = x.copy()
    for j, p in enumerate(parameters):
        y[:, j] = np.clip(y[:, j], p.min_value, p.max_value)
    return y


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
    """
    EnKF update where the model state is an augmented parameter vector and the
    observation is a scalar LAI value.

    Xf: (N, p) parameter ensemble
    yf: (N,) predicted LAI ensemble
    """
    Xf = Xf.copy()
    N, p = Xf.shape
    if N < 2:
        raise ValueError("Ensemble size must be at least 2")

    # Multiplicative inflation around ensemble mean
    xm = Xf.mean(axis=0, keepdims=True)
    Xf = xm + inflation * (Xf - xm)

    yfm = float(np.mean(yf))
    A = Xf - Xf.mean(axis=0, keepdims=True)     # (N, p)
    dy = (yf - yfm).reshape(-1, 1)              # (N, 1)

    Pxy = (A.T @ dy) / (N - 1)                  # (p, 1)
    Pyy = float((dy.T @ dy) / (N - 1)) + obs_std ** 2
    K = (Pxy / Pyy).reshape(-1)                 # (p,)

    Xa = np.zeros_like(Xf)
    for i in range(N):
        y_pert = y_obs + rng.normal(0.0, obs_std)
        Xa[i, :] = Xf[i, :] + K * (y_pert - yf[i])

    # Mild process noise to avoid collapse
    spread = Xa.std(axis=0, ddof=1)
    noise = rng.normal(0.0, 1.0, size=Xa.shape) * (process_noise_frac * np.maximum(spread, 1e-12))
    Xa = Xa + noise

    Xa = clip_state(Xa, parameters)
    return Xa


# -----------------------------
# Observation loading
# -----------------------------
def load_observations(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    cols = {str(c).strip().lower(): str(c).strip() for c in df.columns}

    date_col = cols.get("date")
    lai_col = cols.get("lai") or cols.get("lai_obs") or cols.get("laiobs")
    std_col = cols.get("std") or cols.get("lai_std") or cols.get("obs_std")

    if date_col is None:
        raise KeyError(f"Observation CSV must contain a date column. Existing columns: {list(df.columns)}")
    if lai_col is None:
        raise KeyError(
            "Observation CSV must contain a LAI column. Acceptable names include "
            f"'lai' or 'lai_obs'. Existing columns: {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["date"] = normalize_date_column(df[date_col], None)
    out["lai"] = pd.to_numeric(df[lai_col], errors="coerce")
    out["std"] = pd.to_numeric(df[std_col], errors="coerce") if std_col else np.nan
    out = out.dropna(subset=["date", "lai"]).sort_values("date").reset_index(drop=True)
    out["std"] = out["std"].fillna(max(0.1, float(out["lai"].std(ddof=0) * 0.15 if len(out) > 1 else 0.2)))
    return out


# -----------------------------
# Ensemble creation
# -----------------------------
def sample_initial_ensemble(parameters: Sequence[ParameterSpec], n_ensemble: int, rng: np.random.Generator) -> np.ndarray:
    X = np.zeros((n_ensemble, len(parameters)), dtype=float)
    for j, p in enumerate(parameters):
        X[:, j] = rng.normal(p.prior_mean, p.prior_std, size=n_ensemble)
        X[:, j] = np.clip(X[:, j], p.min_value, p.max_value)
    return X


# -----------------------------
# Main pipeline
# -----------------------------
def run_assimilation(config_path: str) -> pd.DataFrame:
    cfg = load_config(config_path)
    rng = np.random.default_rng(cfg.enkf.random_seed)
    random.seed(cfg.enkf.random_seed)

    template_apsim = os.path.abspath(cfg.template_apsim)
    apsim_exe = os.path.abspath(cfg.apsim_exe)
    workspace = Path(cfg.workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    if not os.path.isfile(template_apsim):
        raise FileNotFoundError(f"Template APSIM file not found: {template_apsim}")
    if not os.path.isfile(apsim_exe):
        raise FileNotFoundError(f"APSIM executable not found: {apsim_exe}")

    obs = load_observations(cfg.observations_csv)
    base_values = read_base_parameter_values(template_apsim, cfg.parameters)
    X = sample_initial_ensemble(cfg.parameters, cfg.enkf.n_ensemble, rng)

    history_rows: List[Dict[str, Any]] = []
    template_name = Path(template_apsim).name

    for k, row in obs.iterrows():
        obs_date = pd.Timestamp(row["date"])
        y_obs = float(row["lai"])
        obs_std = float(row["std"])

        y_pred = np.zeros(cfg.enkf.n_ensemble, dtype=float)

        for i in range(cfg.enkf.n_ensemble):
            ens_dir = workspace / f"ens_{i:03d}"
            if ens_dir.exists():
                shutil.rmtree(ens_dir)
            ens_dir.mkdir(parents=True, exist_ok=True)

            run_apsim_path = ens_dir / template_name
            apply_parameter_values(
                src_apsim=template_apsim,
                dst_apsim=str(run_apsim_path),
                parameters=cfg.parameters,
                base_values=base_values,
                state_vector=X[i, :],
            )

            code, stdout, stderr = run_apsim_classic(apsim_exe, str(run_apsim_path), cwd=str(ens_dir))
            if code != 0:
                raise RuntimeError(
                    f"APSIM run failed for ensemble {i} at obs step {k}.\n"
                    f"stdout:\n{stdout}\n\n"
                    f"stderr:\n{stderr}"
                )

            out_file = find_output_file(str(ens_dir), cfg.report.output_pattern)
            df_out = read_apsim_output_table(str(out_file))
            y_pred[i] = extract_lai_on_date(df_out, cfg.report, obs_date)

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

        # Diagnostics: store posterior parameter mean
        diag: Dict[str, Any] = {
            "step": k,
            "date": obs_date.strftime("%Y-%m-%d"),
            "lai_obs": y_obs,
            "lai_prior_mean": prior_mean,
            "lai_prior_std": prior_std,
        }
        for j, p in enumerate(cfg.parameters):
            diag[f"post_{p.name}_mean"] = float(X[:, j].mean())
            diag[f"post_{p.name}_std"] = float(X[:, j].std(ddof=1)) if cfg.enkf.n_ensemble > 1 else 0.0
        history_rows.append(diag)

        print(f"[step {k}] {diag['date']} | obs={y_obs:.4f} | prior_mean={prior_mean:.4f} | prior_std={prior_std:.4f}")

    hist = pd.DataFrame(history_rows)
    hist_path = workspace / "assimilation_history.csv"
    hist.to_csv(hist_path, index=False, encoding="utf-8-sig")

    # Final posterior mean parameter file for whole-season rerun
    final_dir = workspace / "final_posterior_mean"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    final_dir.mkdir(parents=True, exist_ok=True)
    final_state = X.mean(axis=0)
    final_model = final_dir / template_name
    apply_parameter_values(
        src_apsim=template_apsim,
        dst_apsim=str(final_model),
        parameters=cfg.parameters,
        base_values=base_values,
        state_vector=final_state,
    )

    code, stdout, stderr = run_apsim_classic(apsim_exe, str(final_model), cwd=str(final_dir))
    if code != 0:
        raise RuntimeError(f"Final posterior mean APSIM run failed.\nstdout:\n{stdout}\n\nstderr:\n{stderr}")

    print(f"Assimilation finished. History saved to: {hist_path}")
    return hist


# -----------------------------
# Inspection helper
# -----------------------------
def scan_numeric_xml_nodes(apsim_file: str, keyword: Optional[str] = None) -> pd.DataFrame:
    """
    Helps you find XML nodes that can be used in the config selectors.
    Example:
        df = scan_numeric_xml_nodes(r"F:\\APSIM710-r4221\\yuan\\test.apsim", keyword="sla")
        print(df.head(50))
    """
    tree = ET.parse(apsim_file)
    root = tree.getroot()
    rows = []
    keyword_low = keyword.lower() if keyword else None
    for elem in root.iter():
        if is_number(elem.text):
            text_tag = str(elem.tag)
            attrs = dict(elem.attrib)
            blob = (text_tag + " " + json.dumps(attrs, ensure_ascii=False)).lower()
            if keyword_low and keyword_low not in blob:
                continue
            rows.append({
                "tag": text_tag,
                "attrs": attrs,
                "value": float(str(elem.text).strip()),
            })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="APSIM Classic + EnKF LAI assimilation")
    parser.add_argument("config", help="Path to JSON config")
    args = parser.parse_args()
    run_assimilation(args.config)
