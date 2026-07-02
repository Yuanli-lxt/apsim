#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sobol-guided APSIM Classic yield simulation iteration.

The workflow starts from the calibrated cultivar+system baseline, creates
single-parameter yield candidates guided by existing Sobol results, runs APSIM,
and ranks candidates against observed crop yield.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from lxml import etree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = PROJECT_ROOT / "models" / "apsim_classic" / "calibrated_baseline"
BASE_APSIM = BASELINE_DIR / "baseline_after_cultivar_sobol.apsim"
BASE_WHEAT_XML = BASELINE_DIR / "Wheat.xml"
BASE_MAIZE_XML = BASELINE_DIR / "Maize.xml"
OBS_CSV = PROJECT_ROOT / "data" / "processed" / "observations" / "independent_validation_observations_p02_maize_p01_wheat.csv"
WORK_DIR = PROJECT_ROOT / "outputs" / "yield_simulation"
FINAL_DIR = WORK_DIR / "final_results"
INTERMEDIATE_DIR = WORK_DIR / "intermediate_and_raw_files"
HELPER_DIR = WORK_DIR / "helper_files"
RUN_DIR = INTERMEDIATE_DIR / "apsim_runs"
LOG_DIR = INTERMEDIATE_DIR / "logs"
BEST_DIR = FINAL_DIR / "best"

APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")
MODEL_DIR = APSIM_EXE.parent
RUNTIME_XML = {"wheat": MODEL_DIR / "Wheat.xml", "maize": MODEL_DIR / "Maize.xml"}

SYSTEM_SOBOL = PROJECT_ROOT / "outputs" / "system_sensitivity" / "final_results" / "sobol_indices_summary.csv"
CULTIVAR_SOBOL = (
    PROJECT_ROOT
    / "outputs"
    / "sobol"
    / "organized_outputs_screened_N128_20260515_185604"
    / "final_results"
    / "sobol_top5_by_target.csv"
)

for scripts_dir in [PROJECT_ROOT / "scripts" / "system_sensitivity", PROJECT_ROOT / "scripts" / "sobol"]:
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

from system_common import build_system_parameter_rows, modify_apsim_for_system_sample  # noqa: E402
from sobol_common import read_apsim_out, update_xml_parameter  # noqa: E402


@dataclass(frozen=True)
class Candidate:
    sample_id: int
    source: str
    crop: str
    parameter_key: str
    parameter_name: str
    direction: str
    value: float | None
    baseline_value: float | None
    note: str


def ensure_dirs() -> None:
    for path in [WORK_DIR, FINAL_DIR, INTERMEDIATE_DIR, HELPER_DIR, RUN_DIR, LOG_DIR, BEST_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def read_csv_fallback(path: Path) -> pd.DataFrame:
    last_error: Exception | None = None
    for encoding in ["utf-8-sig", "utf-8", "gbk", "latin1"]:
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not decode {path}: {last_error}")


def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    return etree.parse(str(path), parser)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(path), encoding="utf-8", xml_declaration=False, pretty_print=True)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def as_float(value: object) -> float | None:
    try:
        text = clean_text(value)
        if text == "":
            return None
        return float(text)
    except Exception:
        return None


def format_float(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.10g}"


def manager_cultivars(apsim_path: Path) -> dict[str, str]:
    tree = parse_xml(apsim_path)
    out: dict[str, str] = {}
    for crop, manager_name in [("wheat", "Wheat Management"), ("maize", "Maize Management")]:
        nodes = tree.xpath(
            ".//*[local-name()='manager2' and @name=$manager]/*[local-name()='ui']/*[local-name()='cultivar1']",
            manager=manager_name,
        )
        if nodes:
            out[crop] = clean_text(nodes[0].text)
    return out


def set_output_filenames(apsim_path: Path, sample_id: int) -> str:
    tree = parse_xml(apsim_path)
    out_dir = RUN_DIR / "outputs" / f"case_{sample_id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_files: list[str] = []
    for node in tree.xpath(".//*[local-name()='outputfile']/*[local-name()='filename']"):
        old_name = clean_text(node.text) or f"case_{sample_id:06d}.out"
        new_path = out_dir / Path(old_name).name
        node.text = str(new_path)
        output_files.append(str(new_path))
    write_xml(tree, apsim_path)
    return ";".join(output_files)


def load_yield_targets(obs_csv: Path) -> dict[str, float]:
    obs = read_csv_fallback(obs_csv)
    required = {"crop", "variable_name", "value"}
    missing = required - set(obs.columns)
    if missing:
        raise ValueError(f"Observation CSV missing columns: {sorted(missing)}")
    mask = obs["variable_name"].astype(str).str.strip().eq("产量/kg/公顷")
    rows = obs[mask].copy()
    if rows.empty:
        raise ValueError("No observed yield rows named '产量/kg/公顷' were found.")
    rows["value"] = pd.to_numeric(rows["value"], errors="coerce")
    rows["crop"] = rows["crop"].astype(str).str.lower().str.strip()
    targets = rows.dropna(subset=["value"]).groupby("crop")["value"].mean().to_dict()
    return {crop: float(value) for crop, value in targets.items() if crop in {"wheat", "maize"}}


def top_system_parameters(top_n: int) -> list[str]:
    if not SYSTEM_SOBOL.exists():
        return []
    sobol = read_csv_fallback(SYSTEM_SOBOL)
    grain = sobol[sobol["target_variable"].astype(str).eq("grain_yield")].copy()
    grain["ST"] = pd.to_numeric(grain["ST"], errors="coerce").fillna(0.0)
    grain = grain.sort_values(["crop", "ST"], ascending=[True, False])
    keys: list[str] = []
    for _, row in grain.iterrows():
        key = clean_text(row.get("parameter_key"))
        if key and key not in keys:
            keys.append(key)
        if len(keys) >= top_n:
            break
    return keys


def top_cultivar_rows(top_n_per_crop: int) -> list[dict]:
    if not CULTIVAR_SOBOL.exists():
        return []
    sobol = read_csv_fallback(CULTIVAR_SOBOL)
    grain = sobol[sobol["target_variable"].astype(str).eq("grain_yield")].copy()
    grain["ST"] = pd.to_numeric(grain["ST"], errors="coerce").fillna(0.0)
    rows: list[dict] = []
    for crop, group in grain.groupby("crop"):
        for _, row in group.sort_values("ST", ascending=False).head(top_n_per_crop).iterrows():
            rows.append(row.to_dict())
    return rows


def scalar_or_index_from_key(row: dict) -> tuple[str, str]:
    name = clean_text(row.get("parameter_name"))
    key = clean_text(row.get("parameter_key"))
    m = re.search(r"__i(\d+)$", key)
    value_index = m.group(1) if m else ""
    base = re.sub(r"\[\d+\]$", "", name).replace("_1", "").replace("_2", "")
    if name.startswith("largestLeafParams") or "largestLeafParams" in key:
        base = "largestLeafParams"
    return base, value_index


def read_cultivar_value(xml_path: Path, cultivar: str, parameter_name: str, value_index: str) -> float | None:
    tree = parse_xml(xml_path)
    nodes = tree.xpath(".//*[local-name()=$name and @cultivar='yes']", name=cultivar)
    if not nodes:
        return None
    params = nodes[0].xpath("./*[local-name()=$pname]", pname=parameter_name)
    if not params:
        return None
    text = clean_text(params[0].text)
    if value_index == "":
        return as_float(text)
    values = [as_float(part) for part in re.split(r"[\s,;]+", text) if part != ""]
    idx = int(value_index)
    if idx >= len(values):
        return None
    return values[idx]


def bounded_delta_value(base: float, direction: str, step: float, lower: float | None, upper: float | None) -> float:
    if abs(base) < 1e-9:
        raw = step if direction == "up" else -step
    else:
        raw = base * (1.0 + step if direction == "up" else 1.0 - step)
    if lower is not None:
        raw = max(raw, lower)
    if upper is not None:
        raw = min(raw, upper)
    return raw


def build_candidates(max_cases: int, relative_step: float) -> tuple[list[Candidate], list[dict]]:
    cultivars = manager_cultivars(BASE_APSIM)
    system_rows = {row["parameter_key"]: row for row in build_system_parameter_rows(BASE_APSIM)}
    candidates: list[Candidate] = [
        Candidate(0, "baseline", "both", "baseline", "baseline", "none", None, None, "unchanged calibrated baseline")
    ]
    priority_rows: list[dict] = []

    for key in top_system_parameters(top_n=6):
        row = system_rows.get(key)
        if not row:
            continue
        base = as_float(row.get("baseline_value"))
        if base is None:
            continue
        lower = as_float(row.get("lower_bound"))
        upper = as_float(row.get("upper_bound"))
        for direction in ["down", "up"]:
            value = bounded_delta_value(base, direction, relative_step, lower, upper)
            candidates.append(
                Candidate(
                    len(candidates),
                    "system_sobol",
                    clean_text(row.get("crop")) or "system",
                    key,
                    clean_text(row.get("parameter_name")),
                    direction,
                    value,
                    base,
                    clean_text(row.get("biological_meaning")),
                )
            )
            priority_rows.append({"source": "system_sobol", **row, "direction": direction, "candidate_value": value})

    for row in top_cultivar_rows(top_n_per_crop=4):
        crop = clean_text(row.get("crop")).lower()
        cultivar = cultivars.get(crop)
        if crop not in {"wheat", "maize"} or not cultivar:
            continue
        xml_path = BASE_WHEAT_XML if crop == "wheat" else BASE_MAIZE_XML
        base_param, value_index = scalar_or_index_from_key(row)
        base = read_cultivar_value(xml_path, cultivar, base_param, value_index)
        if base is None:
            continue
        for direction in ["down", "up"]:
            value = bounded_delta_value(base, direction, relative_step, None, None)
            candidates.append(
                Candidate(
                    len(candidates),
                    "cultivar_sobol",
                    crop,
                    clean_text(row.get("parameter_key")),
                    f"{base_param}[{value_index}]" if value_index else base_param,
                    direction,
                    value,
                    base,
                    f"current cultivar={cultivar}; Sobol ST={row.get('ST')}",
                )
            )
            priority_rows.append({"source": "cultivar_sobol", **row, "current_cultivar": cultivar, "candidate_value": value})

    return candidates[:max_cases], priority_rows


def copy_inputs(case_dir: Path) -> tuple[Path, Path, Path]:
    case_dir.mkdir(parents=True, exist_ok=True)
    apsim = case_dir / "yield_candidate.apsim"
    wheat = case_dir / "Wheat.xml"
    maize = case_dir / "Maize.xml"
    shutil.copy2(BASE_APSIM, apsim)
    shutil.copy2(BASE_WHEAT_XML, wheat)
    shutil.copy2(BASE_MAIZE_XML, maize)
    return apsim, wheat, maize


def apply_candidate(candidate: Candidate, apsim: Path, wheat_xml: Path, maize_xml: Path) -> str:
    output_files = set_output_filenames(apsim, candidate.sample_id)
    if candidate.source == "baseline":
        return output_files
    if candidate.source == "system_sobol":
        system_rows = {row["parameter_key"]: row for row in build_system_parameter_rows(BASE_APSIM)}
        row = system_rows[candidate.parameter_key]
        trace = modify_apsim_for_system_sample(
            BASE_APSIM,
            apsim,
            [row],
            {candidate.parameter_key: float(candidate.value)},
            candidate.sample_id,
        )
        set_output_filenames(apsim, candidate.sample_id)
        if trace and trace[0].get("status") != "ok":
            raise RuntimeError(trace[0].get("message", "system parameter modification failed"))
        return output_files

    cultivars = manager_cultivars(BASE_APSIM)
    crop = candidate.crop
    xml_path = wheat_xml if crop == "wheat" else maize_xml
    cultivar = cultivars[crop]
    if "[" in candidate.parameter_name:
        base_param, idx = candidate.parameter_name.rstrip("]").split("[", 1)
    else:
        base_param, idx = candidate.parameter_name, ""
    ok, msg = update_xml_parameter(xml_path, crop, cultivar, base_param, float(candidate.value), idx)
    if not ok:
        raise RuntimeError(msg)
    return output_files


def restore_runtime_xml(backups: dict[str, Path]) -> None:
    for crop, backup in backups.items():
        if backup.exists():
            shutil.copy2(backup, RUNTIME_XML[crop])


def run_apsim(case_dir: Path, apsim: Path, wheat_xml: Path, maize_xml: Path, timeout: int | None) -> tuple[str, float, Path]:
    log_file = LOG_DIR / f"{case_dir.name}.log"
    backups: dict[str, Path] = {}
    backup_dir = LOG_DIR / "runtime_xml_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for crop, runtime in RUNTIME_XML.items():
        if runtime.exists():
            backup = backup_dir / runtime.name
            shutil.copy2(runtime, backup)
            backups[crop] = backup
    try:
        shutil.copy2(wheat_xml, RUNTIME_XML["wheat"])
        shutil.copy2(maize_xml, RUNTIME_XML["maize"])
        start = time.time()
        with open(log_file, "w", encoding="utf-8", errors="ignore") as handle:
            handle.write(f"COMMAND: {APSIM_EXE} {apsim}\nWORKDIR: {case_dir}\n\n")
            proc = subprocess.run(
                [str(APSIM_EXE), str(apsim)],
                cwd=str(case_dir),
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=timeout,
            )
        elapsed = time.time() - start
        return ("finished" if proc.returncode == 0 else f"failed_returncode_{proc.returncode}", elapsed, log_file)
    except subprocess.TimeoutExpired:
        return "failed_timeout", float(timeout or 0), log_file
    finally:
        restore_runtime_xml(backups)


def collect_yields(output_files: str, case_dir: Path) -> tuple[dict[str, float | None], list[str]]:
    files = [Path(p) for p in output_files.split(";") if p]
    files.extend(case_dir.glob("*.out"))
    seen = set()
    files = [p for p in files if not (str(p) in seen or seen.add(str(p)))]
    found = {"wheat": None, "maize": None}
    candidates = {
        "wheat": ["WheatYield", "wheat.Yield", "paddock.wheat.yield"],
        "maize": ["MaizeYield", "maize.Yield", "paddock.maize.yield"],
    }
    existing_files: list[str] = []
    for path in files:
        if not path.exists() or path.suffix.lower() != ".out":
            continue
        existing_files.append(str(path))
        df = read_apsim_out(path)
        if df.empty:
            continue
        lower = {str(c).lower(): c for c in df.columns}
        for crop, names in candidates.items():
            if found[crop] is not None:
                continue
            col = next((lower[n.lower()] for n in names if n.lower() in lower), None)
            if col is None:
                continue
            values = pd.to_numeric(df[col], errors="coerce").dropna()
            if not values.empty:
                found[crop] = float(values.max())
    return found, existing_files


def score_yields(pred: dict[str, float | None], targets: dict[str, float]) -> dict:
    rows = []
    rels = []
    for crop, obs in targets.items():
        sim = pred.get(crop)
        abs_error = None if sim is None else abs(float(sim) - obs)
        rel_error = None if sim is None or abs(obs) < 1e-9 else abs_error / abs(obs)
        if rel_error is not None:
            rels.append(rel_error)
        rows.append({"crop": crop, "observed_yield_kg_ha": obs, "simulated_yield_kg_ha": sim, "abs_error": abs_error, "rel_error": rel_error})
    return {"rows": rows, "mean_relative_error": float(sum(rels) / len(rels)) if rels else None}


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, ensure_ascii=False, indent=2)


def write_summary(best: dict | None, targets: dict[str, float], cases: list[dict], path: Path) -> None:
    lines = [
        "# 作物产量 Sobol 引导模拟迭代",
        "",
        f"- 创建时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 基础模型：`{BASE_APSIM}`",
        f"- 工作目录：`{WORK_DIR}`",
        f"- 观测产量目标：{json.dumps(targets, ensure_ascii=False)}",
        f"- 已评估 case 数：{len(cases)}",
    ]
    if best:
        lines.extend(
            [
                "",
                "## 当前 best",
                f"- case：{best['case_id']}",
                f"- 来源：{best['source']}",
                f"- 参数：{best['parameter_name']} ({best['direction']})",
                f"- 平均相对误差：{best.get('mean_relative_error')}",
                f"- 目录：`{best['case_dir']}`",
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Sobol-guided APSIM yield simulation iteration.")
    parser.add_argument("--max-cases", type=int, default=15, help="Maximum cases including baseline.")
    parser.add_argument("--relative-step", type=float, default=0.03, help="Single-parameter perturbation size.")
    parser.add_argument("--timeout", type=int, default=None, help="APSIM timeout per case in seconds.")
    parser.add_argument("--prepare-only", action="store_true", help="Only generate cases and manifests; do not run APSIM.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ensure_dirs()
    for path in [BASE_APSIM, BASE_WHEAT_XML, BASE_MAIZE_XML, OBS_CSV, APSIM_EXE]:
        if not path.exists():
            raise FileNotFoundError(path)

    targets = load_yield_targets(OBS_CSV)
    candidates, priority_rows = build_candidates(args.max_cases, args.relative_step)
    if priority_rows:
        pd.DataFrame(priority_rows).to_csv(HELPER_DIR / "sobol_priority_parameters_used.csv", index=False, encoding="utf-8-sig")

    index_rows: list[dict] = []
    all_score_rows: list[dict] = []
    best: dict | None = None

    for candidate in candidates:
        case_tag = f"case_{candidate.sample_id:06d}"
        case_dir = RUN_DIR / case_tag
        apsim, wheat_xml, maize_xml = copy_inputs(case_dir)
        status = "prepared"
        output_files = ""
        elapsed = 0.0
        log_file = ""
        try:
            output_files = apply_candidate(candidate, apsim, wheat_xml, maize_xml)
            if not args.prepare_only:
                status, elapsed, log_path = run_apsim(case_dir, apsim, wheat_xml, maize_xml, args.timeout)
                log_file = str(log_path)
            if status == "finished":
                pred, existing_outputs = collect_yields(output_files, case_dir)
            else:
                pred, existing_outputs = {"wheat": None, "maize": None}, []
            score = score_yields(pred, targets)
        except Exception as exc:
            status = f"failed_exception_{type(exc).__name__}"
            pred = {"wheat": None, "maize": None}
            existing_outputs = []
            score = {"rows": [], "mean_relative_error": None}
            log_file = str(LOG_DIR / f"{case_tag}.log")
            Path(log_file).write_text(str(exc), encoding="utf-8")

        row = {
            "case_id": candidate.sample_id,
            "case_dir": str(case_dir),
            "apsim_file": str(apsim),
            "wheat_xml": str(wheat_xml),
            "maize_xml": str(maize_xml),
            "source": candidate.source,
            "crop": candidate.crop,
            "parameter_key": candidate.parameter_key,
            "parameter_name": candidate.parameter_name,
            "direction": candidate.direction,
            "baseline_value": candidate.baseline_value,
            "candidate_value": candidate.value,
            "status": status,
            "elapsed_seconds": elapsed,
            "output_files": output_files,
            "existing_output_files": ";".join(existing_outputs),
            "log_file": log_file,
            "wheat_yield": pred.get("wheat"),
            "maize_yield": pred.get("maize"),
            "mean_relative_error": score.get("mean_relative_error"),
            "note": candidate.note,
        }
        index_rows.append(row)
        for score_row in score["rows"]:
            all_score_rows.append({"case_id": candidate.sample_id, **score_row})
        if row["mean_relative_error"] is not None and (
            best is None or float(row["mean_relative_error"]) < float(best["mean_relative_error"])
        ):
            best = row

    pd.DataFrame(index_rows).to_csv(FINAL_DIR / "yield_iteration_index.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(all_score_rows).to_csv(FINAL_DIR / "yield_prediction_vs_observation.csv", index=False, encoding="utf-8-sig")
    write_json(FINAL_DIR / "yield_iteration_manifest.json", {"targets": targets, "best": best, "cases": index_rows})
    if best:
        best_case_dir = Path(best["case_dir"])
        for name in ["yield_candidate.apsim", "Wheat.xml", "Maize.xml"]:
            src = best_case_dir / name
            if src.exists():
                shutil.copy2(src, BEST_DIR / name)
        write_json(BEST_DIR / "best_selection.json", best)
    write_summary(best, targets, index_rows, FINAL_DIR / "summary_zh.md")
    print(f"Wrote {FINAL_DIR / 'yield_iteration_index.csv'}")
    print(f"Wrote {FINAL_DIR / 'yield_prediction_vs_observation.csv'}")
    print(f"Wrote {FINAL_DIR / 'summary_zh.md'}")
    if best:
        print(f"Best case: {best['case_id']} mean_relative_error={best['mean_relative_error']}")


if __name__ == "__main__":
    main()
