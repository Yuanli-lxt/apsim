#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""output_sobol 当前 best 周围的 InitialWater / crit_fr_asw 局部搜索。

只改：
- InitialWater.FractionFull
- irrigation.crit_fr_asw

不改：
- Soil physical properties
- weather / fertilizer / sowing density / crop rotation / tillage / residue
- Wheat.xml / Maize.xml 品种参数
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

SMOKE_DELTAS = [
    (-0.02, 0.0),
    (-0.01, 0.0),
    (0.01, 0.0),
    (0.02, 0.0),
    (0.0, -0.02),
    (0.0, -0.01),
    (0.0, 0.01),
    (0.0, 0.02),
]

FULL_DELTAS = [-0.04, -0.02, -0.01, 0.0, 0.01, 0.02, 0.04]


class Args:
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
    min_soil_water_improvement = 0.001
    max_total_irrigation_mm = 240.0


def parse_args():
    p = argparse.ArgumentParser(description="Local grid around output_sobol best for InitialWater and crit_fr_asw.")
    p.add_argument("--mode", choices=["smoke", "full"], default="smoke")
    p.add_argument("--update_best", action="store_true", help="如果找到合格且更优的 candidate，则更新 output_sobol/best。")
    p.add_argument("--maize_yield_guard", type=float, default=0.149, help="给 maize yield 留一点安全余量，避免贴近 0.15。")
    p.add_argument("--min_soil_improvement", type=float, default=0.001)
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


def set_crit(truth_text: str, value: float) -> str:
    nodes = search.parse_crit_fr_asw(truth_text)
    if not nodes:
        raise ValueError("truth.apsim 中没有 crit_fr_asw")
    return search.set_crit_fr_asw(truth_text, nodes[0], value)


def evaluate_case(case_dir: Path, truth_text: str, truth_obj: dict, args_obj: Args, weights: dict) -> tuple[dict, list[dict]]:
    case_dir.mkdir(parents=True, exist_ok=True)
    search.base.write_text(case_dir / "truth.apsim", truth_text)
    shutil.copy2(BEST_DIR / "Wheat.xml", case_dir / "Wheat.xml")
    shutil.copy2(BEST_DIR / "Maize.xml", case_dir / "Maize.xml")
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
    return {
        "eval": eval_obj,
        "custom": custom,
        "water": water,
    }, rows


def layer_summary(rows: list[dict]) -> dict:
    out = {}
    for layer in ("water_1", "water_2", "water_3", "water_4", "water_5"):
        rels, signed = [], []
        for r in rows:
            if r.get("scenario") != "candidate" or r.get("variable") != layer:
                continue
            try:
                sim = float(r["sim_value"])
                obs = float(r["obs_value"])
                rel = float(r["rel_error"])
            except Exception:
                continue
            rels.append(rel)
            signed.append(sim - obs)
        out[f"{layer}_error"] = search.mean_valid(rels)
        out[f"{layer}_signed"] = search.mean_valid(signed)
    return out


def flatten_result(case_id: int, case_name: str, iw: float, crit: float, base_iw: float, base_crit: float, case_dir: Path, result: dict, rows: list[dict]) -> dict:
    custom = result["custom"]
    water = result["water"]
    eval_obj = result["eval"]
    crops = eval_obj.get("crops") or {}
    row = {
        "case_id": case_id,
        "case_name": case_name,
        "case_dir": str(case_dir),
        "InitialWater.FractionFull": iw,
        "crit_fr_asw": crit,
        "delta_initial_water": iw - base_iw,
        "delta_crit_fr_asw": crit - base_crit,
        "soil_water_error": water.get("soil_water_error"),
        "water_yield_score": water.get("water_yield_score"),
        "wheat_yield_error": water.get("wheat_yield_error"),
        "maize_yield_error": water.get("maize_yield_error"),
        "phenology_error_days_max": water.get("phenology_error_days_max"),
        "total_biomass_error": water.get("total_biomass_error"),
        "structure_error": water.get("structure_error"),
        "yield_constraint_passed": water.get("yield_constraint_passed"),
        "phenology_passed": water.get("phenology_passed"),
        "wheat_yield_sim_kg_ha": (crops.get("wheat") or {}).get("yield_sim_kg_ha"),
        "maize_yield_sim_kg_ha": (crops.get("maize") or {}).get("yield_sim_kg_ha"),
        "custom_score": custom.get("custom_score"),
    }
    row.update(layer_summary(rows))
    return row


def acceptable(row: dict, baseline: dict, cli) -> tuple[bool, str]:
    reasons = []
    if row["soil_water_error"] is None or row["soil_water_error"] > baseline["soil_water_error"] - cli.min_soil_improvement:
        reasons.append("soil_water 改善不足")
    if row["wheat_yield_error"] is None or row["wheat_yield_error"] >= 0.15:
        reasons.append("wheat yield error 未小于 0.15")
    if row["maize_yield_error"] is None or row["maize_yield_error"] >= cli.maize_yield_guard:
        reasons.append(f"maize yield error 未保留安全余量 < {cli.maize_yield_guard}")
    if row["phenology_error_days_max"] is None or row["phenology_error_days_max"] > 6:
        reasons.append("phenology_error_days_max > 6")
    if row["total_biomass_error"] is not None and row["total_biomass_error"] > baseline["total_biomass_error"] * 1.03:
        reasons.append("total_biomass 明显恶化")
    if row["structure_error"] is not None and row["structure_error"] > baseline["structure_error"] * 1.03:
        reasons.append("structure 明显恶化")
    return (not reasons), "；".join(reasons) if reasons else "通过：soil_water 改善，yield/phenology/biomass/structure 守门均满足"


def backup_and_update_best(best_row: dict, baseline_truth: str, out_dir: Path, metrics_bundle: dict, rows: list[dict]) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = OUT_ROOT / "backups" / f"before_local_iwcrit_update_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ["truth.apsim", "Wheat.xml", "Maize.xml", "metrics.json", "summary_zh.md", "change_manifest.json", "best_selection.json"]:
        p = BEST_DIR / name
        if p.exists():
            shutil.copy2(p, backup_dir / name)

    candidate_truth = Path(best_row["case_dir"]) / "truth.apsim"
    shutil.copy2(candidate_truth, BEST_DIR / "truth.apsim")
    shutil.copy2(BEST_DIR / "truth.apsim", search.base.TRUTH_PATH)
    # XML 不变，但同步一次，保证下一轮脚本从 best 恢复。
    shutil.copy2(BEST_DIR / "Wheat.xml", search.base.WHEAT_PATH)
    shutil.copy2(BEST_DIR / "Maize.xml", search.base.MAIZE_PATH)

    metrics = {
        "iteration": f"local_iwcrit_{ts}",
        "baseline_custom": metrics_bundle["baseline"]["custom"],
        "candidate_custom": metrics_bundle["candidate"]["custom"],
        "baseline_water_yield": metrics_bundle["baseline"]["water"],
        "candidate_water_yield": metrics_bundle["candidate"]["water"],
        "candidate": metrics_bundle["candidate"]["eval"],
        "comparison": {
            "local_search_case_id": best_row["case_id"],
            "soil_water_delta": best_row["soil_water_error"] - metrics_bundle["baseline"]["water"]["soil_water_error"],
            "accepted_by_local_iwcrit": True,
        },
    }
    (BEST_DIR / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    (BEST_DIR / "best_selection.json").write_text(
        json.dumps(
            {
                "iteration": metrics["iteration"],
                "source": "local_search_output_sobol_iw_crit.py",
                "case_id": best_row["case_id"],
                "InitialWater.FractionFull": best_row["InitialWater.FractionFull"],
                "crit_fr_asw": best_row["crit_fr_asw"],
                "soil_water_error": best_row["soil_water_error"],
                "wheat_yield_error": best_row["wheat_yield_error"],
                "maize_yield_error": best_row["maize_yield_error"],
                "phenology_error_days_max": best_row["phenology_error_days_max"],
                "backup_dir": str(backup_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "optimization_mode": "local_iwcrit_search",
        "changed_parameters": {
            "InitialWater.FractionFull": {
                "from": metrics_bundle["baseline_values"]["initial_water"],
                "to": best_row["InitialWater.FractionFull"],
            },
            "crit_fr_asw": {
                "from": metrics_bundle["baseline_values"]["crit_fr_asw"],
                "to": best_row["crit_fr_asw"],
            },
        },
        "forbidden_changes": ["soil_physical_properties", "weather", "fertilizer", "sowing_density", "rotation", "tillage", "residue"],
        "acceptance_reason": best_row.get("acceptance_reason"),
        "backup_dir": str(backup_dir),
    }
    (BEST_DIR / "change_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = [
        "# local_iwcrit_search best update",
        "",
        f"- case_id: {best_row['case_id']}",
        f"- InitialWater.FractionFull: {metrics_bundle['baseline_values']['initial_water']} -> {best_row['InitialWater.FractionFull']}",
        f"- crit_fr_asw: {metrics_bundle['baseline_values']['crit_fr_asw']} -> {best_row['crit_fr_asw']}",
        f"- soil_water error: {metrics_bundle['baseline']['water']['soil_water_error']} -> {best_row['soil_water_error']}",
        f"- wheat yield error: {best_row['wheat_yield_error']}",
        f"- maize yield error: {best_row['maize_yield_error']}",
        f"- phenology_error_days_max: {best_row['phenology_error_days_max']}",
        f"- acceptance: {best_row.get('acceptance_reason')}",
        f"- backup_dir: {backup_dir}",
    ]
    (BEST_DIR / "summary_zh.md").write_text("\n".join(summary), encoding="utf-8")
    with (BEST_DIR / "prediction_vs_truth.csv").open("w", encoding="utf-8-sig", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    return backup_dir


def main() -> None:
    cli = parse_args()
    if not (BEST_DIR / "truth.apsim").exists():
        raise FileNotFoundError(f"缺少 best truth: {BEST_DIR / 'truth.apsim'}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / f"local_iwcrit_{cli.mode}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 确保 APSIM 运行时使用当前 best 的 XML，避免品种不存在。
    shutil.copy2(BEST_DIR / "Wheat.xml", search.base.WHEAT_PATH)
    shutil.copy2(BEST_DIR / "Maize.xml", search.base.MAIZE_PATH)

    args_obj = Args()
    args_obj.min_soil_water_improvement = cli.min_soil_improvement
    truth_obj = search.load_truth_observations(VALIDATION_CSV)
    weights = group_weights()
    base_truth = search.base.read_text(BEST_DIR / "truth.apsim")
    base_iw = search.parse_initial_water_config(base_truth)["fraction_full"]
    base_crit = search.parse_crit_fr_asw(base_truth)[0]["value"]

    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "mode": cli.mode,
                "source_best": str(BEST_DIR),
                "base_initial_water": base_iw,
                "base_crit_fr_asw": base_crit,
                "soil_policy": "keep output_sobol soil unchanged; only edit InitialWater.FractionFull and crit_fr_asw",
                "maize_yield_guard": cli.maize_yield_guard,
                "min_soil_improvement": cli.min_soil_improvement,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    baseline_dir = out_dir / "baseline_current_best"
    baseline_metrics, baseline_rows = evaluate_case(baseline_dir, base_truth, truth_obj, args_obj, weights)
    baseline_row = flatten_result(0, "baseline_current_best", base_iw, base_crit, base_iw, base_crit, baseline_dir, baseline_metrics, baseline_rows)

    if cli.mode == "smoke":
        pairs = SMOKE_DELTAS
    else:
        pairs = [(di, dc) for di in FULL_DELTAS for dc in FULL_DELTAS if not (di == 0 and dc == 0)]

    rows = []
    candidate_bundles = {}
    for i, (di, dc) in enumerate(pairs, start=1):
        iw = round(max(0.10, min(1.00, base_iw + di)), 6)
        crit = round(max(0.05, min(0.95, base_crit + dc)), 6)
        case_name = f"case_{i:03d}_di{di:+.2f}_dc{dc:+.2f}_iw{iw:.6f}_crit{crit:.6f}".replace("+", "p").replace("-", "m")
        print(f"[local_iwcrit] {case_name}")
        truth = search.set_initial_water_config(base_truth, {"fraction_full": iw})
        truth = set_crit(truth, crit)
        case_dir = out_dir / case_name
        metrics, pred_rows = evaluate_case(case_dir, truth, truth_obj, args_obj, weights)
        row = flatten_result(i, case_name, iw, crit, base_iw, base_crit, case_dir, metrics, pred_rows)
        ok, reason = acceptable(row, baseline_row, cli)
        row["accepted_candidate"] = ok
        row["acceptance_reason"] = reason
        rows.append(row)
        candidate_bundles[i] = (metrics, pred_rows)

    fields = list(rows[0].keys()) if rows else []
    with (out_dir / "local_iwcrit_results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    accepted = [r for r in rows if r["accepted_candidate"]]
    accepted.sort(key=lambda r: (r["soil_water_error"], r["maize_yield_error"], r["wheat_yield_error"]))
    best = accepted[0] if accepted else None
    report = {
        "output_dir": str(out_dir),
        "baseline": baseline_row,
        "n_cases": len(rows),
        "n_accepted_candidates": len(accepted),
        "best_candidate": best,
    }
    if best and cli.update_best:
        metrics, pred_rows = candidate_bundles[best["case_id"]]
        backup_dir = backup_and_update_best(
            best,
            base_truth,
            out_dir,
            {
                "baseline": baseline_metrics,
                "candidate": metrics,
                "baseline_values": {"initial_water": base_iw, "crit_fr_asw": base_crit},
            },
            pred_rows,
        )
        report["best_updated"] = True
        report["best_backup_dir"] = str(backup_dir)
    else:
        report["best_updated"] = False

    (out_dir / "local_iwcrit_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# output_sobol InitialWater / crit_fr_asw 局部搜索",
        "",
        f"- mode: {cli.mode}",
        f"- cases: {len(rows)}",
        f"- accepted candidates: {len(accepted)}",
        f"- baseline soil_water: {baseline_row['soil_water_error']}",
        f"- baseline wheat yield error: {baseline_row['wheat_yield_error']}",
        f"- baseline maize yield error: {baseline_row['maize_yield_error']}",
        "",
    ]
    if best:
        lines += [
            "## Best accepted candidate",
            f"- case_id: {best['case_id']}",
            f"- InitialWater.FractionFull: {base_iw} -> {best['InitialWater.FractionFull']}",
            f"- crit_fr_asw: {base_crit} -> {best['crit_fr_asw']}",
            f"- soil_water: {baseline_row['soil_water_error']} -> {best['soil_water_error']}",
            f"- wheat yield error: {best['wheat_yield_error']}",
            f"- maize yield error: {best['maize_yield_error']}",
            f"- phenology_error_days_max: {best['phenology_error_days_max']}",
            f"- updated best: {report['best_updated']}",
        ]
    else:
        lines += ["## Result", "没有 candidate 同时满足 soil_water 改善、yield 安全余量、phenology 和 biomass/structure 守门。"]
    (out_dir / "local_iwcrit_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:5000])


if __name__ == "__main__":
    main()
