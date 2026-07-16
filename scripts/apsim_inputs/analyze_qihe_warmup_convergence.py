"""Aggregate Qihe warm-up results and test initial-mineral-N convergence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
CONFIG = ROOT / "configs" / "spatial" / "qihe_warmup_sensitivity_scenarios.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_manifest(run_root: Path) -> None:
    manifest_path = run_root / "run_file_manifest.csv"
    rows = []
    for path in sorted(item for item in run_root.rglob("*") if item.is_file() and item != manifest_path):
        stat = path.stat()
        rows.append({
            "path": str(path.relative_to(ROOT)).replace("\\", "/"),
            "bytes": stat.st_size,
            "modified_time": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
            "sha256": sha256(path),
        })
    pd.DataFrame(rows).to_csv(manifest_path, index=False, encoding="utf-8-sig")


def weighted(group: pd.DataFrame, column: str) -> float:
    values = pd.to_numeric(group[column], errors="coerce")
    valid = values.notna()
    if not valid.any():
        return float("nan")
    return float(np.average(values[valid], weights=group.loc[valid, "soil_rotation_area_ha"]))


def relative_range(values: pd.Series) -> float:
    values = pd.to_numeric(values, errors="coerce").dropna()
    mean = float(values.mean())
    return float((values.max() - values.min()) / abs(mean) * 100.0) if len(values) and mean != 0 else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="qihe_warmup_2010_2023_v1")
    parser.add_argument("--data-run-id", help="Prepared weather/input run ID; defaults to --run-id.")
    args = parser.parse_args()
    data_root = PILOT / "warmup_sensitivity" / (args.data_run_id or args.run_id)
    run_root = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "warmup_sensitivity" / args.run_id
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    units = pd.read_csv(data_root / "simulation_units_10km.csv")
    cases = pd.read_csv(run_root / "annual_case_yield_and_mineral_n.csv")
    mapped = units[["case_id", "soil_rotation_area_ha", "grid_id", "hwsd_soil_unit", "weather_grid_id"]].merge(
        cases, on="case_id", how="inner", validate="many_to_many"
    )
    rows = []
    variables = ["wheat_yield_kg_ha", "maize_yield_kg_ha", "oct1_no3_kg_ha", "oct1_nh4_kg_ha"]
    for keys, group in mapped.groupby(["management_scenario", "initial_n_multiplier", "year"]):
        scenario, multiplier, year = keys
        row = {"management_scenario": scenario, "initial_n_multiplier": multiplier, "year": int(year)}
        row.update({column: weighted(group, column) for column in variables})
        row["oct1_mineral_n_kg_ha"] = row["oct1_no3_kg_ha"] + row["oct1_nh4_kg_ha"]
        row.update({
            "represented_rotation_area_ha": float(group.soil_rotation_area_ha.sum()),
            "grid_cells": int(group.grid_id.nunique()), "soil_units": int(group.hwsd_soil_unit.nunique()),
            "weather_nodes": int(group.weather_grid_id.nunique()), "unique_cases": int(group.case_id.nunique()),
        })
        rows.append(row)
    annual = pd.DataFrame(rows)
    annual.to_csv(run_root / "annual_county_yield_and_mineral_n.csv", index=False, encoding="utf-8-sig")

    checks = []
    yield_limit = float(config["convergence_criteria"]["annual_crop_yield_max_relative_range_percent"])
    n_limit = float(config["convergence_criteria"]["pre_sowing_mineral_n_max_relative_range_percent"])
    formal = set(config["formal_statistical_years"])
    for (scenario, year), group in annual.groupby(["management_scenario", "year"]):
        wheat_range = relative_range(group.wheat_yield_kg_ha)
        maize_range = relative_range(group.maize_yield_kg_ha)
        n_range = relative_range(group.oct1_mineral_n_kg_ha)
        checks.append({
            "management_scenario": scenario, "year": int(year), "formal_analysis_year": int(year) in formal,
            "wheat_yield_relative_range_percent": wheat_range, "maize_yield_relative_range_percent": maize_range,
            "oct1_mineral_n_relative_range_percent": n_range,
            "yield_converged": max(wheat_range, maize_range) <= yield_limit,
            "mineral_n_converged": n_range <= n_limit,
            "both_converged": max(wheat_range, maize_range) <= yield_limit and n_range <= n_limit,
        })
    convergence = pd.DataFrame(checks)
    convergence.to_csv(run_root / "warmup_convergence_by_year.csv", index=False, encoding="utf-8-sig")
    formal_checks = convergence.loc[convergence.formal_analysis_year]
    summary = formal_checks.groupby("management_scenario").agg(
        formal_years=("year", "count"), years_both_converged=("both_converged", "sum"),
        max_wheat_yield_range_pct=("wheat_yield_relative_range_percent", "max"),
        max_maize_yield_range_pct=("maize_yield_relative_range_percent", "max"),
        max_oct1_mineral_n_range_pct=("oct1_mineral_n_relative_range_percent", "max"),
    ).reset_index()
    summary["all_formal_years_converged"] = summary.years_both_converged == summary.formal_years
    summary.to_csv(run_root / "warmup_convergence_summary.csv", index=False, encoding="utf-8-sig")
    write_manifest(run_root)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
