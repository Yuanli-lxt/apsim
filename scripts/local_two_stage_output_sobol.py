#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic local optimization around output_sobol/best.

This script is intentionally narrow:
- no HDSW soil
- no soil physical property replacement
- no irrigation search
- Phase A: deterministic InitialWater.FractionFull grid with crit_fr_asw fixed
- Phase B: only if Phase A fails, tiny maize cultivar tests based on accepted history
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import run_process_bio_search as search


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "output_sobol"
BEST_DIR = OUT_ROOT / "best"
VALIDATION_CSV = ROOT / "independent_validation_observations_p02_maize_p01_wheat.csv"
CODEX_TRUTH = ROOT.parent / "codex" / "truth.apsim"

BASE_SOIL = 0.2547055
BASE_WHEAT_YIELD = 0.1062
BASE_MAIZE_YIELD = 0.1481
BASE_PHENO_DAYS = 6.0

PHASE_A_VALUES = [0.548, 0.550, 0.552, 0.554, 0.556, 0.558]
PHASE_A_FINE_VALUES = [0.553, 0.554, 0.555, 0.556, 0.557]


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
    min_soil_water_improvement = 0.0001
    max_total_irrigation_mm = 240.0


def parse_args():
    p = argparse.ArgumentParser(description="Two-stage deterministic local search for output_sobol best.")
    p.add_argument("--update_best", action="store_true")
    p.add_argument("--maize_margin_guard", type=float, default=0.147)
    p.add_argument("--maize_hard_guard", type=float, default=0.15)
    p.add_argument("--min_soil_improvement", type=float, default=0.0002)
    p.add_argument("--allow_phase_b", action=argparse.BooleanOptionalAction, default=True)
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


def read_best_texts():
    return {
        "truth": search.base.read_text(BEST_DIR / "truth.apsim"),
        "wheat": search.base.read_text(BEST_DIR / "Wheat.xml"),
        "maize": search.base.read_text(BEST_DIR / "Maize.xml"),
    }


def parse_iw_and_crit(truth_text: str) -> tuple[float, float]:
    iw = search.parse_initial_water_config(truth_text)["fraction_full"]
    crit_nodes = search.parse_crit_fr_asw(truth_text)
    if not crit_nodes:
        raise ValueError("No crit_fr_asw node found.")
    return float(iw), float(crit_nodes[0]["value"])


def set_fraction_and_crit(truth_text: str, fraction_full: float, crit: float = 0.56) -> str:
    cfg = search.parse_initial_water_config(truth_text)
    cfg["fraction_full"] = float(fraction_full)
    out = search.set_initial_water_config(truth_text, cfg)
    nodes = search.parse_crit_fr_asw(out)
    if not nodes:
        raise ValueError("No crit_fr_asw node found after setting FractionFull.")
    out = search.set_crit_fr_asw(out, nodes[0], float(crit))
    return out


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


def flatten(case_id: int, phase: str, case_name: str, case_dir: Path, truth_text: str, maize_cultivar: str, maize_param: str | None, old_value, new_value, result: dict, rows: list[dict]) -> dict:
    iw, crit = parse_iw_and_crit(truth_text)
    water = result["water"]
    custom = result["custom"]
    out = {
        "case_id": case_id,
        "phase": phase,
        "case_name": case_name,
        "case_dir": str(case_dir),
        "FractionFull": iw,
        "crit_fr_asw": crit,
        "maize_cultivar": maize_cultivar,
        "maize_param": maize_param,
        "old_value": old_value,
        "new_value": new_value,
        "soil_water_error": water.get("soil_water_error"),
        "water_yield_score": water.get("water_yield_score"),
        "wheat_yield_error": water.get("wheat_yield_error"),
        "maize_yield_error": water.get("maize_yield_error"),
        "phenology_error_days_max": water.get("phenology_error_days_max"),
        "total_biomass_error": water.get("total_biomass_error"),
        "structure_error": water.get("structure_error"),
        "custom_score": custom.get("custom_score"),
    }
    out.update(layer_summary(rows))
    return out


def reject_reasons(row: dict, baseline: dict, cli, stage: str) -> list[str]:
    reasons = []
    if row.get("soil_water_error") is None:
        reasons.append("missing soil_water error")
    if row.get("wheat_yield_error") is None or row["wheat_yield_error"] >= 0.15:
        reasons.append("wheat yield error >= 0.15")
    if row.get("maize_yield_error") is None or row["maize_yield_error"] >= cli.maize_hard_guard:
        reasons.append("maize yield error >= 0.15")
    if row.get("phenology_error_days_max") is None or row["phenology_error_days_max"] > 6:
        reasons.append("phenology_error_days_max > 6")
    if row.get("total_biomass_error") is not None and row["total_biomass_error"] > baseline["total_biomass_error"] * 1.03:
        reasons.append("total_biomass clearly worsened")
    if row.get("structure_error") is not None and row["structure_error"] > baseline["structure_error"] * 1.03:
        reasons.append("structure clearly worsened")

    if stage == "fraction_grid":
        if row.get("soil_water_error") is None or row["soil_water_error"] >= baseline["soil_water_error"] - cli.min_soil_improvement:
            reasons.append("soil_water not clearly below current best")
        if row.get("maize_yield_error") is not None and row["maize_yield_error"] > BASE_MAIZE_YIELD:
            reasons.append("maize yield error worse than current best")
        if row.get("maize_yield_error") is not None and row["maize_yield_error"] > cli.maize_margin_guard:
            reasons.append(f"maize yield margin not safe (>{cli.maize_margin_guard})")
    elif stage == "maize_margin":
        if row.get("maize_yield_error") is None or row["maize_yield_error"] >= BASE_MAIZE_YIELD:
            reasons.append("maize yield error not lower than current best")
        if row.get("soil_water_error") is not None and row["soil_water_error"] > baseline["soil_water_error"] + 0.003:
            reasons.append("soil_water clearly worsened")
    return reasons


def choose_best(rows: list[dict], baseline: dict, cli, stage: str) -> dict | None:
    candidates = []
    for r in rows:
        reasons = reject_reasons(r, baseline, cli, stage)
        r["accepted"] = not reasons
        r["reject_reason"] = "; ".join(reasons) if reasons else "accepted"
        if not reasons:
            candidates.append(r)
    if not candidates:
        return None
    if stage == "fraction_grid":
        return sorted(candidates, key=lambda r: (r["soil_water_error"], r["maize_yield_error"]))[0]
    return sorted(candidates, key=lambda r: (r["maize_yield_error"], r["soil_water_error"]))[0]


def make_maize_variant(maize_text: str, base_name: str, param: str, new_value: float, case_id: int) -> tuple[str, str]:
    params = search.base.parse_maize_params(maize_text, base_name)
    if param not in params:
        raise ValueError(f"Maize parameter not found: {param}")
    params[param] = float(new_value)
    new_name = f"{base_name}_local_{param}_{case_id:03d}".replace(".", "p").replace("-", "m")
    return search.base.append_maize_cultivar(maize_text, new_name, params), new_name


def update_best(best_row: dict, best_result: dict, out_dir: Path, baseline: dict, base_texts: dict, rows_for_best: list[dict]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = OUT_ROOT / "backups" / f"before_two_stage_update_{ts}"
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
        "iteration": f"two_stage_local_{ts}",
        "source": "local_two_stage_output_sobol.py",
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
        "source": "local_two_stage_output_sobol.py",
        "phase": best_row["phase"],
        "case_id": best_row["case_id"],
        "FractionFull": best_row["FractionFull"],
        "crit_fr_asw": best_row["crit_fr_asw"],
        "maize_cultivar": best_row["maize_cultivar"],
        "maize_param": best_row["maize_param"],
        "old_value": best_row["old_value"],
        "new_value": best_row["new_value"],
        "soil_water_error": best_row["soil_water_error"],
        "wheat_yield_error": best_row["wheat_yield_error"],
        "maize_yield_error": best_row["maize_yield_error"],
        "phenology_error_days_max": best_row["phenology_error_days_max"],
        "backup_dir": str(backup_dir),
    }
    search.base.write_text(BEST_DIR / "best_selection.json", json.dumps(selection, ensure_ascii=False, indent=2))
    manifest = {
        "optimization_mode": "two_stage_local_search",
        "accepted_phase": best_row["phase"],
        "forbidden_changes": ["HDSW", "soil_physical_properties", "soil_replacement", "irrigation_search", "fertilizer", "sowing_density", "weather"],
        "changed": {
            "InitialWater.FractionFull": {
                "from": baseline["FractionFull"],
                "to": best_row["FractionFull"],
            },
            "crit_fr_asw": {
                "from": baseline["crit_fr_asw"],
                "to": best_row["crit_fr_asw"],
            },
            "maize_param": {
                "parameter": best_row["maize_param"],
                "from": best_row["old_value"],
                "to": best_row["new_value"],
            },
        },
        "backup_dir": str(backup_dir),
    }
    search.base.write_text(BEST_DIR / "change_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    summary = [
        "# two-stage local search best update",
        "",
        f"- accepted phase: {best_row['phase']}",
        f"- case_id: {best_row['case_id']}",
        f"- FractionFull: {baseline['FractionFull']} -> {best_row['FractionFull']}",
        f"- crit_fr_asw: {baseline['crit_fr_asw']} -> {best_row['crit_fr_asw']}",
        f"- maize cultivar: {best_row['maize_cultivar']}",
        f"- maize param: {best_row['maize_param']} {best_row['old_value']} -> {best_row['new_value']}",
        f"- soil_water error: {baseline['soil_water_error']} -> {best_row['soil_water_error']}",
        f"- wheat yield error: {baseline['wheat_yield_error']} -> {best_row['wheat_yield_error']}",
        f"- maize yield error: {baseline['maize_yield_error']} -> {best_row['maize_yield_error']}",
        f"- phenology_error_days_max: {baseline['phenology_error_days_max']} -> {best_row['phenology_error_days_max']}",
        f"- backup_dir: {backup_dir}",
    ]
    search.base.write_text(BEST_DIR / "summary_zh.md", "\n".join(summary))
    return backup_dir


def write_table(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fields = sorted({k for r in rows for k in r.keys()})
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    cli = parse_args()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"two_stage_local_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    args_obj = EvalArgs()
    weights = group_weights()
    truth_obj = search.load_truth_observations(VALIDATION_CSV)
    base_texts = read_best_texts()
    base_iw, base_crit = parse_iw_and_crit(base_texts["truth"])
    if abs(base_crit - 0.56) > 1e-9:
        raise ValueError(f"Current crit_fr_asw is {base_crit}, expected 0.56.")

    baseline_result, baseline_rows = evaluate_case(out_dir / "baseline_current_best", base_texts["truth"], base_texts["wheat"], base_texts["maize"], truth_obj, args_obj, weights)
    base_maize_name = search.base.get_manager_cultivar(base_texts["truth"], "Maize Management")
    baseline = flatten(0, "baseline", "baseline_current_best", out_dir / "baseline_current_best", base_texts["truth"], base_maize_name, None, None, None, baseline_result, baseline_rows)

    all_rows = []
    result_by_case = {}
    row_rows = {}
    case_id = 1

    # Phase A coarse grid.
    for value in PHASE_A_VALUES:
        case_name = f"A_fraction_{value:.3f}"
        truth = set_fraction_and_crit(base_texts["truth"], value, 0.56)
        result, rows = evaluate_case(out_dir / case_name, truth, base_texts["wheat"], base_texts["maize"], truth_obj, args_obj, weights)
        row = flatten(case_id, "A_fraction_grid", case_name, out_dir / case_name, truth, base_maize_name, None, None, None, result, rows)
        all_rows.append(row)
        result_by_case[case_id] = result
        row_rows[case_id] = rows
        case_id += 1

    phase_a_rows = [r for r in all_rows if r["phase"] == "A_fraction_grid"]
    phase_a_best = choose_best(phase_a_rows, baseline, cli, "fraction_grid")

    # If any direction improves soil, add fine grid near the best direction.
    fine_best = None
    best_soil_row = min(phase_a_rows, key=lambda r: 999 if r["soil_water_error"] is None else r["soil_water_error"])
    if best_soil_row["soil_water_error"] is not None and best_soil_row["soil_water_error"] < baseline["soil_water_error"]:
        fine_values = PHASE_A_FINE_VALUES
        if best_soil_row["FractionFull"] < base_iw:
            fine_values = [0.548, 0.549, 0.550, 0.551, 0.552, 0.553, 0.554]
        elif best_soil_row["FractionFull"] > base_iw:
            fine_values = [0.554, 0.555, 0.556, 0.557, 0.558, 0.559, 0.560]
        for value in fine_values:
            case_name = f"A_fine_fraction_{value:.3f}"
            truth = set_fraction_and_crit(base_texts["truth"], value, 0.56)
            result, rows = evaluate_case(out_dir / case_name, truth, base_texts["wheat"], base_texts["maize"], truth_obj, args_obj, weights)
            row = flatten(case_id, "A_fraction_fine", case_name, out_dir / case_name, truth, base_maize_name, None, None, None, result, rows)
            all_rows.append(row)
            result_by_case[case_id] = result
            row_rows[case_id] = rows
            case_id += 1
        fine_rows = [r for r in all_rows if r["phase"] == "A_fraction_fine"]
        fine_best = choose_best(fine_rows, baseline, cli, "fraction_grid")

    best_candidate = fine_best or phase_a_best

    # Phase B only if no acceptable Phase A result.
    phase_b_best = None
    if best_candidate is None and cli.allow_phase_b:
        params = search.base.parse_maize_params(base_texts["maize"], base_maize_name)
        test_plan = []
        for param, rel_steps in {
            "tt_endjuv_to_init": [-0.01, 0.01],
            "tt_flower_to_maturity": [-0.01, 0.01],
            "tt_flag_to_flower": [-0.01, 0.01],
        }.items():
            if param in params:
                for rel in rel_steps:
                    test_plan.append((param, params[param], params[param] * (1.0 + rel)))
        for param, old, new in test_plan:
            maize_variant, new_name = make_maize_variant(base_texts["maize"], base_maize_name, param, new, case_id)
            truth = search.base.set_manager_cultivar(base_texts["truth"], "Maize Management", new_name)
            truth = set_fraction_and_crit(truth, base_iw, 0.56)
            case_name = f"B_maize_{param}_{new:.4f}".replace(".", "p")
            result, rows = evaluate_case(out_dir / case_name, truth, base_texts["wheat"], maize_variant, truth_obj, args_obj, weights)
            row = flatten(case_id, "B_maize_margin", case_name, out_dir / case_name, truth, new_name, param, old, new, result, rows)
            all_rows.append(row)
            result_by_case[case_id] = result
            row_rows[case_id] = rows
            case_id += 1
        phase_b_rows = [r for r in all_rows if r["phase"] == "B_maize_margin"]
        phase_b_best = choose_best(phase_b_rows, baseline, cli, "maize_margin")
        best_candidate = phase_b_best

        # Phase C: if maize safety margin is created, redo FractionFull grid with that maize variant.
        if phase_b_best is not None and phase_b_best["maize_yield_error"] <= 0.145:
            b_case = Path(phase_b_best["case_dir"])
            b_truth = search.base.read_text(b_case / "truth.apsim")
            b_maize = search.base.read_text(b_case / "Maize.xml")
            c_rows = []
            for value in PHASE_A_VALUES:
                truth = set_fraction_and_crit(b_truth, value, 0.56)
                case_name = f"C_fraction_after_maize_{value:.3f}"
                result, rows = evaluate_case(out_dir / case_name, truth, base_texts["wheat"], b_maize, truth_obj, args_obj, weights)
                row = flatten(case_id, "C_fraction_after_maize", case_name, out_dir / case_name, truth, phase_b_best["maize_cultivar"], phase_b_best["maize_param"], phase_b_best["old_value"], phase_b_best["new_value"], result, rows)
                all_rows.append(row)
                c_rows.append(row)
                result_by_case[case_id] = result
                row_rows[case_id] = rows
                case_id += 1
            c_best = choose_best(c_rows, baseline, cli, "fraction_grid")
            if c_best is not None:
                best_candidate = c_best

    # Mark acceptance/rejection for all rows.
    for r in all_rows:
        stage = "maize_margin" if r["phase"] == "B_maize_margin" else "fraction_grid"
        if r["phase"] == "C_fraction_after_maize":
            stage = "fraction_grid"
        reasons = reject_reasons(r, baseline, cli, stage)
        r["accepted"] = not reasons
        r["reject_reason"] = "; ".join(reasons) if reasons else "accepted"

    backup_dir = None
    if best_candidate is not None and cli.update_best:
        backup_dir = update_best(best_candidate, result_by_case[best_candidate["case_id"]], out_dir, baseline, base_texts, row_rows[best_candidate["case_id"]])

    write_table(out_dir / "two_stage_results.csv", all_rows)
    summary = {
        "output_dir": str(out_dir),
        "baseline": baseline,
        "n_cases": len(all_rows),
        "best_candidate": best_candidate,
        "best_updated": bool(best_candidate is not None and cli.update_best),
        "backup_dir": str(backup_dir) if backup_dir else None,
    }
    search.base.write_text(out_dir / "two_stage_summary.json", json.dumps(summary, ensure_ascii=False, indent=2))

    lines = [
        "# two-stage local search summary",
        "",
        f"- output_dir: {out_dir}",
        f"- baseline FractionFull: {baseline['FractionFull']}",
        f"- baseline crit_fr_asw: {baseline['crit_fr_asw']}",
        f"- baseline soil_water: {baseline['soil_water_error']}",
        f"- baseline maize yield error: {baseline['maize_yield_error']}",
        f"- tested cases: {len(all_rows)}",
        f"- best updated: {bool(best_candidate is not None and cli.update_best)}",
    ]
    if best_candidate:
        lines += [
            "",
            "## Best candidate",
            f"- phase: {best_candidate['phase']}",
            f"- case_id: {best_candidate['case_id']}",
            f"- FractionFull: {best_candidate['FractionFull']}",
            f"- crit_fr_asw: {best_candidate['crit_fr_asw']}",
            f"- maize_param: {best_candidate['maize_param']}",
            f"- old_value -> new_value: {best_candidate['old_value']} -> {best_candidate['new_value']}",
            f"- soil_water: {best_candidate['soil_water_error']}",
            f"- wheat yield error: {best_candidate['wheat_yield_error']}",
            f"- maize yield error: {best_candidate['maize_yield_error']}",
            f"- phenology days max: {best_candidate['phenology_error_days_max']}",
            f"- total_biomass: {best_candidate['total_biomass_error']}",
            f"- structure: {best_candidate['structure_error']}",
        ]
    else:
        lines += ["", "## Best candidate", "- None accepted under current guards."]
    search.base.write_text(out_dir / "two_stage_summary.md", "\n".join(lines))
    sync_model_xml(search.base.read_text(BEST_DIR / "Wheat.xml"), search.base.read_text(BEST_DIR / "Maize.xml"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
