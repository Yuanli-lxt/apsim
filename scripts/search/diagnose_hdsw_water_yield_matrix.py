#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HDSW soil 水分-产量诊断矩阵。

目的：
1. 不修改 soil physical properties。
2. 不修改 fertilizer / sowing density / observations。
3. 只测试 InitialWater.FractionFull、crit_fr_asw 和灌溉模式组合。
4. 找出 HDSW soil 下 maize yield = 0 的主要触发原因。
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

import run_process_bio_search as search


ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "output_hdsw_sobol_water_yield"
BEST_DIR = OUT_ROOT / "best"
VALIDATION_CSV = ROOT / "data" / "processed" / "observations" / "independent_validation_observations_p02_maize_p01_wheat.csv"


INITIAL_WATER_VALUES = [0.10, 0.30, 0.50, 0.70]
CRIT_FR_ASW_VALUES = [0.05, 0.20, 0.40, 0.60]
IRRIGATION_MODES = ["off", "threshold", "fixed_small"]


class Args:
    pheno_guard_days = 10
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
    soil_water_priority_weight = 0.60
    yield_constraint_weight = 0.25
    phenology_constraint_weight = 0.10
    stability_weight = 0.05


def _group_weights() -> dict:
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


def apply_irrigation_mode(truth_text: str, mode: str, crit: float) -> tuple[str, dict]:
    cfg = search.parse_irrigation_config(truth_text)
    cfg["crit_fr_asw"] = crit
    cfg["threshold"]["trigger_below"] = crit
    if mode == "off":
        cfg["mode"] = "off"
        cfg["automatic_irrigation"] = "off"
        cfg["fixed_enabled"] = "no"
        cfg["events"] = []
    elif mode == "threshold":
        cfg["mode"] = "threshold"
        cfg["automatic_irrigation"] = "on"
        cfg["fixed_enabled"] = "no"
        cfg["events"] = []
    elif mode == "fixed_small":
        cfg["mode"] = "fixed_small"
        cfg["automatic_irrigation"] = "off"
        cfg["fixed_enabled"] = "yes"
        cfg["events"] = [
            {"date": "2025-04-25", "amount_mm": 20.0},
            {"date": "2025-08-17", "amount_mm": 20.0},
        ]
    else:
        raise ValueError(f"未知灌溉模式: {mode}")
    return search.set_irrigation_config(truth_text, cfg), cfg


def summarize_layers(rows: list[dict]) -> dict:
    out = {}
    for layer in ("water_1", "water_2", "water_3", "water_4", "water_5"):
        rels, signed = [], []
        for r in rows:
            if r.get("scenario") != "candidate" or r.get("variable") != layer:
                continue
            try:
                sim = float(r.get("sim_value"))
                obs = float(r.get("obs_value"))
                rel = float(r.get("rel_error"))
            except Exception:
                continue
            rels.append(rel)
            signed.append(sim - obs)
        out[layer] = {
            "mean_rel_error": search.mean_valid(rels),
            "mean_signed_error": search.mean_valid(signed),
            "n": len(rels),
        }
    return out


def run_one(case_dir: Path, truth_text: str, truth_obj: dict, args: Args, group_weights: dict) -> dict:
    case_dir.mkdir(parents=True, exist_ok=True)
    search.base.write_text(case_dir / "truth.apsim", truth_text)
    shutil.copy2(BEST_DIR / "Wheat.xml", case_dir / "Wheat.xml")
    shutil.copy2(BEST_DIR / "Maize.xml", case_dir / "Maize.xml")
    search.base.run_apsim_on_truth(case_dir / "truth.apsim")
    eval_obj = search.base.evaluate_output_dir(case_dir, truth_obj)
    rows = search.collect_prediction_vs_truth_rows(case_dir, truth_obj, "candidate")
    pheno_diag = search._build_pheno_diag(eval_obj, search.extract_wheat_anchor_metrics(case_dir, truth_obj, args.wheat_late_stage_cn))
    custom = search.score_all_truth_objective(
        rows,
        args.pheno_guard_days,
        pheno_diag,
        weights=group_weights,
        missing_truth_penalty=args.missing_truth_penalty,
        rel_error_cap=args.truth_rel_error_cap,
    )
    hdsw_score = search.score_hdsw_water_yield_objective(eval_obj, custom, args)
    layer = summarize_layers(rows)
    harvest = case_dir / "Rotation Sample Harvest.out"
    return {
        "case_dir": str(case_dir),
        "apsim_passed": harvest.exists(),
        "custom_score": custom.get("custom_score"),
        "hdsw_water_yield_score": hdsw_score.get("hdsw_water_yield_score"),
        "soil_water_error": hdsw_score.get("soil_water_error"),
        "wheat_yield_error": hdsw_score.get("wheat_yield_error"),
        "maize_yield_error": hdsw_score.get("maize_yield_error"),
        "wheat_yield_sim_kg_ha": (eval_obj.get("crops", {}).get("wheat", {}) or {}).get("yield_sim_kg_ha"),
        "maize_yield_sim_kg_ha": (eval_obj.get("crops", {}).get("maize", {}) or {}).get("yield_sim_kg_ha"),
        "phenology_error_days_max": hdsw_score.get("phenology_error_days_max"),
        "total_biomass_error": hdsw_score.get("total_biomass_error"),
        "structure_error": hdsw_score.get("structure_error"),
        "yield_constraint_passed": hdsw_score.get("yield_constraint_passed"),
        "phenology_passed": hdsw_score.get("phenology_passed"),
        "water_1_error": (layer.get("water_1") or {}).get("mean_rel_error"),
        "water_2_error": (layer.get("water_2") or {}).get("mean_rel_error"),
        "water_3_error": (layer.get("water_3") or {}).get("mean_rel_error"),
        "water_4_error": (layer.get("water_4") or {}).get("mean_rel_error"),
        "water_5_error": (layer.get("water_5") or {}).get("mean_rel_error"),
        "water_1_signed": (layer.get("water_1") or {}).get("mean_signed_error"),
        "water_2_signed": (layer.get("water_2") or {}).get("mean_signed_error"),
        "water_3_signed": (layer.get("water_3") or {}).get("mean_signed_error"),
        "water_4_signed": (layer.get("water_4") or {}).get("mean_signed_error"),
        "water_5_signed": (layer.get("water_5") or {}).get("mean_signed_error"),
    }


def main() -> None:
    if not (BEST_DIR / "truth.apsim").exists():
        raise FileNotFoundError(f"缺少 HDSW best truth.apsim: {BEST_DIR / 'truth.apsim'}")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"diagnostic_matrix_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    truth_obj = search.load_truth_observations(VALIDATION_CSV)
    args = Args()
    weights = _group_weights()
    base_truth = search.base.read_text(BEST_DIR / "truth.apsim")

    manifest = {
        "timestamp": ts,
        "source_truth": str(BEST_DIR / "truth.apsim"),
        "source_wheat_xml": str(BEST_DIR / "Wheat.xml"),
        "source_maize_xml": str(BEST_DIR / "Maize.xml"),
        "initial_water_values": INITIAL_WATER_VALUES,
        "crit_fr_asw_values": CRIT_FR_ASW_VALUES,
        "irrigation_modes": IRRIGATION_MODES,
        "forbidden_changes": ["weather", "fertilizer", "sowing_density", "soil_physical_properties", "observations"],
    }
    (out_dir / "diagnostic_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    case_no = 0
    for iw in INITIAL_WATER_VALUES:
        for crit in CRIT_FR_ASW_VALUES:
            for irr_mode in IRRIGATION_MODES:
                case_no += 1
                case_name = f"case_{case_no:03d}_iw{iw:.2f}_crit{crit:.2f}_{irr_mode}"
                print(f"[matrix] {case_name}")
                truth = search.set_initial_water_config(base_truth, {"fraction_full": iw})
                truth, irr_cfg = apply_irrigation_mode(truth, irr_mode, crit)
                case_dir = out_dir / case_name
                result = run_one(case_dir, truth, truth_obj, args, weights)
                result.update(
                    {
                        "case_id": case_no,
                        "initial_water_fraction_full": iw,
                        "crit_fr_asw": crit,
                        "irrigation_mode": irr_mode,
                        "total_fixed_irrigation_mm": search.total_irrigation_mm(irr_cfg),
                    }
                )
                rows.append(result)

    fields = list(rows[0].keys()) if rows else []
    with (out_dir / "diagnostic_matrix_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    sorted_rows = sorted(rows, key=lambda r: (r["maize_yield_error"], r["wheat_yield_error"], r["soil_water_error"]))
    best_yield = sorted_rows[:10]
    best_soil = sorted(rows, key=lambda r: (r["soil_water_error"], r["maize_yield_error"], r["wheat_yield_error"]))[:10]
    report = {
        "output_dir": str(out_dir),
        "n_cases": len(rows),
        "best_10_by_yield_recovery": best_yield,
        "best_10_by_soil_water": best_soil,
        "maize_yield_nonzero_cases": sum(1 for r in rows if (r.get("maize_yield_sim_kg_ha") or 0) > 0),
        "yield_constraint_passed_cases": sum(1 for r in rows if r.get("yield_constraint_passed")),
    }
    (out_dir / "diagnostic_matrix_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        "# HDSW water-yield 诊断矩阵结果",
        "",
        f"- cases: {len(rows)}",
        f"- maize_yield_nonzero_cases: {report['maize_yield_nonzero_cases']}",
        f"- yield_constraint_passed_cases: {report['yield_constraint_passed_cases']}",
        "",
        "## Best 10 by yield recovery",
    ]
    for r in best_yield:
        md.append(
            f"- case {r['case_id']}: iw={r['initial_water_fraction_full']}, crit={r['crit_fr_asw']}, "
            f"irr={r['irrigation_mode']}, wheat_yerr={r['wheat_yield_error']:.3f}, "
            f"maize_yerr={r['maize_yield_error']:.3f}, soil={r['soil_water_error']:.3f}, "
            f"wheat_yield={r['wheat_yield_sim_kg_ha']}, maize_yield={r['maize_yield_sim_kg_ha']}"
        )
    md.append("")
    md.append("## Best 10 by soil_water")
    for r in best_soil:
        md.append(
            f"- case {r['case_id']}: iw={r['initial_water_fraction_full']}, crit={r['crit_fr_asw']}, "
            f"irr={r['irrigation_mode']}, soil={r['soil_water_error']:.3f}, "
            f"wheat_yerr={r['wheat_yield_error']:.3f}, maize_yerr={r['maize_yield_error']:.3f}"
        )
    (out_dir / "diagnostic_matrix_summary.md").write_text("\n".join(md), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])


if __name__ == "__main__":
    main()
