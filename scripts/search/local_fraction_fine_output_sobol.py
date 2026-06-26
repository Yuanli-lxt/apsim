#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fine deterministic FractionFull grid around the current output_sobol best.

This is a deliberately narrow follow-up after the two-stage local search:
- uses current output_sobol/best as the baseline
- keeps crit_fr_asw fixed at 0.56
- keeps the current maize and wheat cultivars fixed
- does not use HDSW
- does not change soil physical properties, irrigation, fertilizer, density, weather, or rotation
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import run_process_bio_search as search


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "output_sobol"
BEST_DIR = OUT_ROOT / "best"
VALIDATION_CSV = ROOT / "data" / "processed" / "observations" / "independent_validation_observations_p02_maize_p01_wheat.csv"
CODEX_TRUTH = ROOT.parent / "codex" / "truth.apsim"

FRACTION_VALUES = [round(v, 3) for v in [0.546, 0.547, 0.548, 0.549, 0.550, 0.551, 0.552]]


class EvalArgs:
    pheno_guard_days = 6
    missing_truth_penalty = 1.0
    truth_rel_error_cap = 2.0
    wheat_late_stage_cn = search.WHEAT_LATE_STAGE_CN_DEFAULT
    wheat_anchor_weight = 0.20
    wheat_late_weight = 0.35
    wheat_anchor_max_weight = 0.10
    score_mode = "all_truth"
    enable_legacy_wheat_anchor_bonus = False
    target_yield_rel = 0.15
    target_soil_water_rel = 0.12
    soil_water_priority_weight = 0.65
    yield_constraint_weight = 0.25
    phenology_constraint_weight = 0.10
    stability_weight = 0.05
    min_soil_water_improvement = 0.00005
    max_total_irrigation_mm = 240.0


def parse_args():
    p = argparse.ArgumentParser(description="Fine FractionFull grid around current output_sobol best.")
    p.add_argument("--update_best", action="store_true")
    p.add_argument("--min_soil_improvement", type=float, default=0.00005)
    p.add_argument("--maize_hard_guard", type=float, default=0.15)
    p.add_argument("--maize_soft_guard_delta", type=float, default=0.002)
    p.add_argument("--biomass_worsen_frac", type=float, default=0.02)
    p.add_argument("--structure_worsen_frac", type=float, default=0.02)
    return p.parse_args()


def group_weights() -> dict:
    return search.normalize_group_weights(
        {
            "total_biomass": 0.25,
            "structure_biomass_leaf_stem": 0.20,
            "yield": 0.20,
            "LAI": 0.0,
            "soil_water": 0.15,
            "phenology": 0.10,
        }
    )


def parse_iw_and_crit(truth_text: str) -> tuple[float, float]:
    iw = float(search.parse_initial_water_config(truth_text)["fraction_full"])
    nodes = search.parse_crit_fr_asw(truth_text)
    if not nodes:
        raise ValueError("No crit_fr_asw found in truth.apsim.")
    return iw, float(nodes[0]["value"])


def set_fraction_fixed_crit(truth_text: str, fraction_full: float) -> str:
    cfg = search.parse_initial_water_config(truth_text)
    cfg["fraction_full"] = float(fraction_full)
    out = search.set_initial_water_config(truth_text, cfg)
    nodes = search.parse_crit_fr_asw(out)
    if not nodes:
        raise ValueError("No crit_fr_asw found after FractionFull update.")
    return search.set_crit_fr_asw(out, nodes[0], 0.56)


def sync_model_xml(wheat_text: str, maize_text: str) -> None:
    search.base.write_text(search.base.WHEAT_PATH, wheat_text)
    search.base.write_text(search.base.MAIZE_PATH, maize_text)


def evaluate_case(case_dir: Path, truth_text: str, wheat_text: str, maize_text: str, truth_obj: dict, args_obj: EvalArgs, weights: dict) -> tuple[dict, list[dict]]:
    case_dir.mkdir(parents=True, exist_ok=True)
    search.base.write_text(case_dir / "truth.apsim", truth_text)
    search.base.write_text(case_dir / "Wheat.xml", wheat_text)
    search.base.write_text(case_dir / "Maize.xml", maize_text)
    sync_model_xml(wheat_text, maize_text)
    search.base.run_apsim_on_truth(case_dir / "truth.apsim")
    eval_obj = search.base.evaluate_output_dir(case_dir, truth_obj)
    rows = search.collect_prediction_vs_truth_rows(case_dir, truth_obj, "candidate")
    pheno_diag = search._build_pheno_diag(
        eval_obj,
        search.extract_wheat_anchor_metrics(case_dir, truth_obj, args_obj.wheat_late_stage_cn),
    )
    custom = search.score_all_truth_objective(
        rows,
        args_obj.pheno_guard_days,
        pheno_diag,
        weights=weights,
        missing_truth_penalty=args_obj.missing_truth_penalty,
        rel_error_cap=args_obj.truth_rel_error_cap,
    )
    water = search.score_water_yield_objective(eval_obj, custom, args_obj)
    return {"eval": eval_obj, "custom": custom, "water": water}, rows


def layer_summary(rows: list[dict]) -> dict:
    out = {}
    for layer in ("water_1", "water_2", "water_3", "water_4", "water_5"):
        rels, signed = [], []
        for r in rows:
            if r.get("scenario") != "candidate" or r.get("variable") != layer:
                continue
            try:
                rels.append(float(r["rel_error"]))
                signed.append(float(r["sim_value"]) - float(r["obs_value"]))
            except Exception:
                continue
        out[f"{layer}_error"] = search.mean_valid(rels)
        out[f"{layer}_signed"] = search.mean_valid(signed)
    return out


def flatten(case_id: int, case_name: str, case_dir: Path, truth_text: str, result: dict, rows: list[dict]) -> dict:
    iw, crit = parse_iw_and_crit(truth_text)
    water = result["water"]
    custom = result["custom"]
    row = {
        "case_id": case_id,
        "case_name": case_name,
        "case_dir": str(case_dir),
        "FractionFull": iw,
        "crit_fr_asw": crit,
        "soil_water_error": water.get("soil_water_error"),
        "water_yield_score": water.get("water_yield_score"),
        "wheat_yield_error": water.get("wheat_yield_error"),
        "maize_yield_error": water.get("maize_yield_error"),
        "phenology_error_days_max": water.get("phenology_error_days_max"),
        "total_biomass_error": water.get("total_biomass_error"),
        "structure_error": water.get("structure_error"),
        "custom_score": custom.get("custom_score"),
    }
    row.update(layer_summary(rows))
    return row


def reject_reasons(row: dict, baseline: dict, cli) -> list[str]:
    reasons = []
    if row.get("soil_water_error") is None or row["soil_water_error"] >= baseline["soil_water_error"] - cli.min_soil_improvement:
        reasons.append("soil_water not lower enough")
    if row.get("wheat_yield_error") is None or row["wheat_yield_error"] >= 0.15:
        reasons.append("wheat yield error >= 0.15")
    if row.get("maize_yield_error") is None or row["maize_yield_error"] >= cli.maize_hard_guard:
        reasons.append("maize yield error >= 0.15")
    if row.get("maize_yield_error") is not None and row["maize_yield_error"] > baseline["maize_yield_error"] + cli.maize_soft_guard_delta:
        reasons.append("maize yield margin worsened too much")
    if row.get("phenology_error_days_max") is None or row["phenology_error_days_max"] > 6:
        reasons.append("phenology_error_days_max > 6")
    if row.get("total_biomass_error") is not None and row["total_biomass_error"] > baseline["total_biomass_error"] * (1 + cli.biomass_worsen_frac):
        reasons.append("total_biomass worsened too much")
    if row.get("structure_error") is not None and row["structure_error"] > baseline["structure_error"] * (1 + cli.structure_worsen_frac):
        reasons.append("structure worsened too much")
    return reasons


def write_table(path: Path, rows: list[dict]) -> None:
    fields = sorted({k for r in rows for k in r.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def update_best(best_row: dict, best_result: dict, baseline: dict) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = OUT_ROOT / "backups" / f"before_fraction_fine_update_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ["truth.apsim", "Wheat.xml", "Maize.xml", "metrics.json", "summary_zh.md", "change_manifest.json", "best_selection.json"]:
        p = BEST_DIR / name
        if p.exists():
            shutil.copy2(p, backup_dir / name)

    case_dir = Path(best_row["case_dir"])
    shutil.copy2(case_dir / "truth.apsim", BEST_DIR / "truth.apsim")
    shutil.copy2(case_dir / "Wheat.xml", BEST_DIR / "Wheat.xml")
    shutil.copy2(case_dir / "Maize.xml", BEST_DIR / "Maize.xml")
    shutil.copy2(BEST_DIR / "truth.apsim", CODEX_TRUTH)
    sync_model_xml(search.base.read_text(BEST_DIR / "Wheat.xml"), search.base.read_text(BEST_DIR / "Maize.xml"))

    metrics = {
        "iteration": f"fraction_fine_{ts}",
        "source": "local_fraction_fine_output_sobol.py",
        "baseline": baseline,
        "candidate_row": best_row,
        "candidate_custom": best_result["custom"],
        "candidate_water_yield": best_result["water"],
        "comparison": {
            "soil_water_delta": best_row["soil_water_error"] - baseline["soil_water_error"],
            "maize_yield_delta": best_row["maize_yield_error"] - baseline["maize_yield_error"],
        },
    }
    search.base.write_text(BEST_DIR / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
    selection = {
        "iteration": metrics["iteration"],
        "source": "local_fraction_fine_output_sobol.py",
        "case_id": best_row["case_id"],
        "FractionFull": best_row["FractionFull"],
        "crit_fr_asw": best_row["crit_fr_asw"],
        "soil_water_error": best_row["soil_water_error"],
        "wheat_yield_error": best_row["wheat_yield_error"],
        "maize_yield_error": best_row["maize_yield_error"],
        "phenology_error_days_max": best_row["phenology_error_days_max"],
        "backup_dir": str(backup_dir),
    }
    search.base.write_text(BEST_DIR / "best_selection.json", json.dumps(selection, ensure_ascii=False, indent=2))
    manifest = {
        "optimization_mode": "fraction_fine_local_search",
        "forbidden_changes": ["HDSW", "soil_physical_properties", "soil_replacement", "irrigation", "cultivar", "fertilizer", "sowing_density", "weather"],
        "changed": {
            "InitialWater.FractionFull": {"from": baseline["FractionFull"], "to": best_row["FractionFull"]},
            "crit_fr_asw": {"from": baseline["crit_fr_asw"], "to": best_row["crit_fr_asw"]},
        },
        "backup_dir": str(backup_dir),
    }
    search.base.write_text(BEST_DIR / "change_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    summary = [
        "# fraction fine local search best update",
        "",
        f"- FractionFull: {baseline['FractionFull']} -> {best_row['FractionFull']}",
        f"- crit_fr_asw: {baseline['crit_fr_asw']} -> {best_row['crit_fr_asw']}",
        f"- soil_water error: {baseline['soil_water_error']} -> {best_row['soil_water_error']}",
        f"- wheat yield error: {baseline['wheat_yield_error']} -> {best_row['wheat_yield_error']}",
        f"- maize yield error: {baseline['maize_yield_error']} -> {best_row['maize_yield_error']}",
        f"- phenology_error_days_max: {baseline['phenology_error_days_max']} -> {best_row['phenology_error_days_max']}",
        f"- backup_dir: {backup_dir}",
    ]
    search.base.write_text(BEST_DIR / "summary_zh.md", "\n".join(summary))
    return backup_dir


def main():
    cli = parse_args()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"fraction_fine_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    args_obj = EvalArgs()
    weights = group_weights()
    truth_obj = search.load_truth_observations(VALIDATION_CSV)

    best_truth = search.base.read_text(BEST_DIR / "truth.apsim")
    best_wheat = search.base.read_text(BEST_DIR / "Wheat.xml")
    best_maize = search.base.read_text(BEST_DIR / "Maize.xml")
    base_iw, base_crit = parse_iw_and_crit(best_truth)
    if abs(base_crit - 0.56) > 1e-9:
        raise ValueError(f"crit_fr_asw is {base_crit}, expected fixed value 0.56.")

    baseline_result, baseline_rows = evaluate_case(out_dir / "baseline_current_best", best_truth, best_wheat, best_maize, truth_obj, args_obj, weights)
    baseline = flatten(0, "baseline_current_best", out_dir / "baseline_current_best", best_truth, baseline_result, baseline_rows)

    rows, results = [], {}
    for i, value in enumerate(FRACTION_VALUES, start=1):
        truth = set_fraction_fixed_crit(best_truth, value)
        case_name = f"fraction_{value:.3f}"
        result, pred_rows = evaluate_case(out_dir / case_name, truth, best_wheat, best_maize, truth_obj, args_obj, weights)
        row = flatten(i, case_name, out_dir / case_name, truth, result, pred_rows)
        reasons = reject_reasons(row, baseline, cli)
        row["accepted"] = not reasons
        row["reject_reason"] = "; ".join(reasons) if reasons else "accepted"
        rows.append(row)
        results[i] = result

    accepted = [r for r in rows if r["accepted"]]
    best_candidate = None
    if accepted:
        best_candidate = sorted(accepted, key=lambda r: (r["soil_water_error"], r["maize_yield_error"]))[0]

    backup_dir = None
    if best_candidate is not None and cli.update_best:
        backup_dir = update_best(best_candidate, results[best_candidate["case_id"]], baseline)

    write_table(out_dir / "fraction_fine_results.csv", rows)
    summary = {
        "output_dir": str(out_dir),
        "baseline": baseline,
        "values_tested": FRACTION_VALUES,
        "n_cases": len(rows),
        "best_candidate": best_candidate,
        "best_updated": bool(best_candidate is not None and cli.update_best),
        "backup_dir": str(backup_dir) if backup_dir else None,
    }
    search.base.write_text(out_dir / "fraction_fine_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))
    lines = [
        "# fraction fine local search summary",
        "",
        f"- output_dir: {out_dir}",
        f"- baseline FractionFull: {baseline['FractionFull']}",
        f"- baseline soil_water: {baseline['soil_water_error']}",
        f"- baseline maize yield error: {baseline['maize_yield_error']}",
        f"- tested values: {', '.join(str(v) for v in FRACTION_VALUES)}",
        f"- best updated: {summary['best_updated']}",
    ]
    if best_candidate:
        lines.extend(
            [
                "",
                "## Best candidate",
                f"- FractionFull: {best_candidate['FractionFull']}",
                f"- soil_water: {best_candidate['soil_water_error']}",
                f"- wheat yield error: {best_candidate['wheat_yield_error']}",
                f"- maize yield error: {best_candidate['maize_yield_error']}",
                f"- phenology days max: {best_candidate['phenology_error_days_max']}",
                f"- total_biomass: {best_candidate['total_biomass_error']}",
                f"- structure: {best_candidate['structure_error']}",
            ]
        )
    else:
        lines.extend(["", "## Best candidate", "- None accepted under current guards."])
    search.base.write_text(out_dir / "fraction_fine_summary.md", "\n".join(lines))

    # Restore global model XML to current best after all trials.
    sync_model_xml(search.base.read_text(BEST_DIR / "Wheat.xml"), search.base.read_text(BEST_DIR / "Maize.xml"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
