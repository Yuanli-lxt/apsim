##bio优先

#!/usr/bin/env python3
import argparse
import csv
import copy
import json
import math
import random
import re
import shutil
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
APSIM_ROOT = PROJECT_ROOT.parent
PROCESSING_DIR = APSIM_ROOT / "processing"
if str(PROCESSING_DIR) not in sys.path:
    sys.path.insert(0, str(PROCESSING_DIR))

import run_joint_single_factor_rounds as base


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output_sobol"
DEFAULT_HDSW_OUTPUT_DIR = PROJECT_ROOT / "output_hdsw_water_yield"
DEFAULT_HDSW_SOIL_SOURCE = PROJECT_ROOT / "output_hdsw" / "output" / "best" / "truth.apsim"
DEFAULT_HDSW_SEED_DIR = PROJECT_ROOT / "output_sobol" / "best"
DEFAULT_WHEAT_CULTIVAR = "Jimai70_v132_joint_iter353"
DEFAULT_MAIZE_CULTIVAR = "P01_shandong_2025_v527_joint_iter3"

PROCESS_BIO = DEFAULT_OUTPUT_DIR
INDEX_PATH = PROCESS_BIO / "iteration_index.csv"
BEST_DIR = PROCESS_BIO / "best"
WHEAT_LATE_STAGE_CN_DEFAULT = "蜡熟期"
DEFAULT_VALIDATION_CSV = PROJECT_ROOT / "data" / "processed" / "observations" / "independent_validation_observations_p02_maize_p01_wheat.csv"
DEFAULT_TRUTH_TEMPLATE = PROJECT_ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"
EPS = 1e-9


def configure_output_dir(output_dir: Path | str) -> None:
    """运行时切换本轮搜索的输出目录，避免覆盖旧实验。"""
    global PROCESS_BIO, INDEX_PATH, BEST_DIR
    PROCESS_BIO = Path(output_dir)
    INDEX_PATH = PROCESS_BIO / "iteration_index.csv"
    BEST_DIR = PROCESS_BIO / "best"

PROCESS_BIO_PHASE_COLS = [
    "Date",
    "Rainfall",
    "currentState",
    "rotationNumber",
    "simulation_days",
    "SoilWater",
    "water_1",
    "water_2",
    "water_3",
    "water_4",
    "water_5",
    "SurfaceRunoff",
    "SurfacePond",
    "Infiltration",
    "IrrigationApplied",
    "IrrigationTotal",
    "IrrigationLoss",
    "IrrigationAllocation",
    "IrrigationCritFrASW",
    "MaizeBio",
    "WheatBio",
    "MaizeYield",
    "WheatYield",
    "wheatlai",
    "maizelai",
    "WheatLeafGreen",
    "WheatLeafSen",
    "WheatStemGreen",
    "WheatStemSen",
    "MaizeLeafGreen",
    "MaizeLeafSen",
    "MaizeStemGreen",
    "MaizeStemSen",
    "WheatStage",
    "MaizeStage",
    "WheatGrain_no",
]

PROCESS_BIO_NUMERIC_PHASE_COLS = [
    c
    for c in PROCESS_BIO_PHASE_COLS
    if c not in ("Date", "currentState", "WheatStage", "MaizeStage")
]

# 当前 process_bio 的 APSIM output/report 已经扩展了 Phases.out 列。
# 原 processing 模块中的固定列定义较旧，会把 WheatStage/MaizeStage 读错位。
base.PHASE_COLS = PROCESS_BIO_PHASE_COLS
base.NUMERIC_PHASE_COLS = PROCESS_BIO_NUMERIC_PHASE_COLS

GROUP_KEYS = (
    "total_biomass",
    "structure_biomass_leaf_stem",
    "yield",
    "LAI",
    "soil_water",
    "phenology",
)

DEFAULT_GROUP_WEIGHTS = {
    "total_biomass": 0.25,
    "structure_biomass_leaf_stem": 0.20,
    "yield": 0.20,
    "LAI": 0.00,
    "soil_water": 0.15,
    "phenology": 0.10,
}

SOIL_VAR_ALIASES = {
    "water_1": ["water_1", "sw1", "sw_1", "soilwater1", "soil_water_1", "theta10", "vwc10", "swc10"],
    "water_2": ["water_2", "sw2", "sw_2", "soilwater2", "soil_water_2", "theta20", "vwc20", "swc20"],
    "water_3": ["water_3", "sw3", "sw_3", "soilwater3", "soil_water_3", "theta30", "vwc30", "swc30"],
    "water_4": ["water_4", "sw4", "sw_4", "soilwater4", "soil_water_4", "theta40", "vwc40", "swc40"],
    "water_5": ["water_5", "sw5", "sw_5", "soilwater5", "soil_water_5", "theta50", "vwc50", "swc50"],
}

SOBOL_PRIORITY = {
    "maize": {
        "phenology": [
            "tt_flag_to_flower",
            "tt_endjuv_to_init",
            "tt_flower_to_maturity",
            "tt_emerg_to_endjuv",
        ],
        "yield_component": [
            "tt_flag_to_flower",
            "tt_endjuv_to_init",
            "tt_flower_to_maturity",
        ],
        "biomass_canopy": [
            "rue",
        ],
    },
    "wheat": {
        "phenology": [
            "tt_floral_initiation",
            "tt_end_of_juvenile",
            "photop_sens",
            "vern_sens",
        ],
        "yield_component": [
            "tt_floral_initiation",
            "tt_end_of_juvenile",
            "photop_sens",
            "vern_sens",
            "tt_start_grain_fill",
            "max_grain_size",
        ],
        "biomass_canopy": [
            "rue",
            "largestLeafParams[1]",
            "largestLeafParams[2]",
        ],
    },
}

PARAM_ALIASES = {
    "maize": {
        "largestLeafParams[0]": "largestLeafArea",
        "largestLeafParams[1]": "largestLeafB",
        "largestLeafParams[2]": "largestLeafC",
    },
    "wheat": {
        "largestLeafParams[0]": "largestLeafArea",
        "largestLeafParams[1]": "largestLeafB",
        "largestLeafParams[2]": "largestLeafC",
    },
}

PARAM_BOUNDS = {
    ("maize", "tt_emerg_to_endjuv"): (40.0, 120.0),
    ("maize", "tt_endjuv_to_init"): (120.0, 360.0),
    ("maize", "tt_flag_to_flower"): (15.0, 90.0),
    ("maize", "tt_flower_to_maturity"): (650.0, 1050.0),
    ("maize", "rue"): (2.0, 4.5),
    ("maize", "largestLeafParams[0]"): (300.0, 1100.0),
    ("maize", "largestLeafParams[1]"): (-3.0, 1.0),
    ("maize", "largestLeafParams[2]"): (0.005, 0.12),
    ("wheat", "vern_sens"): (0.8, 5.0),
    ("wheat", "photop_sens"): (0.8, 5.0),
    ("wheat", "tt_end_of_juvenile"): (260.0, 520.0),
    ("wheat", "tt_floral_initiation"): (320.0, 700.0),
    ("wheat", "tt_start_grain_fill"): (450.0, 760.0),
    ("wheat", "max_grain_size"): (0.035, 0.075),
    ("wheat", "rue"): (1.0, 3.5),
    ("wheat", "largestLeafParams[1]"): (-3.0, 1.0),
    ("wheat", "largestLeafParams[2]"): (0.005, 0.12),
}

WATER_YIELD_FIXED_DATE_CANDIDATES = [
    "2025-03-21",  # wheat spring green-up/jointing proxy
    "2025-04-25",  # wheat flowering proxy
    "2025-05-13",  # wheat grain filling proxy
    "2025-05-26",  # wheat late grain filling proxy
    "2025-07-16",  # maize vegetative sampling
    "2025-08-03",  # maize rapid growth
    "2025-08-17",  # maize flowering proxy
    "2025-09-02",  # maize grain filling proxy
]

WATER_YIELD_MANAGER_NAME = "SobolWaterYieldIrrigation"


def mean_valid(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None
    return sum(vals) / len(vals)


def max_valid(values):
    vals = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None
    return max(vals)


def parse_stage_cn_from_sheet(source_sheet: str):
    if not source_sheet:
        return None
    m = re.search(r"-\s*([^-]+)\s*$", str(source_sheet))
    if not m:
        return None
    stage = m.group(1).strip()
    return stage or None


def pick_validation_year(rows: list):
    if not rows:
        return None
    by_year_counts = defaultdict(int)
    years_with_yield_ha = set()
    for r in rows:
        d = str(r.get("date", "")).strip()
        if not d:
            continue
        try:
            year = int(d[:4])
        except Exception:
            continue
        by_year_counts[year] += 1
        if str(r.get("variable_name", "")).strip() == "产量/kg/公顷":
            years_with_yield_ha.add(year)
    if years_with_yield_ha:
        return max(years_with_yield_ha)
    if not by_year_counts:
        return None
    return sorted(by_year_counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)[0][0]


def load_truth_from_validation_csv(csv_path: Path):
    if not csv_path.exists():
        raise FileNotFoundError(f"validation csv not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out = {"wheat": {"observations": []}, "maize": {"observations": []}}
    crop_to_year = {}
    for crop in ("wheat", "maize"):
        crop_rows = [r for r in rows if str(r.get("crop", "")).strip().lower() == crop]
        y = pick_validation_year(crop_rows)
        if y is None:
            continue
        crop_to_year[crop] = y

        # Aggregate variables by date for the selected validation season.
        by_date = {}
        for r in crop_rows:
            date = str(r.get("date", "")).strip()
            if not date:
                continue
            try:
                year = int(date[:4])
            except Exception:
                continue
            if year != y:
                continue

            variable = str(r.get("variable_name", "")).strip()
            val_txt = str(r.get("value", "")).strip()
            if val_txt == "":
                continue
            try:
                value = float(val_txt)
            except ValueError:
                continue

            o = by_date.setdefault(date, {"date": date})
            stage_cn = parse_stage_cn_from_sheet(str(r.get("source_sheet", "")).strip())
            if stage_cn:
                o["stage_cn"] = stage_cn

            if variable == "叶生物量/kg/ha":
                o["leaf_biomass_kg_ha"] = value
            elif variable == "茎生物量/kg/ha":
                o["stem_biomass_kg_ha"] = value
            elif variable == "总生物量/kg/ha":
                o["total_biomass_kg_ha"] = value
            elif variable == "产量/kg/公顷":
                o["yield_kg_ha"] = value
            elif variable == "产量/kg/亩" and "yield_kg_ha" not in o:
                o["yield_kg_ha"] = value * 15.0

        obs = sorted(by_date.values(), key=lambda x: x["date"])
        out[crop]["observations"] = obs

    if not out["wheat"]["observations"] or not out["maize"]["observations"]:
        raise ValueError(
            f"validation csv has insufficient observations after season filtering: "
            f"wheat={len(out['wheat']['observations'])}, maize={len(out['maize']['observations'])}"
        )
    out["_meta"] = {"source": str(csv_path), "validation_year": crop_to_year}
    return out


def normalize_group_weights(weights: Optional[dict]) -> dict:
    raw = dict(DEFAULT_GROUP_WEIGHTS)
    if weights:
        for k in GROUP_KEYS:
            if k in weights and weights[k] is not None:
                raw[k] = max(0.0, float(weights[k]))
    total = sum(raw.values())
    if total <= 0:
        return dict(DEFAULT_GROUP_WEIGHTS)
    return {k: raw[k] / total for k in GROUP_KEYS}


def canon_var_from_truth_row(variable_name: str):
    v = str(variable_name or "").strip().lower()
    if not v:
        return None, None
    if v == "lai":
        return "LAI", "LAI"
    if "叶生物量" in v and "/kg/ha" in v:
        return "leaf_biomass_kg_ha", "structure_biomass_leaf_stem"
    if "茎生物量" in v and "/kg/ha" in v:
        return "stem_biomass_kg_ha", "structure_biomass_leaf_stem"
    if "总生物量" in v and "/kg/ha" in v:
        return "total_biomass_kg_ha", "total_biomass"
    if "产量" in v and "/kg/公顷" in v:
        return "yield_kg_ha", "yield"
    if "产量" in v and "/kg/亩" in v:
        return "yield_kg_mu", "yield"
    for k in ("1", "2", "3", "4", "5"):
        if f"water_{k}" in v:
            return f"water_{k}", "soil_water"
    return None, None


def _truth_row_key_for_yield_dedupe(crop: str, date: str):
    return f"{crop or ''}|{date or ''}|yield_kg_ha"


def load_truth_from_validation_csv(csv_path: Path):
    if not csv_path.exists():
        raise FileNotFoundError(f"validation csv not found: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    out = {"wheat": {"observations": []}, "maize": {"observations": []}, "soil": {"observations": []}}
    truth_rows = []
    yield_seen = {}

    for r in rows:
        crop = str(r.get("crop", "")).strip().lower()
        if crop not in ("wheat", "maize", "soil"):
            continue
        date = str(r.get("date", "")).strip()
        if not date:
            continue
        val_txt = str(r.get("value", "")).strip()
        if val_txt == "":
            continue
        try:
            obs_value = float(val_txt)
        except Exception:
            continue

        variable_name = str(r.get("variable_name", "")).strip()
        variable, group = canon_var_from_truth_row(variable_name)
        unit = str(r.get("unit", "")).strip()
        source_sheet = str(r.get("source_sheet", "")).strip()
        source_file = str(r.get("source_file", "")).strip()
        stage_cn = parse_stage_cn_from_sheet(source_sheet)

        if variable is None:
            truth_rows.append(
                {
                    "crop": crop,
                    "date": date,
                    "stage_cn": stage_cn,
                    "source_file": source_file,
                    "source_sheet": source_sheet,
                    "variable_name": variable_name,
                    "variable": "unmapped",
                    "unit": unit,
                    "obs_value": obs_value,
                    "group": "unmapped",
                    "used_in_score": False,
                    "is_duplicate_converted": False,
                    "notes": "unmapped_variable",
                }
            )
            continue

        duplicate_converted = False
        notes = None
        if variable == "yield_kg_mu":
            key = _truth_row_key_for_yield_dedupe(crop, date)
            if key in yield_seen and yield_seen[key]["source"] == "yield_kg_ha":
                variable = "yield_kg_ha"
                obs_value = obs_value * 15.0
                duplicate_converted = True
                notes = "duplicate_converted"
                used_in_score = False
            else:
                variable = "yield_kg_ha"
                obs_value = obs_value * 15.0
                used_in_score = True
                yield_seen[key] = {"source": "yield_kg_mu"}
        elif variable == "yield_kg_ha":
            key = _truth_row_key_for_yield_dedupe(crop, date)
            existing = yield_seen.get(key)
            if existing and existing["source"] == "yield_kg_mu":
                for rr in reversed(truth_rows):
                    if rr["crop"] == crop and rr["date"] == date and rr["variable"] == "yield_kg_ha" and rr["used_in_score"]:
                        rr["used_in_score"] = False
                        rr["is_duplicate_converted"] = True
                        rr["notes"] = "duplicate_converted"
                        break
            yield_seen[key] = {"source": "yield_kg_ha"}
            used_in_score = True
        else:
            used_in_score = True

        truth_rows.append(
            {
                "crop": crop,
                "date": date,
                "stage_cn": stage_cn,
                "source_file": source_file,
                "source_sheet": source_sheet,
                "variable_name": variable_name,
                "variable": variable,
                "unit": unit,
                "obs_value": obs_value,
                "group": group,
                "used_in_score": used_in_score,
                "is_duplicate_converted": duplicate_converted,
                "notes": notes,
            }
        )

    by_crop_date = {crop: {} for crop in ("wheat", "maize", "soil")}
    for t in truth_rows:
        crop = t["crop"]
        if crop not in by_crop_date:
            continue
        if t["variable"] in ("unmapped",):
            continue
        d = t["date"]
        o = by_crop_date[crop].setdefault(d, {"date": d})
        if t.get("stage_cn"):
            o["stage_cn"] = t["stage_cn"]
        if t["used_in_score"]:
            o[t["variable"]] = t["obs_value"]

    for crop in ("wheat", "maize", "soil"):
        out[crop]["observations"] = sorted(by_crop_date[crop].values(), key=lambda x: x["date"])

    if not out["wheat"]["observations"] or not out["maize"]["observations"] or not out["soil"]["observations"]:
        raise ValueError(
            f"validation csv has insufficient observations after full-truth loading: "
            f"wheat={len(out['wheat']['observations'])}, maize={len(out['maize']['observations'])}, soil={len(out['soil']['observations'])}"
        )
    out["_truth_rows"] = sorted(truth_rows, key=lambda x: (x["crop"], x["date"], x["variable"]))
    out["_meta"] = {"source": str(csv_path), "score_truth_mode": "all_rows_no_year_filter"}
    return out


def load_truth_observations(validation_csv_path: Path):
    if validation_csv_path and validation_csv_path.exists():
        return load_truth_from_validation_csv(validation_csv_path)
    truth_json = base.PROCESSING / "truth_extract.json"
    if truth_json.exists():
        return json.loads(base.read_text(truth_json))
    raise FileNotFoundError(
        f"Neither validation csv ({validation_csv_path}) nor truth_extract.json ({truth_json}) is available."
    )


def get_wheat_anchor_dates(truth_obj: dict):
    obs = truth_obj.get("wheat", {}).get("observations", [])
    out = []
    for o in obs:
        if "total_biomass_kg_ha" in o and o.get("date"):
            out.append(str(o["date"]))
    return sorted(out)


def find_wheat_late_anchor_date(truth_obj: dict, preferred_stage_cn: str):
    obs = truth_obj.get("wheat", {}).get("observations", [])
    if preferred_stage_cn:
        for o in obs:
            if o.get("stage_cn") == preferred_stage_cn and "total_biomass_kg_ha" in o and o.get("date"):
                return str(o["date"])
    dates = get_wheat_anchor_dates(truth_obj)
    return dates[-1] if dates else None


def extract_wheat_anchor_metrics(out_dir: Path, truth_obj: dict, late_stage_cn: str):
    phase_rows = base.parse_phases(out_dir / "Rotation Sample Phases.out")
    by_date = {r["Date"]: r for r in phase_rows}
    obs = truth_obj.get("wheat", {}).get("observations", [])
    rel_errors = []
    by_date_rel = {}
    for o in obs:
        if "total_biomass_kg_ha" not in o or not o.get("date"):
            continue
        d = str(o["date"])
        sim = by_date.get(d)
        if not sim:
            continue
        rel = base.safe_rel(sim.get("WheatBio"), o.get("total_biomass_kg_ha"))
        if rel is None:
            continue
        rel_errors.append(rel)
        by_date_rel[d] = rel

    late_date = find_wheat_late_anchor_date(truth_obj, late_stage_cn)
    late_rel = None if late_date is None else by_date_rel.get(late_date)
    return {
        "wheat_anchor_dates": get_wheat_anchor_dates(truth_obj),
        "wheat_anchor_rel_by_date": by_date_rel,
        "wheat_anchor_rel_mean": mean_valid(rel_errors),
        "wheat_anchor_rel_max": max_valid(rel_errors),
        "wheat_late_anchor_date": late_date,
        "wheat_late_anchor_rel_error": late_rel,
        "wheat_anchor_count": len(rel_errors),
    }


def score_bio_objective(
    eval_obj: dict,
    pheno_guard_days: int,
    anchor_metrics: dict = None,
    wheat_anchor_weight: float = 0.0,
    wheat_late_weight: float = 0.0,
    wheat_anchor_max_weight: float = 0.0,
):
    w = eval_obj["crops"]["wheat"]
    m = eval_obj["crops"]["maize"]

    yield_mean = mean_valid([w.get("yield_error_rel"), m.get("yield_error_rel")])
    total_bio_mean = mean_valid([w.get("total_biomass_error_rel_mean"), m.get("total_biomass_error_rel_mean")])
    structure_mean = mean_valid(
        [
            w.get("leaf_biomass_error_rel_mean"),
            w.get("stem_biomass_error_rel_mean"),
            m.get("leaf_biomass_error_rel_mean"),
            m.get("stem_biomass_error_rel_mean"),
        ]
    )
    w_ph = w.get("phenology_error_days_mean")
    m_ph = m.get("phenology_error_days_mean")
    pheno_days_max = max_valid([w_ph, m_ph])
    pheno_norm_mean = mean_valid(
        [
            None if w_ph is None else min(float(w_ph), 30.0) / 30.0,
            None if m_ph is None else min(float(m_ph), 30.0) / 30.0,
        ]
    )

    # Missing components are treated as weakly penalized, never as perfect scores.
    y = 1.0 if yield_mean is None else yield_mean
    tb = 1.0 if total_bio_mean is None else total_bio_mean
    st = 1.0 if structure_mean is None else structure_mean
    pn = 1.0 if pheno_norm_mean is None else pheno_norm_mean
    score = 0.45 * tb + 0.20 * st + 0.25 * y + 0.10 * pn

    am = anchor_metrics or {}
    wa = am.get("wheat_anchor_rel_mean")
    wl = am.get("wheat_late_anchor_rel_error")
    wx = am.get("wheat_anchor_rel_max")
    score += max(0.0, float(wheat_anchor_weight)) * (1.0 if wa is None else wa)
    score += max(0.0, float(wheat_late_weight)) * (1.0 if wl is None else wl)
    score += max(0.0, float(wheat_anchor_max_weight)) * (1.0 if wx is None else wx)

    if pheno_days_max is None:
        score += 0.5
    elif pheno_days_max > pheno_guard_days:
        score += 0.5 + 0.02 * (pheno_days_max - pheno_guard_days)

    return {
        "custom_score": score,
        "yield_error_rel_mean": yield_mean,
        "total_biomass_error_rel_mean_all_crops": total_bio_mean,
        "structure_error_rel_mean_all_crops": structure_mean,
        "phenology_error_days_max": pheno_days_max,
        "phenology_error_norm_mean": pheno_norm_mean,
        "wheat_anchor_rel_mean": wa,
        "wheat_late_anchor_rel_error": wl,
        "wheat_anchor_rel_max": wx,
    }


def build_metrics(
    iter_no: int,
    baseline_eval: dict,
    candidate_eval: dict,
    baseline_anchor: dict,
    candidate_anchor: dict,
    pheno_guard_days: int,
    wheat_anchor_weight: float,
    wheat_late_weight: float,
    wheat_anchor_max_weight: float,
):
    b = score_bio_objective(
        baseline_eval,
        pheno_guard_days,
        baseline_anchor,
        wheat_anchor_weight,
        wheat_late_weight,
        wheat_anchor_max_weight,
    )
    c = score_bio_objective(
        candidate_eval,
        pheno_guard_days,
        candidate_anchor,
        wheat_anchor_weight,
        wheat_late_weight,
        wheat_anchor_max_weight,
    )
    improve = 0.0
    if b["custom_score"] not in (None, 0):
        improve = (b["custom_score"] - c["custom_score"]) / b["custom_score"] * 100.0
    better = c["custom_score"] < b["custom_score"]
    return {
        "iteration": iter_no,
        "site": "wheat:P01;maize:P02",
        "objective": "bio_first_with_yield_and_phenology_guard",
        "constraints": {
            "phenology_error_days_max_le": pheno_guard_days,
        },
        "objective_weights": {
            "cross_crop": {
                "total_biomass_component": 0.45,
                "structure_component": 0.20,
                "yield_component": 0.25,
                "phenology_component": 0.10,
            },
            "wheat_anchor": {
                "anchor_mean_weight": wheat_anchor_weight,
                "late_anchor_weight": wheat_late_weight,
                "anchor_max_weight": wheat_anchor_max_weight,
            },
        },
        "baseline": baseline_eval,
        "candidate": candidate_eval,
        "baseline_anchor": baseline_anchor,
        "candidate_anchor": candidate_anchor,
        "baseline_custom": b,
        "candidate_custom": c,
        "comparison": {
            "custom_score_improvement_pct": improve,
            "is_better_than_baseline": better,
        },
    }


def collect_prediction_vs_truth_rows(out_dir: Path, truth_obj: dict, scenario: str):
    phase_rows = base.parse_phases(out_dir / "Rotation Sample Phases.out")
    harvest_rows = base.parse_harvest(out_dir / "Rotation Sample Harvest.out")
    by_date = {r["Date"]: r for r in phase_rows}
    yield_sim = {
        "wheat": max((r["paddock.wheat.yield"] for r in harvest_rows), default=None),
        "maize": max((r["paddock.maize.yield"] for r in harvest_rows), default=None),
    }

    rows = []
    for crop in ("wheat", "maize"):
        obs = truth_obj.get(crop, {}).get("observations", [])
        for o in obs:
            d = o.get("date")
            stage_cn = o.get("stage_cn")
            sim = by_date.get(d) if d else None

            def add_row(variable: str, obs_value, sim_value):
                rows.append(
                    {
                        "scenario": scenario,
                        "crop": crop,
                        "date": d or "",
                        "stage_cn": stage_cn or "",
                        "variable": variable,
                        "obs_value": obs_value,
                        "sim_value": sim_value,
                        "abs_error": None if (obs_value is None or sim_value is None) else abs(sim_value - obs_value),
                        "rel_error": base.safe_rel(sim_value, obs_value),
                    }
                )

            if "leaf_biomass_kg_ha" in o:
                if crop == "wheat":
                    sim_leaf = None if sim is None else (sim["WheatLeafGreen"] + sim["WheatLeafSen"]) * 10.0
                else:
                    sim_leaf = None if sim is None else (sim["MaizeLeafGreen"] + sim["MaizeLeafSen"]) * 10.0
                add_row("leaf_biomass_kg_ha", o.get("leaf_biomass_kg_ha"), sim_leaf)

            if "stem_biomass_kg_ha" in o:
                if crop == "wheat":
                    sim_stem = None if sim is None else (sim["WheatStemGreen"] + sim["WheatStemSen"]) * 10.0
                else:
                    sim_stem = None if sim is None else sim["MaizeStemGreen"] * 10.0
                add_row("stem_biomass_kg_ha", o.get("stem_biomass_kg_ha"), sim_stem)

            if "total_biomass_kg_ha" in o:
                sim_total = None if sim is None else (sim["WheatBio"] if crop == "wheat" else sim["MaizeBio"])
                add_row("total_biomass_kg_ha", o.get("total_biomass_kg_ha"), sim_total)

            if "yield_kg_ha" in o:
                add_row("yield_kg_ha", o.get("yield_kg_ha"), yield_sim[crop])
    return rows


def write_prediction_vs_truth_report(
    iter_dir: Path,
    truth_obj: dict,
    out_base: Path,
    out_cand: Path,
):
    rows = []
    rows.extend(collect_prediction_vs_truth_rows(out_base, truth_obj, "baseline"))
    rows.extend(collect_prediction_vs_truth_rows(out_cand, truth_obj, "candidate"))

    csv_path = iter_dir / "prediction_vs_truth.csv"
    header = [
        "scenario",
        "crop",
        "date",
        "stage_cn",
        "variable",
        "obs_value",
        "sim_value",
        "abs_error",
        "rel_error",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Summary with mean relative error by (scenario, crop, variable).
    bucket = {}
    for r in rows:
        key = (r["scenario"], r["crop"], r["variable"])
        rel = r["rel_error"]
        if rel is None or (isinstance(rel, float) and math.isnan(rel)):
            continue
        bucket.setdefault(key, []).append(rel)

    md_lines = [
        "# prediction_vs_truth_summary",
        "",
        "| scenario | crop | variable | mean_rel_error | n |",
        "|---|---|---|---:|---:|",
    ]
    for key in sorted(bucket.keys()):
        vals = bucket[key]
        mean_rel = sum(vals) / len(vals) if vals else float("nan")
        md_lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {mean_rel:.6f} | {len(vals)} |")
    md_lines.append("")
    base.write_text(iter_dir / "prediction_vs_truth_summary.md", "\n".join(md_lines))


def _to_iso_date(date_text: str):
    t = str(date_text or "").strip()
    if not t:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        return t
    if re.match(r"^\d{2}/\d{2}/\d{4}$", t):
        return datetime.strptime(t, "%d/%m/%Y").strftime("%Y-%m-%d")
    return None


def _parse_apsim_out_table(path: Path):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    unit_idx = None
    for i, line in enumerate(lines):
        if "(dd/mm/yyyy)" in line:
            unit_idx = i
            break
    if unit_idx is None or unit_idx <= 0:
        return []
    header_line = lines[unit_idx - 1].strip()
    header = header_line.split()
    if not header:
        return []
    recs = base.parse_wrapped_records(path, len(header))
    out = []
    for rec in recs:
        row = {}
        for i, c in enumerate(header):
            v = rec[i] if i < len(rec) else ""
            if i == 0:
                row[c] = _to_iso_date(v) or v
                continue
            try:
                row[c] = float(v)
            except Exception:
                row[c] = v
        out.append(row)
    return out


def _normalize_col_name(name: str):
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def _get_numeric_by_alias(row: dict, aliases: list):
    if not row:
        return None
    by_norm = {_normalize_col_name(k): k for k in row.keys()}
    for a in aliases:
        k = by_norm.get(_normalize_col_name(a))
        if not k:
            continue
        v = row.get(k)
        if isinstance(v, (int, float)):
            if isinstance(v, float) and math.isnan(v):
                continue
            return float(v)
        try:
            return float(str(v))
        except Exception:
            continue
    return None


def _normalize_soil_water_obs(v: Optional[float]):
    if v is None:
        return None
    x = float(v)
    # APSIM sw(i) is commonly mm/mm; truth file soil water is in percent.
    if 0.0 <= x <= 1.5:
        return x * 100.0
    return x


def parse_soil_water_by_date(out_dir: Path):
    candidates = [
        out_dir / "Rotation Sample SoilWater.out",
        out_dir / "Rotation Sample Soil Water.out",
        out_dir / "Rotation Sample SW.out",
        out_dir / "Rotation Sample Water.out",
    ]
    candidates.extend(sorted(out_dir.glob("*.out")))
    by_date = {}
    loaded_any = False
    for p in candidates:
        if not p.exists():
            continue
        rows = _parse_apsim_out_table(p)
        if not rows:
            continue
        loaded_any = True
        for r in rows:
            d = _to_iso_date(r.get("Date") if "Date" in r else next(iter(r.values()), None))
            if not d:
                continue
            o = by_date.setdefault(d, {})
            for var, aliases in SOIL_VAR_ALIASES.items():
                v = _get_numeric_by_alias(r, aliases)
                if v is not None:
                    o[var] = _normalize_soil_water_obs(v)
    return by_date, loaded_any


def _build_pheno_diag(eval_obj: dict, anchor_metrics: dict):
    w = eval_obj["crops"]["wheat"]
    m = eval_obj["crops"]["maize"]
    w_ph = w.get("phenology_error_days_mean")
    m_ph = m.get("phenology_error_days_mean")
    return {
        "yield_error_rel_mean": mean_valid([w.get("yield_error_rel"), m.get("yield_error_rel")]),
        "total_biomass_error_rel_mean_all_crops": mean_valid([w.get("total_biomass_error_rel_mean"), m.get("total_biomass_error_rel_mean")]),
        "structure_error_rel_mean_all_crops": mean_valid(
            [
                w.get("leaf_biomass_error_rel_mean"),
                w.get("stem_biomass_error_rel_mean"),
                m.get("leaf_biomass_error_rel_mean"),
                m.get("stem_biomass_error_rel_mean"),
            ]
        ),
        "phenology_error_days_max": max_valid([w_ph, m_ph]),
        "phenology_error_norm_mean": mean_valid(
            [
                None if w_ph is None else min(float(w_ph), 30.0) / 30.0,
                None if m_ph is None else min(float(m_ph), 30.0) / 30.0,
            ]
        ),
        "wheat_pheno_days_mean": w_ph,
        "maize_pheno_days_mean": m_ph,
        "wheat_anchor_rel_mean": (anchor_metrics or {}).get("wheat_anchor_rel_mean"),
        "wheat_late_anchor_rel_error": (anchor_metrics or {}).get("wheat_late_anchor_rel_error"),
        "wheat_anchor_rel_max": (anchor_metrics or {}).get("wheat_anchor_rel_max"),
    }


def collect_prediction_vs_truth_rows(out_dir: Path, truth_obj: dict, scenario: str):
    phase_rows = base.parse_phases(out_dir / "Rotation Sample Phases.out")
    harvest_rows = base.parse_harvest(out_dir / "Rotation Sample Harvest.out")
    phase_by_date = {r["Date"]: r for r in phase_rows}
    yield_sim = {
        "wheat": max((r["paddock.wheat.yield"] for r in harvest_rows), default=None),
        "maize": max((r["paddock.maize.yield"] for r in harvest_rows), default=None),
    }
    soil_by_date, soil_parser_loaded = parse_soil_water_by_date(out_dir)
    truth_rows = truth_obj.get("_truth_rows", [])

    rows = []
    for t in truth_rows:
        row = copy.deepcopy(t)
        crop = row.get("crop")
        date = row.get("date")
        var = row.get("variable")
        sim_value = None
        missing_reason = None

        phase = phase_by_date.get(date) if date else None
        if var == "leaf_biomass_kg_ha":
            if phase is None:
                missing_reason = "missing_sim_date"
            elif crop == "wheat":
                sim_value = (phase["WheatLeafGreen"] + phase["WheatLeafSen"]) * 10.0
            elif crop == "maize":
                sim_value = (phase["MaizeLeafGreen"] + phase["MaizeLeafSen"]) * 10.0
        elif var == "stem_biomass_kg_ha":
            if phase is None:
                missing_reason = "missing_sim_date"
            elif crop == "wheat":
                sim_value = (phase["WheatStemGreen"] + phase["WheatStemSen"]) * 10.0
            elif crop == "maize":
                sim_value = phase["MaizeStemGreen"] * 10.0
        elif var == "total_biomass_kg_ha":
            if phase is None:
                missing_reason = "missing_sim_date"
            elif crop == "wheat":
                sim_value = phase["WheatBio"]
            elif crop == "maize":
                sim_value = phase["MaizeBio"]
        elif var == "yield_kg_ha":
            sim_value = yield_sim.get(crop)
            if sim_value is None:
                missing_reason = "missing_sim_column"
        elif var == "LAI":
            if phase is None:
                missing_reason = "missing_sim_date"
            elif crop == "wheat":
                sim_value = phase.get("wheatlai")
            elif crop == "maize":
                sim_value = phase.get("maizelai")
            if sim_value is None or (isinstance(sim_value, float) and math.isnan(sim_value)):
                sim_value = None
                missing_reason = missing_reason or "missing_sim_column"
        elif var in SOIL_VAR_ALIASES:
            soil = soil_by_date.get(date, {})
            sim_value = soil.get(var)
            if sim_value is None:
                missing_reason = "missing_sim_column" if soil_parser_loaded else "missing_sim_file"
        elif var == "unmapped":
            missing_reason = "unmapped_variable"
        else:
            missing_reason = "unsupported_variable"

        obs = row.get("obs_value")
        rel = None
        abs_err = None
        if obs is None:
            missing_reason = missing_reason or "missing_obs_value"
        elif sim_value is not None:
            abs_err = abs(float(sim_value) - float(obs))
            rel = abs_err / max(abs(float(obs)), EPS)

        row.update(
            {
                "scenario": scenario,
                "sim_value": sim_value,
                "abs_error": abs_err,
                "rel_error": rel,
                "capped_rel_error": None,
                "missing_reason": missing_reason,
            }
        )
        rows.append(row)
    return rows


def score_all_truth_objective(
    prediction_rows: list,
    pheno_guard_days: int,
    pheno_metrics: dict | None = None,
    weights: dict | None = None,
    missing_truth_penalty: float = 1.0,
    rel_error_cap: float = 2.0,
):
    group_weights = normalize_group_weights(weights)
    rows = [copy.deepcopy(r) for r in prediction_rows]
    group_vals = {k: [] for k in GROUP_KEYS if k != "phenology"}
    group_n_truth = {k: 0 for k in GROUP_KEYS}
    group_n_missing_sim = {k: 0 for k in GROUP_KEYS}
    depth_vals = {k: [] for k in SOIL_VAR_ALIASES}

    truth_rows_total = len(rows)
    truth_rows_used = 0
    truth_rows_missing_sim = 0
    truth_rows_missing_obs = 0

    for r in rows:
        g = r.get("group")
        if g not in group_n_truth:
            r["used_in_score"] = False
            continue
        if g == "phenology":
            r["used_in_score"] = False
            continue

        obs = r.get("obs_value")
        if obs is None:
            truth_rows_missing_obs += 1
            r["used_in_score"] = False
            r["missing_reason"] = r.get("missing_reason") or "missing_obs_value"
            continue

        if not r.get("used_in_score", True):
            r["used_in_score"] = False
            continue

        truth_rows_used += 1
        group_n_truth[g] += 1
        sim = r.get("sim_value")
        if sim is None:
            truth_rows_missing_sim += 1
            group_n_missing_sim[g] += 1
            rel = float(missing_truth_penalty)
            r["missing_reason"] = r.get("missing_reason") or "missing_sim_value"
        else:
            rel = abs(float(sim) - float(obs)) / max(abs(float(obs)), EPS)

        capped = min(float(rel), float(rel_error_cap))
        r["rel_error"] = rel
        r["capped_rel_error"] = capped
        group_vals[g].append(capped)
        if g == "soil_water" and r.get("variable") in depth_vals:
            depth_vals[r["variable"]].append(capped)

    group_scores = {k: None for k in GROUP_KEYS}
    for g in ("total_biomass", "structure_biomass_leaf_stem", "yield", "LAI"):
        group_scores[g] = mean_valid(group_vals[g])

    soil_depth_means = [mean_valid(depth_vals[k]) for k in ("water_1", "water_2", "water_3", "water_4", "water_5")]
    group_scores["soil_water"] = mean_valid(soil_depth_means)

    pm = pheno_metrics or {}
    w_ph = pm.get("wheat_pheno_days_mean")
    m_ph = pm.get("maize_pheno_days_mean")
    ph_norms = [
        None if w_ph is None else min(float(w_ph), 30.0) / 30.0,
        None if m_ph is None else min(float(m_ph), 30.0) / 30.0,
    ]
    group_scores["phenology"] = mean_valid(ph_norms)
    if ph_norms[0] is not None:
        group_n_truth["phenology"] += 1
    if ph_norms[1] is not None:
        group_n_truth["phenology"] += 1

    weighted = 0.0
    for g in GROUP_KEYS:
        sc = group_scores.get(g)
        if sc is None:
            sc = float(missing_truth_penalty)
        weighted += group_weights[g] * float(sc)

    pheno_days_max = pm.get("phenology_error_days_max")
    pheno_guard_penalty = 0.0
    if pheno_days_max is None:
        pheno_guard_penalty = 0.5
    elif pheno_days_max > pheno_guard_days:
        pheno_guard_penalty = 0.5 + 0.02 * (float(pheno_days_max) - float(pheno_guard_days))
    all_truth_score = weighted + pheno_guard_penalty

    group_details = {}
    for g in GROUP_KEYS:
        group_details[g] = {
            "mean_rel_error": group_scores.get(g),
            "n_truth": group_n_truth.get(g, 0),
            "n_missing_sim": group_n_missing_sim.get(g, 0),
        }

    return {
        "score_mode": "all_truth",
        "custom_score": all_truth_score,
        "all_truth_score": all_truth_score,
        "group_scores": group_scores,
        "group_weights": group_weights,
        "group_details": group_details,
        "truth_rows_total": truth_rows_total,
        "truth_rows_used": truth_rows_used,
        "truth_rows_missing_sim": truth_rows_missing_sim,
        "truth_rows_missing_obs": truth_rows_missing_obs,
        "rel_error_cap": rel_error_cap,
        "missing_truth_penalty": missing_truth_penalty,
        "phenology_error_days_max": pheno_days_max,
        "phenology_error_norm_mean": mean_valid(ph_norms),
        "rows_scored": rows,
    }


def build_metrics(
    iter_no: int,
    baseline_eval: dict,
    candidate_eval: dict,
    baseline_anchor: dict,
    candidate_anchor: dict,
    pheno_guard_days: int,
    wheat_anchor_weight: float,
    wheat_late_weight: float,
    wheat_anchor_max_weight: float,
    score_mode: str = "all_truth",
    baseline_rows: Optional[list] = None,
    candidate_rows: Optional[list] = None,
    group_weights: Optional[dict] = None,
    missing_truth_penalty: float = 1.0,
    truth_rel_error_cap: float = 2.0,
    enable_legacy_wheat_anchor_bonus: bool = False,
):
    score_mode = str(score_mode or "all_truth").strip().lower()

    if score_mode == "legacy":
        b = score_bio_objective(
            baseline_eval,
            pheno_guard_days,
            baseline_anchor,
            wheat_anchor_weight,
            wheat_late_weight,
            wheat_anchor_max_weight,
        )
        c = score_bio_objective(
            candidate_eval,
            pheno_guard_days,
            candidate_anchor,
            wheat_anchor_weight,
            wheat_late_weight,
            wheat_anchor_max_weight,
        )
        b_rows_scored = baseline_rows or []
        c_rows_scored = candidate_rows or []
    else:
        b_diag = _build_pheno_diag(baseline_eval, baseline_anchor)
        c_diag = _build_pheno_diag(candidate_eval, candidate_anchor)
        b = score_all_truth_objective(
            baseline_rows or [],
            pheno_guard_days=pheno_guard_days,
            pheno_metrics=b_diag,
            weights=group_weights,
            missing_truth_penalty=missing_truth_penalty,
            rel_error_cap=truth_rel_error_cap,
        )
        c = score_all_truth_objective(
            candidate_rows or [],
            pheno_guard_days=pheno_guard_days,
            pheno_metrics=c_diag,
            weights=group_weights,
            missing_truth_penalty=missing_truth_penalty,
            rel_error_cap=truth_rel_error_cap,
        )
        for d, diag, am in ((b, b_diag, baseline_anchor), (c, c_diag, candidate_anchor)):
            d["yield_error_rel_mean"] = diag["yield_error_rel_mean"]
            d["total_biomass_error_rel_mean_all_crops"] = diag["total_biomass_error_rel_mean_all_crops"]
            d["structure_error_rel_mean_all_crops"] = diag["structure_error_rel_mean_all_crops"]
            d["wheat_anchor_rel_mean"] = (am or {}).get("wheat_anchor_rel_mean")
            d["wheat_late_anchor_rel_error"] = (am or {}).get("wheat_late_anchor_rel_error")
            d["wheat_anchor_rel_max"] = (am or {}).get("wheat_anchor_rel_max")

            legacy_bonus = 0.0
            if enable_legacy_wheat_anchor_bonus:
                wa = d.get("wheat_anchor_rel_mean")
                wl = d.get("wheat_late_anchor_rel_error")
                wx = d.get("wheat_anchor_rel_max")
                legacy_bonus += max(0.0, float(wheat_anchor_weight)) * (1.0 if wa is None else wa)
                legacy_bonus += max(0.0, float(wheat_late_weight)) * (1.0 if wl is None else wl)
                legacy_bonus += max(0.0, float(wheat_anchor_max_weight)) * (1.0 if wx is None else wx)
                d["custom_score"] = float(d["custom_score"]) + legacy_bonus
                d["all_truth_score"] = float(d["all_truth_score"]) + legacy_bonus
            d["legacy_wheat_anchor_bonus_applied"] = bool(enable_legacy_wheat_anchor_bonus)
            d["legacy_wheat_anchor_bonus"] = legacy_bonus

        b_rows_scored = b.pop("rows_scored", baseline_rows or [])
        c_rows_scored = c.pop("rows_scored", candidate_rows or [])

    improve = 0.0
    if b["custom_score"] not in (None, 0):
        improve = (b["custom_score"] - c["custom_score"]) / b["custom_score"] * 100.0
    better = c["custom_score"] < b["custom_score"]

    return {
        "iteration": iter_no,
        "site": "wheat:P01;maize:P02",
        "objective": "bio_first_with_yield_and_phenology_guard",
        "constraints": {
            "phenology_error_days_max_le": pheno_guard_days,
        },
        "score_mode": score_mode,
        "objective_weights": {
            "group_weights": normalize_group_weights(group_weights),
            "wheat_anchor": {
                "anchor_mean_weight": wheat_anchor_weight,
                "late_anchor_weight": wheat_late_weight,
                "anchor_max_weight": wheat_anchor_max_weight,
                "enabled_in_custom_score": bool(enable_legacy_wheat_anchor_bonus or score_mode == "legacy"),
            },
        },
        "baseline": baseline_eval,
        "candidate": candidate_eval,
        "baseline_anchor": baseline_anchor,
        "candidate_anchor": candidate_anchor,
        "baseline_custom": b,
        "candidate_custom": c,
        "comparison": {
            "custom_score_improvement_pct": improve,
            "is_better_than_baseline": better,
        },
        "_scored_rows": {
            "baseline": b_rows_scored,
            "candidate": c_rows_scored,
        },
    }


def write_prediction_vs_truth_report(
    iter_dir: Path,
    rows_baseline: list,
    rows_candidate: list,
    baseline_custom: Optional[dict] = None,
    candidate_custom: Optional[dict] = None,
):
    rows = []
    rows.extend(rows_baseline or [])
    rows.extend(rows_candidate or [])

    csv_path = iter_dir / "prediction_vs_truth.csv"
    header = [
        "scenario",
        "crop",
        "date",
        "stage_cn",
        "source_file",
        "source_sheet",
        "unit",
        "group",
        "variable_name",
        "variable",
        "used_in_score",
        "obs_value",
        "sim_value",
        "abs_error",
        "rel_error",
        "capped_rel_error",
        "missing_reason",
        "is_duplicate_converted",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in header})

    md_lines = [
        "# prediction_vs_truth_summary",
        "",
        "## Group Scores",
        "",
        "| scenario | group | mean_rel_error | n_truth | n_missing_sim |",
        "|---|---|---:|---:|---:|",
    ]
    for scenario, cobj in (("baseline", baseline_custom or {}), ("candidate", candidate_custom or {})):
        gd = cobj.get("group_details", {})
        for g in GROUP_KEYS:
            d = gd.get(g, {})
            md_lines.append(
                f"| {scenario} | {g} | {base.cn(d.get('mean_rel_error'), 6)} | {int(d.get('n_truth', 0))} | {int(d.get('n_missing_sim', 0))} |"
            )

    md_lines.extend(
        [
            "",
            "## Final Score",
            "",
            f"- baseline all_truth_score: {base.cn((baseline_custom or {}).get('all_truth_score'), 6)}",
            f"- candidate all_truth_score: {base.cn((candidate_custom or {}).get('all_truth_score'), 6)}",
            "",
        ]
    )
    base.write_text(iter_dir / "prediction_vs_truth_summary.md", "\n".join(md_lines))


def write_index_row(row: dict):
    header = [
        "iteration",
        "timestamp",
        "change_scope",
        "wheat_cultivar",
        "maize_cultivar",
        "baseline_custom_score",
        "candidate_custom_score",
        "improvement_pct",
        "is_best",
    ]
    write_header = not INDEX_PATH.exists()
    with INDEX_PATH.open("a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        if write_header:
            w.writeheader()
        w.writerow(row)


def read_last_iter():
    if not INDEX_PATH.exists():
        return 0
    with INDEX_PATH.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return 0
    return max(int(r["iteration"]) for r in rows)


def get_wheat_density(truth_text: str) -> float:
    m = re.search(r"<manager2 name=\"Wheat Management\">.*?<density1[^>]*>([^<]+)</density1>", truth_text, re.S)
    if not m:
        raise ValueError("Cannot find Wheat Management.density1")
    return float(m.group(1).strip())


def set_wheat_density(truth_text: str, value: float) -> str:
    p = re.compile(r"(<manager2 name=\"Wheat Management\">.*?<density1[^>]*>)([^<]+)(</density1>)", re.S)
    vtxt = base.fmt(value, 1)
    new_text, n = p.subn(lambda m: f"{m.group(1)}{vtxt}{m.group(3)}", truth_text, count=1)
    if n != 1:
        raise ValueError("Cannot set Wheat Management.density1")
    return new_text


def get_wheat_sowing_fert_amt(truth_text: str) -> float:
    m = re.search(r"<manager2 name=\"Wheat Sowing Fertiliser\">.*?<fertAmt[^>]*>([^<]+)</fertAmt>", truth_text, re.S)
    if not m:
        raise ValueError("Cannot find Wheat Sowing Fertiliser.fertAmt")
    return float(m.group(1).strip())


def set_wheat_sowing_fert_amt(truth_text: str, value: float) -> str:
    p = re.compile(r"(<manager2 name=\"Wheat Sowing Fertiliser\">.*?<fertAmt[^>]*>)([^<]+)(</fertAmt>)", re.S)
    vtxt = base.fmt(value, 1)
    new_text, n = p.subn(lambda m: f"{m.group(1)}{vtxt}{m.group(3)}", truth_text, count=1)
    if n != 1:
        raise ValueError("Cannot set Wheat Sowing Fertiliser.fertAmt")
    return new_text


def mutate_wheat_fert_amt(base_amt: float, seed: int, profile_idx: int, step_scale: float = 1.0) -> float:
    profiles = [25, -20, 35, -30, 15, -12]
    rnd = random.Random(seed)
    d = profiles[profile_idx % len(profiles)]
    scale = max(0.05, float(step_scale))
    val = base_amt + d * scale + rnd.uniform(-5.0 * scale, 5.0 * scale)
    return base.clamp(round(val, 1), 60.0, 360.0)


def mutate_wheat_density(
    base_density: float,
    seed: int,
    profile_idx: int,
    step_scale: float = 1.0,
    min_density: float = 70.0,
    max_density: float = 380.0,
) -> float:
    # Stronger search for wheat biomass mismatch; keep within practical density range.
    profiles = [-140, -120, -100, -80, -60, -40, -20, 10]
    rnd = random.Random(seed)
    d = profiles[profile_idx % len(profiles)]
    scale = max(0.05, float(step_scale))
    val = base_density + d * scale + rnd.uniform(-8.0 * scale, 8.0 * scale)
    lo = min(min_density, max_density)
    hi = max(min_density, max_density)
    return base.clamp(round(val, 1), lo, hi)


def mutate_wheat_stem_bias(base_params: dict, seed: int, profile_idx: int) -> dict:
    # Bias toward better late stem/total biomass retention with moderated grain sink pressure.
    profiles = [
        {
            "vern_sens": 0.02,
            "photop_sens": 0.02,
            "tt_end_of_juvenile": 8,
            "tt_floral_initiation": 10,
            "tt_flowering": 2,
            "tt_start_grain_fill": 16,
            "startgf_to_mat": 26,
            "grains_per_gram_stem": -0.8,
            "max_grain_size": -0.00020,
            "potential_grain_filling_rate": -0.00003,
        },
        {
            "vern_sens": 0.01,
            "photop_sens": 0.01,
            "tt_end_of_juvenile": 6,
            "tt_floral_initiation": 8,
            "tt_flowering": 2,
            "tt_start_grain_fill": 22,
            "startgf_to_mat": 34,
            "grains_per_gram_stem": -1.2,
            "max_grain_size": -0.00030,
            "potential_grain_filling_rate": -0.00005,
        },
        {
            "vern_sens": 0.00,
            "photop_sens": 0.00,
            "tt_end_of_juvenile": 4,
            "tt_floral_initiation": 6,
            "tt_flowering": 1,
            "tt_start_grain_fill": 12,
            "startgf_to_mat": 20,
            "grains_per_gram_stem": -0.6,
            "max_grain_size": -0.00012,
            "potential_grain_filling_rate": -0.00002,
        },
    ]
    jitter = {
        "vern_sens": 0.01,
        "photop_sens": 0.01,
        "tt_end_of_juvenile": 2.0,
        "tt_floral_initiation": 2.0,
        "tt_flowering": 1.0,
        "tt_start_grain_fill": 3.0,
        "startgf_to_mat": 4.0,
        "grains_per_gram_stem": 0.25,
        "max_grain_size": 0.00012,
        "potential_grain_filling_rate": 0.000015,
    }
    bounds = {
        "vern_sens": (0.8, 5.0),
        "photop_sens": (0.8, 5.0),
        "tt_end_of_juvenile": (260, 520),
        "tt_floral_initiation": (320, 700),
        "tt_flowering": (80, 220),
        "tt_start_grain_fill": (450, 760),
        "startgf_to_mat": (520, 760),
        "grains_per_gram_stem": (18, 42),
        "max_grain_size": (0.035, 0.075),
        "potential_grain_filling_rate": (0.0015, 0.0035),
    }
    rnd = random.Random(seed)
    d = profiles[profile_idx % len(profiles)]
    p = dict(base_params)
    for k, dv in d.items():
        val = base_params[k] + dv + rnd.uniform(-jitter[k], jitter[k])
        lo, hi = bounds[k]
        p[k] = base.clamp(val, lo, hi)
    return p


def sobol_param_key(crop: str, param_name: str) -> str:
    return PARAM_ALIASES.get(crop, {}).get(param_name, param_name)


def sobol_phase_to_crop_group(phase: str, crop_priority: str, k: int):
    if phase.startswith("maize_"):
        crop = "maize"
    elif phase.startswith("wheat_"):
        crop = "wheat"
    elif crop_priority == "wheat_first":
        crop = "wheat"
    elif crop_priority == "balanced":
        crop = "maize" if (k % 2 == 0) else "wheat"
    else:
        crop = "maize"

    if phase.endswith("_phenology"):
        group = "phenology"
    elif phase.endswith("_yield_component"):
        group = "yield_component"
    elif phase == "biomass_canopy":
        group = "biomass_canopy"
    else:
        group = "phenology"
    return crop, group


def infer_sobol_phase(baseline_eval: dict, args) -> str:
    """根据当前 baseline 误差自动选择阶段；显式 --sobol_phase 会优先。"""
    if args.sobol_phase != "auto":
        return args.sobol_phase
    crops = baseline_eval.get("crops", {})
    maize_ph = (crops.get("maize") or {}).get("phenology_error_days_mean")
    wheat_ph = (crops.get("wheat") or {}).get("phenology_error_days_mean")
    guard = float(args.pheno_guard_days)
    if args.crop_priority in ("maize_first", "balanced") and maize_ph is not None and float(maize_ph) > guard:
        return "maize_phenology"
    if args.crop_priority in ("wheat_first", "balanced") and wheat_ph is not None and float(wheat_ph) > guard:
        return "wheat_phenology"
    if args.crop_priority == "wheat_first":
        return "wheat_yield_component"
    return "maize_yield_component"


def _signed_rel(sim, obs):
    if sim is None or obs is None:
        return None
    try:
        return (float(sim) - float(obs)) / max(abs(float(obs)), EPS)
    except Exception:
        return None


def _mean_signed_rel_from_rows(rows: list, crop: str, variable_aliases: list):
    vals = []
    aliases = {_normalize_col_name(x) for x in variable_aliases}
    for r in rows or []:
        if str(r.get("crop", "")).lower() != crop:
            continue
        var = _normalize_col_name(r.get("variable") or r.get("variable_name"))
        if var not in aliases:
            continue
        rel = _signed_rel(r.get("sim_value"), r.get("obs_value"))
        if rel is not None:
            vals.append(rel)
    return mean_valid(vals)


def build_sobol_diagnosis(baseline_eval: dict, baseline_rows: list | None = None) -> dict:
    """抽取带方向的误差诊断，供 Sobol 分阶段突变决定上调/下调。"""
    diag = {}
    for crop in ("maize", "wheat"):
        c = (baseline_eval.get("crops") or {}).get(crop) or {}
        diag[f"{crop}_phenology_error_days_mean"] = c.get("phenology_error_days_mean")
        diag[f"{crop}_period_bias_days"] = None
        if c.get("sim_period_days") is not None and c.get("truth_period_days") is not None:
            diag[f"{crop}_period_bias_days"] = float(c["sim_period_days"]) - float(c["truth_period_days"])
        diag[f"{crop}_yield_bias_rel"] = _signed_rel(c.get("yield_sim_kg_ha"), c.get("yield_obs_kg_ha"))
        diag[f"{crop}_grain_number_bias_rel"] = _mean_signed_rel_from_rows(
            baseline_rows,
            crop,
            ["grain_number", "grain_no", "grainnum", "kernel_number", "grains_per_m2"],
        )
        diag[f"{crop}_grain_weight_bias_rel"] = _mean_signed_rel_from_rows(
            baseline_rows,
            crop,
            ["grain_weight", "grain_wt", "grain_size", "kernel_weight", "1000_grain_weight"],
        )
    return diag


def choose_sobol_param(crop: str, group: str, k: int, params: dict) -> str:
    priority = SOBOL_PRIORITY.get(crop, {}).get(group, [])
    available = [p for p in priority if sobol_param_key(crop, p) in params]
    if not available:
        raise ValueError(f"sobol_phased 阶段 {crop}.{group} 没有可解析的候选参数；请检查 XML 或解析函数。")
    return available[k % len(available)]


def pick_sobol_phased_action(k: int, baseline_eval: dict, args, current_state: dict, baseline_rows: list | None = None) -> dict:
    phase = infer_sobol_phase(baseline_eval, args)
    crop, group = sobol_phase_to_crop_group(phase, args.crop_priority, k)
    params = current_state.get(f"{crop}_params") or {}
    try:
        param_name = choose_sobol_param(crop, group, k, params)
    except ValueError:
        if group != "biomass_canopy":
            raise
        alt_crop = "maize" if crop == "wheat" else "wheat"
        alt_params = current_state.get(f"{alt_crop}_params") or {}
        param_name = choose_sobol_param(alt_crop, group, k, alt_params)
        crop = alt_crop
    return {
        "action": f"{crop}_cultivar",
        "sobol_phase": phase,
        "crop": crop,
        "parameter_name": param_name,
        "parameter_key": sobol_param_key(crop, param_name),
        "parameter_group": group,
        "diagnosis": build_sobol_diagnosis(baseline_eval, baseline_rows),
    }


def _sobol_effective_frac(step_scale: float, lo: float = 0.02, hi: float = 0.08) -> float:
    # 命令行里的 step_scale 延续旧脚本语义；这里折算成每轮 2%-8% 的保守步幅。
    return base.clamp(float(step_scale) * 0.35, lo, hi)


def sobol_step(old_value: float, crop: str, param_name: str, step_scale: float) -> float:
    p = sobol_param_key(crop, param_name)
    old = abs(float(old_value))
    if p.startswith("tt_") or p in ("tt_start_grain_fill", "tt_end_of_juvenile", "tt_floral_initiation"):
        min_step = 2.0 if old < 100.0 else 10.0
        return base.clamp(old * _sobol_effective_frac(step_scale), min_step, 50.0)
    if p in ("photop_sens", "vern_sens"):
        return base.clamp(float(step_scale), 0.05, 0.20)
    if p == "max_grain_size":
        return old * base.clamp(float(step_scale) * 0.25, 0.01, 0.05)
    if p == "rue":
        return old * base.clamp(float(step_scale) * 0.15, 0.01, 0.03)
    if p.startswith("largestLeaf"):
        return old * base.clamp(float(step_scale) * 0.25, 0.01, 0.05)
    return old * _sobol_effective_frac(step_scale)


def sobol_direction(crop: str, param_name: str, phase: str, diagnosis: dict) -> tuple[int, str, str, str]:
    period_bias = diagnosis.get(f"{crop}_period_bias_days")
    yield_bias = diagnosis.get(f"{crop}_yield_bias_rel")
    grain_no_bias = diagnosis.get(f"{crop}_grain_number_bias_rel")
    grain_wt_bias = diagnosis.get(f"{crop}_grain_weight_bias_rel")
    p = sobol_param_key(crop, param_name)

    # 正方向表示提高参数值，负方向表示降低参数值。
    if "phenology" in phase:
        if period_bias is not None and period_bias > 1:
            return -1, "down", f"{crop} 模拟物候期偏长/偏晚 {period_bias:.1f} 天", "缩短相关热时间，促使发育提前"
        if period_bias is not None and period_bias < -1:
            return 1, "up", f"{crop} 模拟物候期偏短/偏早 {period_bias:.1f} 天", "延长相关热时间，推迟发育"
        return 1, "up", f"{crop} 物候误差已接近阈值，采用小幅上调探索", "验证物候边界内的局部改进"

    if "yield_component" in phase:
        if p in ("tt_flag_to_flower", "tt_endjuv_to_init", "tt_floral_initiation", "tt_end_of_juvenile", "photop_sens", "vern_sens"):
            b = grain_no_bias if grain_no_bias is not None else yield_bias
            if b is not None and b < -0.03:
                return 1, "up", f"{crop} 粒数/产量模拟偏低，signed_rel={b:.3f}", "延长开花前发育或增强响应，增加潜在库形成"
            if b is not None and b > 0.03:
                return -1, "down", f"{crop} 粒数/产量模拟偏高，signed_rel={b:.3f}", "缩短开花前发育或降低响应，避免过高库容量"
        if p in ("tt_flower_to_maturity", "tt_start_grain_fill", "max_grain_size"):
            b = grain_wt_bias if grain_wt_bias is not None else yield_bias
            if b is not None and b < -0.03:
                return 1, "up", f"{crop} 粒重/产量模拟偏低，signed_rel={b:.3f}", "增加灌浆持续时间或潜在粒重"
            if b is not None and b > 0.03:
                return -1, "down", f"{crop} 粒重/产量模拟偏高，signed_rel={b:.3f}", "降低灌浆持续时间或潜在粒重"
        return 1, "up", f"{crop} 产量构成缺少直接 signed 诊断，按保守小步上调探索", "保持单参数、小步幅并由评分函数决定是否接受"

    if "biomass_canopy" in phase:
        if yield_bias is not None and yield_bias < -0.03:
            return 1, "up", f"{crop} 产量模拟偏低，signed_rel={yield_bias:.3f}", "小幅提高光能利用或冠层能力"
        if yield_bias is not None and yield_bias > 0.03:
            return -1, "down", f"{crop} 产量模拟偏高，signed_rel={yield_bias:.3f}", "小幅降低光能利用或冠层能力"
        return 1, "up", f"{crop} 冠层/生物量阶段无强 signed 诊断，保守小步探索", "只在物候守门通过时接受"

    return 1, "up", "未识别阶段，保守小步探索", "由候选评分和物候守门决定是否接受"


def clamp_sobol_param(crop: str, param_name: str, value: float) -> float:
    bounds = PARAM_BOUNDS.get((crop, param_name))
    if bounds is None:
        bounds = PARAM_BOUNDS.get((crop, sobol_param_key(crop, param_name)))
    if bounds is None:
        return float(value)
    return base.clamp(float(value), bounds[0], bounds[1])


def mutate_sobol_param(base_params: dict, crop: str, param_name: str, diagnosis: dict, step_scale: float) -> tuple[dict, dict]:
    p = dict(base_params)
    key = sobol_param_key(crop, param_name)
    if key not in p:
        raise ValueError(f"{crop} 当前解析结果中缺少 Sobol 参数 {param_name} -> {key}")
    old = float(p[key])
    sign, direction, reason, expected = sobol_direction(crop, param_name, diagnosis.get("sobol_phase", ""), diagnosis)
    step = sobol_step(old, crop, param_name, step_scale)
    new = clamp_sobol_param(crop, param_name, old + sign * step)
    if abs(new - old) <= 1e-12:
        new = clamp_sobol_param(crop, param_name, old - sign * step)
        direction = "down" if direction == "up" else "up"
        reason = f"{reason}；原方向触及参数边界，已自动反向做小步测试"
    p[key] = new
    meta = {
        "crop": crop,
        "parameter_name": param_name,
        "parameter_key": key,
        "old_value": old,
        "new_value": new,
        "step": step,
        "adjustment_direction": direction,
        "diagnosis_reason": reason,
        "expected_effect": expected,
        "risk": "单参数变化仍可能改变 flowering/maturity；若物候守门不通过或综合评分变差，会自动回退。",
    }
    return p, meta


def mutate_maize_sobol_param(base_params: dict, param_name: str, diagnosis: dict, step_scale: float, seed: int):
    return mutate_sobol_param(base_params, "maize", param_name, diagnosis, step_scale)


def mutate_wheat_sobol_param(base_params: dict, param_name: str, diagnosis: dict, step_scale: float, seed: int):
    return mutate_sobol_param(base_params, "wheat", param_name, diagnosis, step_scale)


def write_sobol_stage_alignment(path: Path, plan: dict, meta: dict, metrics: dict, accepted: bool) -> None:
    b = metrics.get("baseline_custom", {})
    c = metrics.get("candidate_custom", {})
    comparison = metrics.get("comparison", {})
    fields = [
        "sobol_phase",
        "crop",
        "parameter_changed",
        "old_value",
        "new_value",
        "adjustment_direction",
        "diagnosis_reason",
        "expected_effect",
        "risk",
        "baseline_score",
        "candidate_score",
        "whether_phenology_passed",
        "whether_yield_component_improved",
        "whether_total_score_improved",
        "whether_candidate_accepted",
    ]
    row = {
        "sobol_phase": plan.get("sobol_phase"),
        "crop": meta.get("crop"),
        "parameter_changed": meta.get("parameter_name"),
        "old_value": meta.get("old_value"),
        "new_value": meta.get("new_value"),
        "adjustment_direction": meta.get("adjustment_direction"),
        "diagnosis_reason": meta.get("diagnosis_reason"),
        "expected_effect": meta.get("expected_effect"),
        "risk": meta.get("risk"),
        "baseline_score": b.get("custom_score"),
        "candidate_score": c.get("custom_score"),
        "whether_phenology_passed": c.get("phenology_error_days_max") is not None
        and c.get("phenology_error_days_max") <= metrics.get("constraints", {}).get("phenology_error_days_max_le", 9999),
        "whether_yield_component_improved": (
            (c.get("group_scores") or {}).get("yield") is not None
            and (b.get("group_scores") or {}).get("yield") is not None
            and (c.get("group_scores") or {}).get("yield") <= (b.get("group_scores") or {}).get("yield")
        ),
        "whether_total_score_improved": comparison.get("custom_score_improvement_pct", 0) > 0,
        "whether_candidate_accepted": accepted,
    }
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(row)


def _num_or_none(v):
    if v is None:
        return None
    try:
        x = float(v)
    except Exception:
        return None
    if math.isnan(x):
        return None
    return x


def get_crop_yield_errors(eval_obj: dict) -> dict:
    crops = eval_obj.get("crops") or {}
    return {
        "wheat": _num_or_none((crops.get("wheat") or {}).get("yield_error_rel")),
        "maize": _num_or_none((crops.get("maize") or {}).get("yield_error_rel")),
    }


def score_water_yield_objective(eval_obj: dict, custom_obj: dict, args) -> dict:
    group_scores = custom_obj.get("group_scores") or {}
    soil_water = _num_or_none(group_scores.get("soil_water"))
    total_bio = _num_or_none(group_scores.get("total_biomass"))
    structure = _num_or_none(group_scores.get("structure_biomass_leaf_stem"))
    pheno_days = _num_or_none(custom_obj.get("phenology_error_days_max"))
    yield_errors = get_crop_yield_errors(eval_obj)
    wheat_y = yield_errors["wheat"]
    maize_y = yield_errors["maize"]
    valid_y = [v for v in (wheat_y, maize_y) if v is not None]
    mean_y = mean_valid(valid_y)
    exceed = 0.0
    for v in valid_y:
        exceed += max(0.0, v - float(args.target_yield_rel))
    if mean_y is None:
        yield_penalty = 1.0 + 2.0 * float(args.target_yield_rel)
    elif exceed > 0:
        yield_penalty = mean_y + 2.0 * exceed
    else:
        yield_penalty = mean_y

    if pheno_days is None:
        phenology_penalty = 1.0
    else:
        phenology_penalty = min(pheno_days, 30.0) / 30.0
        if pheno_days > float(args.pheno_guard_days):
            phenology_penalty += 0.5 + 0.02 * (pheno_days - float(args.pheno_guard_days))

    biomass_structure_penalty = 0.0
    if total_bio is not None:
        biomass_structure_penalty += 0.025 * total_bio
    if structure is not None:
        biomass_structure_penalty += 0.025 * structure

    score = (
        float(args.soil_water_priority_weight) * (1.0 if soil_water is None else soil_water)
        + float(args.yield_constraint_weight) * yield_penalty
        + float(args.phenology_constraint_weight) * phenology_penalty
        + biomass_structure_penalty
    )
    return {
        "water_yield_score": score,
        "soil_water_error": soil_water,
        "wheat_yield_error": wheat_y,
        "maize_yield_error": maize_y,
        "mean_yield_error": mean_y,
        "yield_penalty": yield_penalty,
        "yield_exceed_amount": exceed,
        "phenology_error_days_max": pheno_days,
        "phenology_penalty": phenology_penalty,
        "total_biomass_error": total_bio,
        "structure_error": structure,
        "biomass_structure_penalty": biomass_structure_penalty,
        "yield_constraint_passed": (
            wheat_y is not None
            and maize_y is not None
            and wheat_y <= float(args.target_yield_rel)
            and maize_y <= float(args.target_yield_rel)
        ),
        "phenology_passed": pheno_days is not None and pheno_days <= float(args.pheno_guard_days),
    }


def score_hdsw_water_yield_objective(eval_obj: dict, custom_obj: dict, args) -> dict:
    """HDSW soil 基础下的水分-产量目标函数。

    soil_water 是主目标，wheat/maize yield 是硬约束，phenology 是守门约束；
    biomass/structure 只作为稳定性惩罚，防止为了水分过拟合而让植株过程崩掉。
    """
    group_scores = custom_obj.get("group_scores") or {}
    soil_water = _num_or_none(group_scores.get("soil_water"))
    total_bio = _num_or_none(group_scores.get("total_biomass"))
    structure = _num_or_none(group_scores.get("structure_biomass_leaf_stem"))
    lai = _num_or_none(group_scores.get("LAI"))
    pheno_days = _num_or_none(custom_obj.get("phenology_error_days_max"))
    yield_errors = get_crop_yield_errors(eval_obj)
    wheat_y = yield_errors["wheat"]
    maize_y = yield_errors["maize"]
    valid_y = [v for v in (wheat_y, maize_y) if v is not None]
    mean_y = mean_valid(valid_y)
    exceed = sum(max(0.0, v - float(args.target_yield_rel)) for v in valid_y)
    if mean_y is None:
        yield_penalty = 1.0 + 2.0 * float(args.target_yield_rel)
    elif exceed > 0:
        yield_penalty = mean_y + 2.0 * exceed
    else:
        yield_penalty = mean_y

    if pheno_days is None:
        phenology_penalty = 1.0
    else:
        phenology_penalty = min(pheno_days, 30.0) / 30.0
        if pheno_days > float(args.pheno_guard_days):
            phenology_penalty += 0.5 + 0.02 * (pheno_days - float(args.pheno_guard_days))

    stability_vals = [v for v in (total_bio, structure) if v is not None]
    stability_penalty = mean_valid(stability_vals) if stability_vals else 1.0
    score = (
        float(args.soil_water_priority_weight) * (1.0 if soil_water is None else soil_water)
        + float(args.yield_constraint_weight) * yield_penalty
        + float(args.phenology_constraint_weight) * phenology_penalty
        + float(args.stability_weight) * stability_penalty
    )
    return {
        "hdsw_water_yield_score": score,
        "water_yield_score": score,
        "soil_water_error": soil_water,
        "wheat_yield_error": wheat_y,
        "maize_yield_error": maize_y,
        "mean_yield_error": mean_y,
        "yield_penalty": yield_penalty,
        "yield_exceed_amount": exceed,
        "phenology_error_days_max": pheno_days,
        "phenology_penalty": phenology_penalty,
        "total_biomass_error": total_bio,
        "structure_error": structure,
        "lai_error": lai,
        "stability_penalty": stability_penalty,
        "yield_constraint_passed": (
            wheat_y is not None
            and maize_y is not None
            and wheat_y <= float(args.target_yield_rel)
            and maize_y <= float(args.target_yield_rel)
        ),
        "phenology_passed": pheno_days is not None and pheno_days <= float(args.pheno_guard_days),
    }


def diagnose_soil_water_error(rows: list) -> dict:
    out = {
        "layers": {},
        "surface_bias": None,
        "deep_bias": None,
        "overall_bias": None,
        "recommended_action": "no_change",
        "top_error_dates": [],
    }
    all_signed = []
    top_records = []
    for layer in ("water_1", "water_2", "water_3", "water_4", "water_5"):
        vals, signed, abs_errs = [], [], []
        for r in rows or []:
            if r.get("variable") != layer:
                continue
            sim = _num_or_none(r.get("sim_value"))
            obs = _num_or_none(r.get("obs_value"))
            rel = _num_or_none(r.get("rel_error"))
            if sim is None or obs is None:
                continue
            s = sim - obs
            signed.append(s)
            all_signed.append(s)
            abs_err = abs(s)
            abs_errs.append(abs_err)
            if rel is not None:
                vals.append(rel)
            top_records.append(
                {
                    "date": r.get("date"),
                    "layer": layer,
                    "sim_value": sim,
                    "obs_value": obs,
                    "signed_error": s,
                    "abs_error": abs_err,
                    "rel_error": rel,
                }
            )
        mean_signed = mean_valid(signed)
        bias = "unknown"
        if mean_signed is not None:
            if mean_signed > 0.2:
                bias = "wet"
            elif mean_signed < -0.2:
                bias = "dry"
            else:
                bias = "near_zero"
        out["layers"][layer] = {
            "mean_rel_error": mean_valid(vals),
            "mean_signed_error": mean_signed,
            "mean_abs_error": mean_valid(abs_errs),
            "bias": bias,
            "n": len(abs_errs),
        }

    out["surface_bias"] = mean_valid(
        [out["layers"][x]["mean_signed_error"] for x in ("water_1", "water_2") if out["layers"][x]["mean_signed_error"] is not None]
    )
    out["deep_bias"] = mean_valid(
        [out["layers"][x]["mean_signed_error"] for x in ("water_4", "water_5") if out["layers"][x]["mean_signed_error"] is not None]
    )
    out["overall_bias"] = mean_valid(all_signed)
    top_records = [r for r in top_records if r.get("rel_error") is not None]
    top_records.sort(key=lambda r: abs(float(r.get("rel_error") or 0)), reverse=True)
    out["top_error_dates"] = top_records[:10]

    surface = out["surface_bias"]
    deep = out["deep_bias"]
    overall = out["overall_bias"]
    if overall is None:
        out["recommended_action"] = "no_change"
    elif surface is not None and surface < -0.5 and (deep is None or deep <= 0.5):
        out["recommended_action"] = "increase_water"
    elif surface is not None and surface > 0.5:
        out["recommended_action"] = "decrease_water"
    elif deep is not None and deep > 0.5:
        out["recommended_action"] = "reduce_single_amount"
    elif overall < -0.5:
        out["recommended_action"] = "increase_events"
    elif overall > 0.5:
        out["recommended_action"] = "reduce_events"
    return out


def diagnose_yield_error(eval_obj: dict, args) -> dict:
    errors = get_crop_yield_errors(eval_obj)
    crops = eval_obj.get("crops") or {}
    out = {
        "wheat_yield_error": errors.get("wheat"),
        "maize_yield_error": errors.get("maize"),
        "wheat_bias": "unknown",
        "maize_bias": "unknown",
        "yield_constraint_passed": False,
        "crop_to_recover": "none",
    }
    for crop in ("wheat", "maize"):
        c = crops.get(crop) or {}
        sim = _num_or_none(c.get("yield_sim_kg_ha"))
        obs = _num_or_none(c.get("yield_obs_kg_ha"))
        if sim is not None and obs is not None:
            if sim > obs:
                out[f"{crop}_bias"] = "sim_high"
            elif sim < obs:
                out[f"{crop}_bias"] = "sim_low"
            else:
                out[f"{crop}_bias"] = "near_zero"
    wy, my = errors.get("wheat"), errors.get("maize")
    out["yield_constraint_passed"] = (
        wy is not None and my is not None and wy <= float(args.target_yield_rel) and my <= float(args.target_yield_rel)
    )
    bad = {k: v for k, v in errors.items() if v is None or v > float(args.target_yield_rel)}
    if bad:
        out["crop_to_recover"] = max(bad, key=lambda c: 999.0 if bad[c] is None else bad[c])
    return out


def _degrade_too_much(base_val, cand_val, max_frac: float = 0.20) -> bool:
    if base_val is None or cand_val is None:
        return False
    return float(cand_val) > float(base_val) * (1.0 + max_frac)


def is_water_yield_candidate_acceptable(baseline_custom: dict, candidate_custom: dict, args) -> dict:
    reasons = []
    bw, bm = baseline_custom.get("wheat_yield_error"), baseline_custom.get("maize_yield_error")
    cw, cm = candidate_custom.get("wheat_yield_error"), candidate_custom.get("maize_yield_error")
    b_soil, c_soil = baseline_custom.get("soil_water_error"), candidate_custom.get("soil_water_error")
    b_score, c_score = baseline_custom.get("water_yield_score"), candidate_custom.get("water_yield_score")
    pheno_ok = bool(candidate_custom.get("phenology_passed"))
    yield_pass = bool(candidate_custom.get("yield_constraint_passed"))
    baseline_yield_pass = (
        bw is not None
        and bm is not None
        and bw <= float(args.target_yield_rel)
        and bm <= float(args.target_yield_rel)
    )
    soil_improved = (
        b_soil is not None
        and c_soil is not None
        and (float(b_soil) - float(c_soil)) >= float(args.min_soil_water_improvement)
    )
    score_improved = b_score is not None and c_score is not None and float(c_score) < float(b_score)
    bio_bad = _degrade_too_much(baseline_custom.get("total_biomass_error"), candidate_custom.get("total_biomass_error"))
    structure_bad = _degrade_too_much(baseline_custom.get("structure_error"), candidate_custom.get("structure_error"))

    if not pheno_ok:
        reasons.append("candidate 物候误差超过 pheno_guard_days")
    if bio_bad:
        reasons.append("total_biomass 误差较 baseline 恶化超过 20%")
    if structure_bad:
        reasons.append("structure 误差较 baseline 恶化超过 20%")

    recovery_search = not baseline_yield_pass
    if recovery_search:
        old_max = max([v for v in (bw, bm) if v is not None], default=None)
        new_max = max([v for v in (cw, cm) if v is not None], default=None)
        yield_recovered = old_max is not None and new_max is not None and new_max < old_max
        soil_not_much_worse = b_soil is not None and c_soil is not None and float(c_soil) <= float(b_soil) + float(args.min_soil_water_improvement)
        accepted = pheno_ok and not bio_bad and not structure_bad and yield_recovered and soil_not_much_worse and score_improved
        if not yield_recovered:
            reasons.append("当前 baseline 产量未达硬约束，candidate 没有降低最差作物产量误差")
        if not soil_not_much_worse:
            reasons.append("恢复性搜索中 soil_water 恶化过多")
        if not score_improved:
            reasons.append("water_yield_score 未改善")
        return {
            "accepted": accepted,
            "reason": "；".join(reasons) if reasons else "当前 baseline 尚未满足产量硬约束，本轮属于恢复性搜索，candidate 改善产量且未显著恶化 soil_water。",
            "recovery_search": True,
            "soil_improved": soil_improved,
            "yield_constraint_passed": yield_pass,
            "phenology_passed": pheno_ok,
            "score_improved": score_improved,
        }

    if not yield_pass:
        reasons.append("candidate 的 wheat/maize yield error 未同时控制在目标阈值内")
    if not soil_improved:
        reasons.append("soil_water 改善幅度小于 min_soil_water_improvement")
    if not score_improved:
        reasons.append("water_yield_score 未改善")

    accepted = pheno_ok and yield_pass and soil_improved and score_improved and not bio_bad and not structure_bad
    return {
        "accepted": accepted,
        "reason": "；".join(reasons) if reasons else "soil_water 明显改善，产量硬约束和物候守门均通过。",
        "recovery_search": False,
        "soil_improved": soil_improved,
        "yield_constraint_passed": yield_pass,
        "phenology_passed": pheno_ok,
        "score_improved": score_improved,
    }


def success_reached_water_yield(candidate_custom: dict, args) -> bool:
    return (
        candidate_custom.get("yield_constraint_passed")
        and candidate_custom.get("phenology_passed")
        and candidate_custom.get("soil_water_error") is not None
        and candidate_custom.get("soil_water_error") <= float(args.target_soil_water_rel)
    )


def is_hdsw_water_yield_candidate_acceptable(baseline_custom: dict, candidate_custom: dict, args, irrigation_meta: dict | None = None) -> dict:
    reasons = []
    bw, bm = baseline_custom.get("wheat_yield_error"), baseline_custom.get("maize_yield_error")
    cw, cm = candidate_custom.get("wheat_yield_error"), candidate_custom.get("maize_yield_error")
    b_soil, c_soil = baseline_custom.get("soil_water_error"), candidate_custom.get("soil_water_error")
    b_score = baseline_custom.get("hdsw_water_yield_score", baseline_custom.get("water_yield_score"))
    c_score = candidate_custom.get("hdsw_water_yield_score", candidate_custom.get("water_yield_score"))
    pheno_ok = bool(candidate_custom.get("phenology_passed"))
    yield_pass = bool(candidate_custom.get("yield_constraint_passed"))
    baseline_yield_pass = (
        bw is not None and bm is not None and bw <= float(args.target_yield_rel) and bm <= float(args.target_yield_rel)
    )
    soil_improved = (
        b_soil is not None and c_soil is not None and (float(b_soil) - float(c_soil)) >= float(args.min_soil_water_improvement)
    )
    soil_not_worse = b_soil is not None and c_soil is not None and float(c_soil) <= float(b_soil) + float(args.min_soil_water_improvement)
    score_improved = b_score is not None and c_score is not None and float(c_score) < float(b_score)
    bio_bad = _degrade_too_much(baseline_custom.get("total_biomass_error"), candidate_custom.get("total_biomass_error"))
    structure_bad = _degrade_too_much(baseline_custom.get("structure_error"), candidate_custom.get("structure_error"))
    total_irrig = None
    if irrigation_meta:
        total_irrig = irrigation_meta.get("total_irrigation_mm_after")
    irrigation_ok = total_irrig is None or float(total_irrig) <= float(args.max_total_irrigation_mm)

    if not pheno_ok:
        reasons.append("candidate 物候误差超过 pheno_guard_days")
    if bio_bad:
        reasons.append("total_biomass 误差较 baseline 恶化超过 20%")
    if structure_bad:
        reasons.append("structure 误差较 baseline 恶化超过 20%")
    if not irrigation_ok:
        reasons.append("灌溉总量超过 max_total_irrigation_mm")

    if not baseline_yield_pass:
        old_max = max([v for v in (bw, bm) if v is not None], default=None)
        new_max = max([v for v in (cw, cm) if v is not None], default=None)
        old_mean = mean_valid([bw, bm])
        new_mean = mean_valid([cw, cm])
        yield_recovered = (
            old_max is not None and new_max is not None and new_max < old_max - 1e-9
        ) or (
            old_mean is not None and new_mean is not None and new_mean < old_mean - 1e-9
        )
        accepted = pheno_ok and irrigation_ok and not bio_bad and not structure_bad and yield_recovered and soil_not_worse
        if not yield_recovered:
            reasons.append("当前 baseline 未满足 yield 硬约束，candidate 没有降低作物产量误差")
        if not soil_not_worse:
            reasons.append("恢复性搜索中 soil_water 明显恶化")
        return {
            "accepted": accepted,
            "reason": "；".join(reasons) if reasons else "当前 baseline 尚未满足 yield 硬约束，本轮属于恢复性搜索，candidate 改善产量且未明显恶化 soil_water。",
            "recovery_search": True,
            "soil_improved": soil_improved,
            "yield_constraint_passed": yield_pass,
            "phenology_passed": pheno_ok,
            "score_improved": score_improved,
        }

    if not yield_pass:
        reasons.append("candidate 的 wheat/maize yield error 未同时控制在目标阈值内")
    if not soil_improved:
        reasons.append("soil_water 改善幅度小于 min_soil_water_improvement")
    if not score_improved:
        reasons.append("hdsw_water_yield_score 未改善")
    accepted = pheno_ok and yield_pass and soil_improved and score_improved and irrigation_ok and not bio_bad and not structure_bad
    return {
        "accepted": accepted,
        "reason": "；".join(reasons) if reasons else "soil_water 明显改善，yield 硬约束和物候守门均通过。",
        "recovery_search": False,
        "soil_improved": soil_improved,
        "yield_constraint_passed": yield_pass,
        "phenology_passed": pheno_ok,
        "score_improved": score_improved,
    }


def success_reached_hdsw_water_yield(candidate_custom: dict, args) -> bool:
    return (
        candidate_custom.get("yield_constraint_passed")
        and candidate_custom.get("phenology_passed")
        and candidate_custom.get("soil_water_error") is not None
        and candidate_custom.get("soil_water_error") <= float(args.target_soil_water_rel)
    )


def extract_soil_block(truth_text: str) -> str:
    m = re.search(r"(?s)<Soil name=\"soil\">.*?</Soil>", truth_text)
    if not m:
        raise ValueError("未找到 <Soil name=\"soil\"> 模块")
    return m.group(0)


def replace_soil_block(truth_text: str, soil_block: str) -> str:
    matches = list(re.finditer(r"(?s)<Soil name=\"soil\">.*?</Soil>", truth_text))
    if len(matches) != 1:
        raise ValueError(f"目标 APSIM Soil 模块数量为 {len(matches)}，期望为 1")
    m = matches[0]
    return truth_text[: m.start()] + soil_block + truth_text[m.end() :]


def extract_soil_fragment(truth_text: str) -> dict:
    soil = extract_soil_block(truth_text)
    return {
        "soil_name": "soil",
        "record_number": _extract_tag(soil, "RecordNumber"),
        "soil_type": _extract_tag(soil, "SoilType"),
        "site": _extract_tag(soil, "Site"),
        "initial_water_fraction_full": _num_or_none(_extract_tag(soil, "FractionFull")),
        "soil_block_length": len(soil),
    }


def ensure_hdsw_soil_base(truth_text: str, hdsw_source_path: Path) -> tuple[str, dict]:
    if not hdsw_source_path.exists():
        raise FileNotFoundError(f"HDSW soil source not found: {hdsw_source_path}")
    source_text = hdsw_source_path.read_text(encoding="utf-8")
    source_soil = extract_soil_block(source_text)
    current_soil = extract_soil_block(truth_text)
    before = extract_soil_fragment(truth_text)
    changed = source_soil != current_soil
    after_text = replace_soil_block(truth_text, source_soil) if changed else truth_text
    after = extract_soil_fragment(after_text)
    return after_text, {
        "soil_base": "HDSW",
        "hdsw_source_path": str(hdsw_source_path),
        "changed": changed,
        "before": before,
        "after": after,
    }


def parse_initial_water_config(truth_text: str) -> dict:
    return {
        "mode": "FractionFull",
        "fraction_full": base.get_initial_water_fraction(truth_text),
        "node_path": "/folder/simulation/Soil/InitialWater/FractionFull",
        "layer_values": [],
    }


def set_initial_water_config(truth_text: str, config: dict) -> str:
    v = float(config.get("fraction_full"))
    return base.set_initial_water_fraction(truth_text, v)


def mutate_initial_water_config(base_config: dict, soil_diag: dict, args, seed: int, k: int) -> tuple[dict, dict]:
    cfg = copy.deepcopy(base_config)
    old = float(cfg.get("fraction_full") or 0.5)
    action = (soil_diag or {}).get("recommended_action", "no_change")
    surface = _num_or_none((soil_diag or {}).get("surface_bias"))
    deep = _num_or_none((soil_diag or {}).get("deep_bias"))
    delta = float(args.initial_water_step)
    if action in ("decrease_water", "reduce_events", "reduce_single_amount") or (surface is not None and surface > 0.5) or (deep is not None and deep > 0.5):
        new = old - delta
        direction = "down"
        reason = "soil_water 诊断为模拟偏湿，降低 InitialWater.FractionFull"
    elif action in ("increase_water", "increase_events") or (surface is not None and surface < -0.5):
        new = old + delta
        direction = "up"
        reason = "soil_water 诊断为模拟偏干，提高 InitialWater.FractionFull"
    else:
        new = old + (delta if (k % 2) else -delta)
        direction = "up" if new > old else "down"
        reason = "soil_water 方向不明确，按固定种子小步探索 InitialWater.FractionFull"
    new = base.clamp(new, float(args.initial_water_min), float(args.initial_water_max))
    cfg["fraction_full"] = new
    return cfg, {
        "old_config": base_config,
        "new_config": cfg,
        "old_value": old,
        "new_value": new,
        "direction": direction,
        "reason": reason,
    }


def parse_crit_fr_asw(truth_text: str) -> list:
    out = []
    for m in re.finditer(r"(<crit_fr_asw[^>]*>)([^<]+)(</crit_fr_asw>)", truth_text, re.S):
        out.append(
            {
                "index": len(out),
                "node_path": f"//crit_fr_asw[{len(out) + 1}]",
                "value": _num_or_none(m.group(2).strip()),
                "manager_name": "Irrigation",
                "crop_or_scope": "global",
                "span": [m.start(2), m.end(2)],
            }
        )
    return out


def set_crit_fr_asw(truth_text: str, target_node: dict, new_value: float) -> str:
    matches = list(re.finditer(r"(<crit_fr_asw[^>]*>)([^<]+)(</crit_fr_asw>)", truth_text, re.S))
    idx = int(target_node.get("index", 0))
    if idx < 0 or idx >= len(matches):
        raise ValueError("crit_fr_asw target_node index 超出范围")
    m = matches[idx]
    return truth_text[: m.start(2)] + base.fmt(float(new_value), 2) + truth_text[m.end(2) :]


def mutate_crit_fr_asw(base_value: float, soil_diag: dict, yield_diag: dict, args, seed: int, k: int) -> tuple[float, dict]:
    old = float(base_value)
    action = (soil_diag or {}).get("recommended_action", "no_change")
    crop = (yield_diag or {}).get("crop_to_recover")
    crop_err = (yield_diag or {}).get(f"{crop}_yield_error") if crop in ("wheat", "maize") else None
    step = float(args.crit_fr_asw_step)
    if action in ("decrease_water", "reduce_events", "reduce_single_amount"):
        new = old - step
        direction = "down"
        reason = "soil_water 模拟偏湿，降低 crit_fr_asw 以减少/推迟自动灌溉触发"
    elif crop_err is not None and crop_err > float(args.target_yield_rel) and action in ("increase_water", "increase_events"):
        new = old + step
        direction = "up"
        reason = "yield 未达标且 soil_water 偏干，提高 crit_fr_asw 以更早触发供水"
    else:
        new = old - step if (k % 2 == 0) else old + step
        direction = "down" if new < old else "up"
        reason = "按 HDSW water-yield 目标小步探索 crit_fr_asw"
    new = base.clamp(new, float(args.crit_fr_asw_min), float(args.crit_fr_asw_max))
    return new, {"old_value": old, "new_value": new, "direction": direction, "reason": reason}


def _extract_tag(block: str, tag: str, default=None):
    m = re.search(rf"<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>", block, re.S)
    if not m:
        return default
    return m.group(1).strip()


def _set_tag(block: str, tag: str, value) -> str:
    p = re.compile(rf"(<{re.escape(tag)}[^>]*>)(.*?)(</{re.escape(tag)}>)", re.S)
    new_block, n = p.subn(lambda m: f"{m.group(1)}{value}{m.group(3)}", block, count=1)
    if n != 1:
        raise ValueError(f"无法设置 irrigation.{tag}")
    return new_block


def _parse_irrigation_events(dates_text: str, amounts_text: str) -> list:
    dates = [x.strip() for x in str(dates_text or "").split(";") if x.strip()]
    amounts = [x.strip() for x in str(amounts_text or "").split(";") if x.strip()]
    out = []
    for i, d in enumerate(dates):
        try:
            amt = float(amounts[i])
        except Exception:
            amt = 0.0
        out.append({"date": d, "amount_mm": amt})
    return out


def _events_to_text(events: list) -> tuple[str, str]:
    clean = []
    seen = set()
    for e in sorted(events or [], key=lambda x: x.get("date", "")):
        d = str(e.get("date", "")).strip()
        if not d or d in seen:
            continue
        seen.add(d)
        clean.append({"date": d, "amount_mm": float(e.get("amount_mm", 0.0))})
    return ";".join(e["date"] for e in clean), ";".join(base.fmt(e["amount_mm"], 1) for e in clean)


def parse_irrigation_config(truth_text: str) -> dict:
    m = re.search(r"<irrigation\s+name=\"([^\"]+)\"[^>]*>(.*?)</irrigation>", truth_text, re.S)
    if not m:
        raise ValueError("truth.apsim 中未找到 <irrigation name=\"...\"> 组件")
    block = m.group(0)
    manager = re.search(
        rf"<manager2\s+name=\"{re.escape(WATER_YIELD_MANAGER_NAME)}\"[^>]*>(.*?)</manager2>",
        truth_text,
        re.S,
    )
    manager_block = manager.group(0) if manager else ""
    dates_text = _extract_tag(manager_block, "event_dates", "") if manager else ""
    amounts_text = _extract_tag(manager_block, "event_amounts", "") if manager else ""
    events = _parse_irrigation_events(dates_text, amounts_text)
    automatic = _extract_tag(block, "automatic_irrigation", "on")
    fixed_enabled = _extract_tag(manager_block, "enabled_flag", "no") if manager else "no"
    if str(fixed_enabled).lower() == "yes":
        mode = "fixed_dates"
    elif str(automatic).lower() == "on":
        mode = "threshold"
    else:
        mode = "off"
    return {
        "mode": mode,
        "manager_name": m.group(1),
        "automatic_irrigation": automatic,
        "asw_depth": _num_or_none(_extract_tag(block, "asw_depth", 800)),
        "crit_fr_asw": _num_or_none(_extract_tag(block, "crit_fr_asw", 0.60)),
        "irrigation_efficiency": _num_or_none(_extract_tag(block, "irrigation_efficiency", 0.85)),
        "allocation": _num_or_none(_extract_tag(block, "allocation", 0)),
        "fixed_manager_name": WATER_YIELD_MANAGER_NAME,
        "fixed_manager_exists": bool(manager),
        "fixed_enabled": fixed_enabled,
        "events": events,
        "threshold": {
            "layer": "asw_depth",
            "trigger_below": _num_or_none(_extract_tag(block, "crit_fr_asw", 0.60)),
            "amount_mm": None,
            "asw_depth": _num_or_none(_extract_tag(block, "asw_depth", 800)),
        },
        "raw_irrigation_block": block,
        "raw_fixed_manager_block": manager_block,
    }


def build_water_yield_irrigation_manager(config: dict) -> str:
    events = config.get("events") or []
    dates_text, amounts_text = _events_to_text(events)
    enabled = "yes" if str(config.get("fixed_enabled", "no")).lower() == "yes" and events else "no"
    eff = config.get("irrigation_efficiency") or 0.85
    return f'''        <manager2 name="{WATER_YIELD_MANAGER_NAME}">
          <ui>
            <enabled_flag type="yesno" description="Enable Sobol water-yield fixed irrigation">{enabled}</enabled_flag>
            <event_dates type="text" description="Fixed irrigation dates, yyyy-MM-dd separated by semicolons">{dates_text}</event_dates>
            <event_amounts type="text" description="Irrigation amounts in mm, separated by semicolons">{amounts_text}</event_amounts>
            <eff type="text" description="Irrigation efficiency (0-1)">{base.fmt(float(eff), 2)}</eff>
          </ui>
          <text>using System;
using System.Globalization;
using ModelFramework;

public class Script
{{
   [Link] Irrigation Irrigation;
   [Input] private DateTime today;
   [Param] private string enabled_flag;
   [Param] private string event_dates;
   [Param] private string event_amounts;
   [Param] private double eff;

   [EventHandler] public void OnPrepare()
   {{
      if (enabled_flag == null || enabled_flag.ToLower() != "yes")
         return;
      string[] dates = (event_dates ?? "").Split(';');
      string[] amounts = (event_amounts ?? "").Split(';');
      for (int i = 0; i &lt; dates.Length; i++)
      {{
         DateTime d;
         if (!DateTime.TryParseExact(dates[i].Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out d))
            continue;
         if (today.Date != d.Date)
            continue;
         double amount = 0.0;
         if (i &lt; amounts.Length)
            Double.TryParse(amounts[i].Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out amount);
         if (amount &lt;= 0.0)
            continue;
         IrrigationApplicationType data = new IrrigationApplicationType();
         data.Amount = (int)Math.Round(amount);
         Irrigation.Set("irrigation_efficiency", eff);
         Irrigation.Apply(data);
         Console.WriteLine("SobolWaterYieldIrrigation applied " + amount + " mm on " + dates[i].Trim());
      }}
   }}
}}
       </text>
        </manager2>'''


def set_irrigation_config(truth_text: str, config: dict) -> str:
    m = re.search(r"(<irrigation\s+name=\"[^\"]+\"[^>]*>.*?</irrigation>)", truth_text, re.S)
    if not m:
        raise ValueError("无法写入灌溉配置：找不到 <irrigation> 组件")
    old_block = m.group(1)
    new_block = old_block
    auto = config.get("automatic_irrigation", "on")
    new_block = _set_tag(new_block, "automatic_irrigation", auto)
    new_block = _set_tag(new_block, "asw_depth", base.fmt(float(config.get("asw_depth") or 800), 1))
    new_block = _set_tag(new_block, "crit_fr_asw", base.fmt(float(config.get("crit_fr_asw") or 0.60), 2))
    new_block = _set_tag(new_block, "irrigation_efficiency", base.fmt(float(config.get("irrigation_efficiency") or 0.85), 2))
    new_text = truth_text[: m.start(1)] + new_block + truth_text[m.end(1) :]

    manager_block = build_water_yield_irrigation_manager(config)
    p = re.compile(rf"<manager2\s+name=\"{re.escape(WATER_YIELD_MANAGER_NAME)}\"[^>]*>.*?</manager2>", re.S)
    new_text, n = p.subn(manager_block, new_text, count=1)
    if n == 0:
        insert_pos = new_text.find(new_block) + len(new_block)
        if insert_pos < len(new_block):
            raise ValueError("无法插入 SobolWaterYieldIrrigation manager")
        new_text = new_text[:insert_pos] + "\n" + manager_block + new_text[insert_pos:]
    return new_text


def total_irrigation_mm(config: dict) -> float:
    return sum(float(e.get("amount_mm", 0.0)) for e in config.get("events") or [])


def _round_irrigation_amount(x: float, args) -> float:
    step = max(1.0, float(args.irrigation_step_mm))
    rounded = round(float(x) / step) * step
    return base.clamp(rounded, float(args.min_irrigation_mm), float(args.max_irrigation_mm))


def _limit_irrigation_events(events: list, args) -> list:
    clean = []
    seen = set()
    for e in sorted(events or [], key=lambda x: x.get("date", "")):
        d = str(e.get("date", "")).strip()
        if not d or d in seen:
            continue
        seen.add(d)
        amt = _round_irrigation_amount(float(e.get("amount_mm", args.min_irrigation_mm)), args)
        clean.append({"date": d, "amount_mm": amt})
    clean = clean[: int(args.max_irrigation_events)]
    total = 0.0
    limited = []
    for e in clean:
        if total + e["amount_mm"] > float(args.max_total_irrigation_mm):
            remaining = float(args.max_total_irrigation_mm) - total
            if remaining >= float(args.min_irrigation_mm):
                e = dict(e)
                e["amount_mm"] = _round_irrigation_amount(remaining, args)
                limited.append(e)
            break
        limited.append(e)
        total += e["amount_mm"]
    return limited


def mutate_irrigation_config(base_config: dict, diagnosis: dict, args, seed: int, k: int, mode: str | None = None) -> tuple[dict, dict]:
    rnd = random.Random(seed)
    mode = mode or args.irrigation_mode
    if mode == "mixed":
        mode = ["threshold", "fixed_dates", "seeded_random"][k % 3]
    action = (diagnosis or {}).get("recommended_action", "no_change")
    cfg = copy.deepcopy(base_config)
    old_total = total_irrigation_mm(base_config)
    changed_events = []

    if mode == "threshold":
        cfg["mode"] = "threshold"
        cfg["automatic_irrigation"] = "on"
        cfg["fixed_enabled"] = "no"
        old_thr = float(cfg.get("crit_fr_asw") or 0.60)
        delta = 0.04 if action in ("increase_water", "increase_events") else -0.04
        if action in ("reduce_single_amount", "reduce_events", "decrease_water"):
            delta = -0.04
        new_thr = base.clamp(old_thr + delta, 0.20, 0.85)
        cfg["crit_fr_asw"] = new_thr
        cfg["threshold"] = {"layer": "asw_depth", "trigger_below": new_thr, "amount_mm": None, "asw_depth": cfg.get("asw_depth")}
        reason = f"threshold 模式：根据 soil_water 诊断 {action}，crit_fr_asw {old_thr:.2f} -> {new_thr:.2f}"
    elif mode == "seeded_random":
        cfg["mode"] = "seeded_random"
        cfg["automatic_irrigation"] = "off"
        cfg["fixed_enabled"] = "yes"
        n_events = rnd.randint(1, int(args.max_irrigation_events))
        dates = rnd.sample(WATER_YIELD_FIXED_DATE_CANDIDATES, k=min(n_events, len(WATER_YIELD_FIXED_DATE_CANDIDATES)))
        events = []
        for d in dates:
            amt = rnd.randrange(int(args.min_irrigation_mm), int(args.max_irrigation_mm) + 1, int(args.irrigation_step_mm))
            events.append({"date": d, "amount_mm": float(amt)})
        cfg["events"] = _limit_irrigation_events(events, args)
        changed_events = cfg["events"]
        reason = f"seeded_random 模式：seed={seed}，生成 {len(cfg['events'])} 个固定日期灌溉事件"
    else:
        cfg["mode"] = "fixed_dates"
        cfg["automatic_irrigation"] = "off"
        cfg["fixed_enabled"] = "yes"
        events = [dict(e) for e in (cfg.get("events") or [])]
        if action in ("decrease_water", "reduce_events", "reduce_single_amount") and events:
            idx = k % len(events)
            old = dict(events[idx])
            if action == "reduce_events" or old["amount_mm"] <= float(args.min_irrigation_mm):
                removed = events.pop(idx)
                changed_events.append({"operation": "delete", **removed})
            else:
                events[idx]["amount_mm"] = _round_irrigation_amount(old["amount_mm"] - float(args.irrigation_step_mm), args)
                changed_events.append({"operation": "reduce_amount", "from": old, "to": events[idx]})
        else:
            available_dates = [d for d in WATER_YIELD_FIXED_DATE_CANDIDATES if d not in {e.get("date") for e in events}]
            if available_dates and len(events) < int(args.max_irrigation_events):
                d = available_dates[k % len(available_dates)]
                amt = _round_irrigation_amount(float(args.min_irrigation_mm) + (k % 3) * float(args.irrigation_step_mm), args)
                new_event = {"date": d, "amount_mm": amt}
                events.append(new_event)
                changed_events.append({"operation": "add", **new_event})
            elif events:
                idx = k % len(events)
                old = dict(events[idx])
                events[idx]["amount_mm"] = _round_irrigation_amount(old["amount_mm"] + float(args.irrigation_step_mm), args)
                changed_events.append({"operation": "increase_amount", "from": old, "to": events[idx]})
        cfg["events"] = _limit_irrigation_events(events, args)
        reason = f"fixed_dates 模式：根据 soil_water 诊断 {action} 调整固定日期灌溉"

    meta = {
        "mode": mode,
        "diagnosis_action": action,
        "reason": reason,
        "old_config": base_config,
        "new_config": cfg,
        "total_irrigation_mm_before": old_total,
        "total_irrigation_mm_after": total_irrigation_mm(cfg),
        "changed_events": changed_events,
        "threshold_todo": mode != "threshold" and "threshold manager 未单独新增；threshold 搜索通过 APSIM irrigation 组件的 automatic_irrigation/crit_fr_asw 实现。",
    }
    return cfg, meta


def pick_action_water_yield(k: int, baseline_eval: dict, baseline_water: dict, soil_diag: dict, args) -> dict:
    y = get_crop_yield_errors(baseline_eval)
    yield_bad = [crop for crop, val in y.items() if val is None or val > float(args.target_yield_rel)]
    if yield_bad and args.allow_cultivar_change:
        crop = max(yield_bad, key=lambda c: 999.0 if y[c] is None else y[c])
        phase = f"{crop}_yield_component"
        return {"action": f"{crop}_cultivar", "action_type": f"{crop}_cultivar_sobol", "crop": crop, "sobol_phase": phase}
    if args.allow_irrigation_change:
        mode = args.irrigation_mode
        if mode == "mixed":
            mode = ["threshold", "fixed_dates", "seeded_random"][k % 3]
        return {"action": f"irrigation_{mode}", "action_type": f"irrigation_{mode}", "irrigation_mode": mode}
    crop = "maize" if (y.get("maize") or 0.0) >= (y.get("wheat") or 0.0) else "wheat"
    return {"action": f"{crop}_cultivar", "action_type": f"{crop}_cultivar_sobol", "crop": crop, "sobol_phase": f"{crop}_yield_component"}


def pick_action_hdsw_water_yield(k: int, baseline_eval: dict, baseline_water: dict, soil_diag: dict, yield_diag: dict, args) -> dict:
    """HDSW 基础下的 action 选择。

    yield 不合格时先做恢复性搜索，但不只盯着品种参数；如果水分整体偏湿，
    会穿插 InitialWater、crit_fr_asw 和灌溉减量探索。
    """
    yield_bad = not bool((yield_diag or {}).get("yield_constraint_passed"))
    crop = (yield_diag or {}).get("crop_to_recover") or "maize"
    recommended = (soil_diag or {}).get("recommended_action", "no_change")

    if yield_bad:
        recovery_cycle = []
        if args.allow_cultivar_change:
            recovery_cycle.append({"action": f"{crop}_cultivar", "action_type": f"{crop}_cultivar_sobol", "crop": crop, "sobol_phase": f"{crop}_yield_component"})
        if recommended in ("decrease_water", "reduce_events", "reduce_single_amount"):
            if args.allow_initial_water_change:
                recovery_cycle.append({"action": "initial_water", "action_type": "initial_water"})
            if args.allow_crit_fr_asw_change:
                recovery_cycle.append({"action": "crit_fr_asw", "action_type": "crit_fr_asw"})
            if args.allow_irrigation_change:
                recovery_cycle.append({"action": "irrigation_threshold", "action_type": "irrigation_threshold", "irrigation_mode": "threshold"})
        elif args.allow_irrigation_change:
            recovery_cycle.append({"action": "irrigation_threshold", "action_type": "irrigation_threshold", "irrigation_mode": "threshold"})
        if not recovery_cycle:
            recovery_cycle.append({"action": f"{crop}_cultivar", "action_type": f"{crop}_cultivar_sobol", "crop": crop, "sobol_phase": f"{crop}_yield_component"})
        return recovery_cycle[k % len(recovery_cycle)]

    water_cycle = []
    if args.allow_initial_water_change:
        water_cycle.append({"action": "initial_water", "action_type": "initial_water"})
    if args.allow_crit_fr_asw_change:
        water_cycle.append({"action": "crit_fr_asw", "action_type": "crit_fr_asw"})
    if args.allow_irrigation_change:
        mode = args.irrigation_mode
        if mode == "mixed":
            mode = ["threshold", "fixed_dates", "seeded_random"][k % 3]
        water_cycle.append({"action": f"irrigation_{mode}", "action_type": f"irrigation_{mode}", "irrigation_mode": mode})
    if args.allow_cultivar_change:
        crop2 = "maize" if k % 2 == 0 else "wheat"
        water_cycle.append({"action": f"{crop2}_cultivar", "action_type": f"{crop2}_cultivar_sobol", "crop": crop2, "sobol_phase": f"{crop2}_yield_component"})
    return water_cycle[k % len(water_cycle)] if water_cycle else {"action": "initial_water", "action_type": "initial_water"}


def write_water_yield_stage_alignment(path: Path, plan: dict, irrigation_meta: dict, sobol_meta: dict, metrics: dict, accepted: bool, decision: dict) -> None:
    bw = metrics.get("baseline_hdsw_water_yield", metrics.get("baseline_water_yield", {}))
    cw = metrics.get("candidate_hdsw_water_yield", metrics.get("candidate_water_yield", {}))
    fields = [
        "optimization_mode",
        "action_type",
        "crop",
        "parameter_changed",
        "old_value",
        "new_value",
        "irrigation_mode",
        "total_irrigation_mm_before",
        "total_irrigation_mm_after",
        "baseline_soil_water_error",
        "candidate_soil_water_error",
        "baseline_water_yield_score",
        "candidate_water_yield_score",
        "wheat_yield_error",
        "maize_yield_error",
        "phenology_error_days_max",
        "accepted",
        "decision_reason",
    ]
    row = {
        "optimization_mode": plan.get("optimization_mode") or ("hdsw_water_yield_search" if metrics.get("candidate_hdsw_water_yield") else "water_yield_search"),
        "action_type": plan.get("action_type"),
        "crop": sobol_meta.get("crop"),
        "parameter_changed": sobol_meta.get("parameter_name"),
        "old_value": sobol_meta.get("old_value"),
        "new_value": sobol_meta.get("new_value"),
        "irrigation_mode": irrigation_meta.get("mode"),
        "total_irrigation_mm_before": irrigation_meta.get("total_irrigation_mm_before"),
        "total_irrigation_mm_after": irrigation_meta.get("total_irrigation_mm_after"),
        "baseline_soil_water_error": bw.get("soil_water_error"),
        "candidate_soil_water_error": cw.get("soil_water_error"),
        "baseline_water_yield_score": bw.get("water_yield_score"),
        "candidate_water_yield_score": cw.get("water_yield_score"),
        "wheat_yield_error": cw.get("wheat_yield_error"),
        "maize_yield_error": cw.get("maize_yield_error"),
        "phenology_error_days_max": cw.get("phenology_error_days_max"),
        "accepted": accepted,
        "decision_reason": decision.get("reason"),
    }
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(row)


def pick_action(k: int, baseline_eval: dict, mode: str):
    if mode == "wheat_focus":
        cycle = ["wheat_density_only", "wheat_cultivar", "wheat_fertilizer_only", "wheat_cultivar"]
        return cycle[k % len(cycle)]
    if mode == "conservative":
        cycle = ["wheat_density_only", "wheat_cultivar", "soil_only", "wheat_density_only", "wheat_cultivar"]
        return cycle[k % len(cycle)]

    # Bias to wheat when wheat biomass is the dominant gap.
    w_tb = baseline_eval["crops"]["wheat"].get("total_biomass_error_rel_mean")
    m_tb = baseline_eval["crops"]["maize"].get("total_biomass_error_rel_mean")
    if w_tb is not None and m_tb is not None and w_tb > m_tb + 0.12:
        cycle = ["wheat_density_only", "wheat_cultivar", "soil_only", "wheat_density_only", "maize_cultivar", "fertilizer_only"]
    elif w_tb is not None and m_tb is not None and w_tb > m_tb + 0.08:
        cycle = ["wheat_density_only", "wheat_cultivar", "soil_only", "fertilizer_only", "maize_cultivar"]
    else:
        cycle = ["wheat_density_only", "wheat_cultivar", "soil_only", "maize_cultivar", "fertilizer_only"]
    return cycle[k % len(cycle)]


def success_reached(
    custom_obj: dict,
    pheno_guard: int,
    target_bio: float,
    target_yield: float,
    target_structure: float,
    target_soil_water: float,
    target_phenology_norm: float,
):
    dmax = custom_obj.get("phenology_error_days_max")
    group_scores = custom_obj.get("group_scores") or {}
    bio = group_scores.get("total_biomass", custom_obj.get("total_biomass_error_rel_mean_all_crops"))
    structure = group_scores.get("structure_biomass_leaf_stem", custom_obj.get("structure_error_rel_mean_all_crops"))
    y = group_scores.get("yield", custom_obj.get("yield_error_rel_mean"))
    soil = group_scores.get("soil_water")
    ph_norm = group_scores.get("phenology", custom_obj.get("phenology_error_norm_mean"))
    return (
        dmax is not None
        and dmax <= pheno_guard
        and bio is not None
        and bio <= target_bio
        and structure is not None
        and structure <= target_structure
        and y is not None
        and y <= target_yield
        and soil is not None
        and soil <= target_soil_water
        and ph_norm is not None
        and ph_norm <= target_phenology_norm
    )


def parse_args():
    p = argparse.ArgumentParser(description="Bio-first single-factor search with traceable outputs under process_bio/output")
    p.add_argument("--rounds", type=int, default=40)
    p.add_argument("--pheno_guard_days", "--phenology_guard_days", dest="pheno_guard_days", type=int, default=10)
    p.add_argument("--target_bio_rel", type=float, default=0.30, help="target for total_biomass group rel error")
    p.add_argument("--target_structure_rel", type=float, default=0.45, help="target for structure_biomass_leaf_stem group rel error")
    p.add_argument("--target_yield_rel", type=float, default=0.15, help="target for crop-specific yield rel error")
    p.add_argument("--target_soil_water_rel", type=float, default=0.15, help="target for soil_water group rel error")
    p.add_argument("--target_phenology_norm", type=float, default=0.20, help="target for phenology normalized error group")
    p.add_argument("--soil_step_scale", type=float, default=0.15)
    p.add_argument("--maize_step_scale", type=float, default=0.25)
    p.add_argument("--wheat_step_scale", type=float, default=0.15)
    p.add_argument("--wheat_density_step_scale", type=float, default=1.0)
    p.add_argument("--wheat_fert_step_scale", type=float, default=1.0)
    p.add_argument(
        "--wheat_cultivar_mode",
        type=str,
        default="mixed",
        choices=["mixed", "standard", "stem_bias"],
        help="mutation mode for wheat cultivar steps",
    )
    p.add_argument(
        "--action_mode",
        type=str,
        default="mixed",
        choices=["mixed", "wheat_focus", "conservative", "sobol_phased", "water_yield_search", "hdsw_water_yield_search"],
        help="mixed: joint search; wheat_focus: freeze maize and only adjust wheat variables; conservative: wheat_focus + occasional soil-only",
    )
    p.add_argument("--soil_base", type=str, default="HDSW", choices=["HDSW", "current"], help="hdsw_water_yield_search 的 soil 基础。")
    p.add_argument("--output_dir", type=Path, default=None, help="本轮搜索输出目录；hdsw_water_yield_search 默认使用 output_hdsw_water_yield。")
    p.add_argument("--hdsw_soil_source", type=Path, default=DEFAULT_HDSW_SOIL_SOURCE, help="包含 HDSW Soil 模块的 APSIM 文件。")
    p.add_argument(
        "--sobol_phase",
        type=str,
        default="auto",
        choices=["auto", "maize_phenology", "maize_yield_component", "wheat_phenology", "wheat_yield_component", "biomass_canopy"],
        help="sobol_phased 模式的校准阶段；auto 会根据当前物候误差自动选择。",
    )
    p.add_argument(
        "--crop_priority",
        type=str,
        default="maize_first",
        choices=["maize_first", "wheat_first", "balanced"],
        help="sobol_phased 自动阶段和同阶段作物顺序。",
    )
    p.add_argument(
        "--cultivar_only",
        action="store_true",
        default=False,
        help="只允许修改 Wheat.xml / Maize.xml 中的品种参数；sobol_phased 模式会强制启用。",
    )
    p.add_argument("--max_params_per_iter", type=int, default=1, help="sobol_phased 每轮最多修改的品种参数数；当前实现默认单参数。")
    p.add_argument("--soil_water_priority_weight", type=float, default=0.65)
    p.add_argument("--yield_constraint_weight", type=float, default=0.25)
    p.add_argument("--phenology_constraint_weight", type=float, default=0.10)
    p.add_argument("--stability_weight", type=float, default=0.05)
    p.add_argument("--max_irrigation_events", type=int, default=6)
    p.add_argument("--min_irrigation_mm", type=float, default=10.0)
    p.add_argument("--max_irrigation_mm", type=float, default=60.0)
    p.add_argument("--max_total_irrigation_mm", type=float, default=240.0)
    p.add_argument("--irrigation_step_mm", type=float, default=10.0)
    p.add_argument(
        "--irrigation_mode",
        type=str,
        default="mixed",
        choices=["fixed_dates", "threshold", "seeded_random", "mixed"],
    )
    p.add_argument("--irrigation_seed", type=int, default=20260519)
    p.add_argument("--allow_cultivar_change", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--allow_irrigation_change", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--allow_initial_water_change", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--allow_crit_fr_asw_change", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--water_yield_patience", type=int, default=30)
    p.add_argument("--min_soil_water_improvement", type=float, default=0.005)
    p.add_argument("--initial_water_step", type=float, default=0.05)
    p.add_argument("--initial_water_min", type=float, default=0.10)
    p.add_argument("--initial_water_max", type=float, default=1.00)
    p.add_argument("--crit_fr_asw_step", type=float, default=0.05)
    p.add_argument("--crit_fr_asw_min", type=float, default=0.05)
    p.add_argument("--crit_fr_asw_max", type=float, default=0.95)
    p.add_argument("--max_rounds_without_yield_pass", type=int, default=80)
    p.add_argument("--wheat_density_min", type=float, default=70.0)
    p.add_argument("--wheat_density_max", type=float, default=380.0)
    p.add_argument("--wheat_anchor_weight", type=float, default=0.20)
    p.add_argument("--wheat_late_weight", type=float, default=0.35)
    p.add_argument("--wheat_anchor_max_weight", type=float, default=0.10)
    p.add_argument("--wheat_late_stage_cn", type=str, default=WHEAT_LATE_STAGE_CN_DEFAULT)
    p.add_argument("--score_mode", type=str, choices=["all_truth", "legacy"], default="all_truth")
    p.add_argument("--missing_truth_penalty", type=float, default=1.0)
    p.add_argument("--truth_rel_error_cap", type=float, default=2.0)
    p.add_argument("--enable_legacy_wheat_anchor_bonus", action="store_true")
    p.add_argument("--weight_total_biomass", type=float, default=DEFAULT_GROUP_WEIGHTS["total_biomass"])
    p.add_argument("--weight_structure", type=float, default=DEFAULT_GROUP_WEIGHTS["structure_biomass_leaf_stem"])
    p.add_argument("--weight_yield", type=float, default=DEFAULT_GROUP_WEIGHTS["yield"])
    p.add_argument("--weight_lai", type=float, default=DEFAULT_GROUP_WEIGHTS["LAI"])
    p.add_argument("--weight_soil_water", type=float, default=DEFAULT_GROUP_WEIGHTS["soil_water"])
    p.add_argument("--weight_phenology", type=float, default=DEFAULT_GROUP_WEIGHTS["phenology"])
    p.add_argument(
        "--validation_csv",
        type=Path,
        default=DEFAULT_VALIDATION_CSV,
        help="optional independent validation csv; if present it is used before processing/truth_extract.json",
    )
    p.add_argument(
        "--truth_template",
        type=Path,
        default=DEFAULT_TRUTH_TEMPLATE,
        help="initial apsim truth template to copy into codex/truth.apsim before search; when present it takes precedence over best snapshots",
    )
    return p.parse_args()


def main():
    args = parse_args()
    if args.output_dir is None:
        args.output_dir = DEFAULT_HDSW_OUTPUT_DIR if args.action_mode == "hdsw_water_yield_search" else DEFAULT_OUTPUT_DIR
    configure_output_dir(args.output_dir)
    if args.action_mode == "sobol_phased":
        args.cultivar_only = True
        args.max_params_per_iter = max(1, min(2, int(args.max_params_per_iter)))
    if args.action_mode == "hdsw_water_yield_search":
        args.soil_base = "HDSW"
    PROCESS_BIO.mkdir(parents=True, exist_ok=True)
    BEST_DIR.mkdir(parents=True, exist_ok=True)

    truth_obj = load_truth_observations(args.validation_csv)
    group_weights = normalize_group_weights(
        {
            "total_biomass": args.weight_total_biomass,
            "structure_biomass_leaf_stem": args.weight_structure,
            "yield": args.weight_yield,
            # Keep LAI as diagnostics only (not part of total score).
            "LAI": 0.0,
            "soil_water": args.weight_soil_water,
            "phenology": args.weight_phenology,
        }
    )

    # Continue from the selected output_dir/best when available.  For a fresh
    # HDSW run, seed from the previous Sobol best if present, then enforce the
    # HDSW Soil block below.  This keeps the new experiment independent while
    # preserving the requested cultivar starting point.
    seed_dir = None
    if (BEST_DIR / "truth.apsim").exists():
        seed_dir = BEST_DIR
    elif args.action_mode == "hdsw_water_yield_search" and (DEFAULT_HDSW_SEED_DIR / "truth.apsim").exists():
        seed_dir = DEFAULT_HDSW_SEED_DIR
    elif (base.PROCESSING / "best" / "truth.apsim").exists():
        seed_dir = base.PROCESSING / "best"
    if seed_dir is not None:
        shutil.copy2(seed_dir / "truth.apsim", base.TRUTH_PATH)
        shutil.copy2(seed_dir / "Wheat.xml", base.WHEAT_PATH)
        shutil.copy2(seed_dir / "Maize.xml", base.MAIZE_PATH)
    elif args.truth_template and Path(args.truth_template).exists():
        shutil.copy2(Path(args.truth_template), base.TRUTH_PATH)

    hdsw_soil_manifest = {}
    if args.action_mode == "hdsw_water_yield_search":
        before_truth_for_hdsw = base.read_text(base.TRUTH_PATH)
        after_truth_for_hdsw, hdsw_soil_manifest = ensure_hdsw_soil_base(before_truth_for_hdsw, Path(args.hdsw_soil_source))
        if hdsw_soil_manifest.get("changed"):
            hdsw_bootstrap_dir = PROCESS_BIO / "hdsw_soil_bootstrap"
            hdsw_bootstrap_dir.mkdir(parents=True, exist_ok=True)
            base.write_text(hdsw_bootstrap_dir / "before_truth.apsim", before_truth_for_hdsw)
            base.write_text(hdsw_bootstrap_dir / "after_truth.apsim", after_truth_for_hdsw)
            base.write_text(hdsw_bootstrap_dir / "before_soil_fragment.json", json.dumps(hdsw_soil_manifest.get("before"), ensure_ascii=False, indent=2))
            base.write_text(hdsw_bootstrap_dir / "after_soil_fragment.json", json.dumps(hdsw_soil_manifest.get("after"), ensure_ascii=False, indent=2))
            base.write_text(base.TRUTH_PATH, after_truth_for_hdsw)
        base.write_text(PROCESS_BIO / "hdsw_run_manifest.json", json.dumps(hdsw_soil_manifest, ensure_ascii=False, indent=2))

    current_truth = base.read_text(base.TRUTH_PATH)
    current_wheat = base.read_text(base.WHEAT_PATH)
    current_maize = base.read_text(base.MAIZE_PATH)
    current_best_wheat = base.get_manager_cultivar(current_truth, "Wheat Management")
    current_best_maize = base.get_manager_cultivar(current_truth, "Maize Management")
    if args.action_mode == "hdsw_water_yield_search":
        base.write_text(
            PROCESS_BIO / "run_start_manifest.json",
            json.dumps(
                {
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "action_mode": args.action_mode,
                    "output_dir": str(PROCESS_BIO),
                    "soil_base": args.soil_base,
                    "hdsw_soil_source": str(args.hdsw_soil_source),
                    "current_wheat_cultivar_in_truth": current_best_wheat,
                    "current_maize_cultivar_in_truth": current_best_maize,
                    "requested_wheat_cultivar": DEFAULT_WHEAT_CULTIVAR,
                    "requested_maize_cultivar": DEFAULT_MAIZE_CULTIVAR,
                    "hdsw_soil": hdsw_soil_manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    last_iter = read_last_iter()
    found_success = False
    no_water_improve_count = 0
    best_water_soil = None
    if args.action_mode in ("water_yield_search", "hdsw_water_yield_search") and (BEST_DIR / "metrics.json").exists():
        try:
            prev_best_metrics = json.loads((BEST_DIR / "metrics.json").read_text(encoding="utf-8"))
            best_water_soil = (
                (prev_best_metrics.get("candidate_hdsw_water_yield") or {}).get("soil_water_error")
                or (prev_best_metrics.get("candidate_water_yield") or {}).get("soil_water_error")
                or (prev_best_metrics.get("candidate_custom") or {}).get("group_scores", {}).get("soil_water")
            )
        except Exception:
            best_water_soil = None

    for k in range(max(1, int(args.rounds))):
        iter_no = last_iter + 1 + k
        iter_dir = PROCESS_BIO / f"iter_{iter_no:03d}"
        before_dir = iter_dir / "before"
        after_dir = iter_dir / "after"
        out_base = iter_dir / "outputs" / "baseline"
        out_cand = iter_dir / "outputs" / "candidate"
        logs_dir = iter_dir / "logs"
        for d in (before_dir, after_dir, out_base, out_cand, logs_dir):
            d.mkdir(parents=True, exist_ok=True)

        truth_before = base.read_text(base.TRUTH_PATH)
        wheat_before = base.read_text(base.WHEAT_PATH)
        maize_before = base.read_text(base.MAIZE_PATH)

        base.write_text(out_base / "truth.apsim", truth_before)
        base.run_apsim_on_truth(out_base / "truth.apsim")
        baseline_eval = base.evaluate_output_dir(out_base, truth_obj)
        baseline_anchor = extract_wheat_anchor_metrics(out_base, truth_obj, args.wheat_late_stage_cn)
        baseline_rows_for_diag = collect_prediction_vs_truth_rows(out_base, truth_obj, "baseline")

        sobol_plan = None
        sobol_meta = {}
        water_plan = None
        water_decision = {}
        irrigation_meta = {}
        irrigation_changed = False
        initial_water_meta = {}
        initial_water_changed = False
        crit_fr_asw_meta = {}
        crit_fr_asw_changed = False
        soil_water_diagnosis = diagnose_soil_water_error(baseline_rows_for_diag)
        yield_diagnosis = diagnose_yield_error(baseline_eval, args)
        if args.action_mode == "sobol_phased":
            current_state = {
                "wheat_params": base.parse_wheat_params(wheat_before, current_best_wheat),
                "maize_params": base.parse_maize_params(maize_before, current_best_maize),
            }
            sobol_plan = pick_sobol_phased_action(k, baseline_eval, args, current_state, baseline_rows_for_diag)
            sobol_plan["diagnosis"]["sobol_phase"] = sobol_plan["sobol_phase"]
            action = sobol_plan["action"]
        elif args.action_mode == "water_yield_search":
            pheno_diag = _build_pheno_diag(baseline_eval, baseline_anchor)
            temp_baseline_custom = score_all_truth_objective(
                baseline_rows_for_diag,
                args.pheno_guard_days,
                pheno_diag,
                weights=group_weights,
                missing_truth_penalty=args.missing_truth_penalty,
                rel_error_cap=args.truth_rel_error_cap,
            )
            baseline_water_probe = score_water_yield_objective(baseline_eval, temp_baseline_custom, args)
            water_plan = pick_action_hdsw_water_yield(k, baseline_eval, baseline_water_probe, soil_water_diagnosis, yield_diagnosis, args)
            action = water_plan["action"]
            if action in ("maize_cultivar", "wheat_cultivar"):
                crop = water_plan["crop"]
                current_state = {
                    "wheat_params": base.parse_wheat_params(wheat_before, current_best_wheat),
                    "maize_params": base.parse_maize_params(maize_before, current_best_maize),
                }
                group = "yield_component"
                param_name = choose_sobol_param(crop, group, k, current_state[f"{crop}_params"])
                diagnosis = build_sobol_diagnosis(baseline_eval, baseline_rows_for_diag)
                diagnosis["sobol_phase"] = water_plan["sobol_phase"]
                sobol_plan = {
                    "action": f"{crop}_cultivar",
                    "sobol_phase": water_plan["sobol_phase"],
                    "crop": crop,
                    "parameter_name": param_name,
                    "parameter_key": sobol_param_key(crop, param_name),
                    "parameter_group": group,
                    "diagnosis": diagnosis,
                }
        elif args.action_mode == "hdsw_water_yield_search":
            pheno_diag = _build_pheno_diag(baseline_eval, baseline_anchor)
            temp_baseline_custom = score_all_truth_objective(
                baseline_rows_for_diag,
                args.pheno_guard_days,
                pheno_diag,
                weights=group_weights,
                missing_truth_penalty=args.missing_truth_penalty,
                rel_error_cap=args.truth_rel_error_cap,
            )
            baseline_hdsw_probe = score_hdsw_water_yield_objective(baseline_eval, temp_baseline_custom, args)
            water_plan = pick_action_hdsw_water_yield(k, baseline_eval, baseline_hdsw_probe, soil_water_diagnosis, yield_diagnosis, args)
            action = water_plan["action"]
            if action in ("maize_cultivar", "wheat_cultivar"):
                crop = water_plan["crop"]
                current_state = {
                    "wheat_params": base.parse_wheat_params(wheat_before, current_best_wheat),
                    "maize_params": base.parse_maize_params(maize_before, current_best_maize),
                }
                group = "yield_component"
                param_name = choose_sobol_param(crop, group, k, current_state[f"{crop}_params"])
                diagnosis = build_sobol_diagnosis(baseline_eval, baseline_rows_for_diag)
                diagnosis["sobol_phase"] = water_plan["sobol_phase"]
                sobol_plan = {
                    "action": f"{crop}_cultivar",
                    "sobol_phase": water_plan["sobol_phase"],
                    "crop": crop,
                    "parameter_name": param_name,
                    "parameter_key": sobol_param_key(crop, param_name),
                    "parameter_group": group,
                    "diagnosis": diagnosis,
                }
        else:
            action = pick_action(k, baseline_eval, args.action_mode)
            if args.cultivar_only and action not in ("wheat_cultivar", "maize_cultivar"):
                action = "maize_cultivar" if (args.crop_priority != "wheat_first" and k % 2 == 0) else "wheat_cultivar"
        truth_after = truth_before
        wheat_after = wheat_before
        maize_after = maize_before
        cultivar_changes = []
        non_cultivar_changes = []
        scope = "cultivar_only"
        non_cultivar_changed = False

        if action == "wheat_cultivar":
            p0 = base.parse_wheat_params(wheat_before, current_best_wheat)
            if sobol_plan and sobol_plan.get("crop") == "wheat":
                p1, sobol_meta = mutate_wheat_sobol_param(
                    p0,
                    sobol_plan["parameter_name"],
                    sobol_plan["diagnosis"],
                    args.wheat_step_scale,
                    20260424 + iter_no,
                )
            elif args.wheat_cultivar_mode == "standard":
                p1 = base.mutate_wheat(p0, 20260424 + iter_no, k)
            elif args.wheat_cultivar_mode == "stem_bias":
                p1 = mutate_wheat_stem_bias(p0, 20260424 + iter_no, k)
            else:
                if (k % 2) == 0:
                    p1 = mutate_wheat_stem_bias(p0, 20260424 + iter_no, k)
                else:
                    p1 = base.mutate_wheat(p0, 20260424 + iter_no, k)
            name = base.next_wheat_name(wheat_before, iter_no)
            wheat_after = base.append_wheat_cultivar(wheat_before, name, p1, current_best_wheat)
            truth_after = base.set_manager_cultivar(truth_after, "Wheat Management", name)
            changed = base.diff_dict(
                p0,
                p1,
                [
                    "vern_sens",
                    "photop_sens",
                    "tt_end_of_juvenile",
                    "tt_floral_initiation",
                    "tt_flowering",
                    "tt_start_grain_fill",
                    "startgf_to_mat",
                    "grains_per_gram_stem",
                    "max_grain_size",
                    "potential_grain_filling_rate",
                ],
            )
            if sobol_meta:
                changed = {
                    sobol_meta["parameter_name"]: {
                        "from": sobol_meta["old_value"],
                        "to": sobol_meta["new_value"],
                    }
                }
            cultivar_changes.append(
                {
                    "crop": "wheat",
                    "new_cultivar": name,
                    "derived_from": current_best_wheat,
                    "changed_params": changed,
                }
            )
        elif action == "maize_cultivar":
            p0 = base.parse_maize_params(maize_before, current_best_maize)
            if sobol_plan and sobol_plan.get("crop") == "maize":
                p1, sobol_meta = mutate_maize_sobol_param(
                    p0,
                    sobol_plan["parameter_name"],
                    sobol_plan["diagnosis"],
                    args.maize_step_scale,
                    20260424 + iter_no,
                )
            else:
                p1 = base.mutate_maize_yield_pullback(p0, 20260424 + iter_no, k, args.maize_step_scale)
            name = base.next_maize_name(maize_before, iter_no)
            maize_after = base.append_maize_cultivar(maize_before, name, p1)
            truth_after = base.set_manager_cultivar(truth_after, "Maize Management", name)
            changed = base.diff_dict(
                p0,
                p1,
                [
                    "tt_endjuv_to_init",
                    "tt_flower_to_start_grain",
                    "tt_flower_to_maturity",
                    "potKernelWt",
                    "GNmaxCoef",
                    "PGRbase",
                    "rue",
                    "aX0",
                    "largestLeafArea",
                    "leaf_no_dead_const",
                    "leaf_no_dead_slope",
                ],
            )
            if sobol_meta:
                changed = {
                    sobol_meta["parameter_name"]: {
                        "from": sobol_meta["old_value"],
                        "to": sobol_meta["new_value"],
                    }
                }
            cultivar_changes.append(
                {
                    "crop": "maize",
                    "new_cultivar": name,
                    "derived_from": current_best_maize,
                    "changed_params": changed,
                }
            )
        elif action in ("irrigation_fixed_dates", "irrigation_threshold", "irrigation_seeded_random"):
            scope = "irrigation_only"
            non_cultivar_changed = True
            irrigation_changed = True
            old_cfg = parse_irrigation_config(truth_before)
            mode = (water_plan or {}).get("irrigation_mode")
            new_cfg, irrigation_meta = mutate_irrigation_config(
                old_cfg,
                soil_water_diagnosis,
                args,
                int(args.irrigation_seed) + iter_no,
                k,
                mode=mode,
            )
            truth_after = set_irrigation_config(truth_after, new_cfg)
            non_cultivar_changes.append(
                {
                    "factor": "Irrigation",
                    "mode": irrigation_meta.get("mode"),
                    "total_irrigation_mm_before": irrigation_meta.get("total_irrigation_mm_before"),
                    "total_irrigation_mm_after": irrigation_meta.get("total_irrigation_mm_after"),
                    "reason": irrigation_meta.get("reason"),
                }
            )
        elif action == "initial_water":
            scope = "initial_water_only"
            non_cultivar_changed = True
            initial_water_changed = True
            old_cfg = parse_initial_water_config(truth_before)
            new_cfg, initial_water_meta = mutate_initial_water_config(
                old_cfg,
                soil_water_diagnosis,
                args,
                20260519 + iter_no,
                k,
            )
            truth_after = set_initial_water_config(truth_after, new_cfg)
            non_cultivar_changes.append(
                {
                    "factor": "InitialWater.FractionFull",
                    "from": initial_water_meta.get("old_value"),
                    "to": initial_water_meta.get("new_value"),
                    "reason": initial_water_meta.get("reason"),
                }
            )
        elif action == "crit_fr_asw":
            scope = "crit_fr_asw_only"
            non_cultivar_changed = True
            crit_fr_asw_changed = True
            nodes = parse_crit_fr_asw(truth_before)
            if not nodes:
                raise ValueError("未找到可修改的 crit_fr_asw 节点")
            node = nodes[0]
            new_value, crit_fr_asw_meta = mutate_crit_fr_asw(
                node["value"],
                soil_water_diagnosis,
                yield_diagnosis,
                args,
                20260519 + iter_no,
                k,
            )
            crit_fr_asw_meta["target_node"] = {k2: v for k2, v in node.items() if k2 != "span"}
            truth_after = set_crit_fr_asw(truth_after, node, new_value)
            non_cultivar_changes.append(
                {
                    "factor": "crit_fr_asw",
                    "from": crit_fr_asw_meta.get("old_value"),
                    "to": crit_fr_asw_meta.get("new_value"),
                    "reason": crit_fr_asw_meta.get("reason"),
                }
            )
        elif action == "soil_only":
            scope = "soil_only"
            non_cultivar_changed = True
            old = base.get_initial_water_fraction(truth_before)
            new = base.mutate_soil_fraction(old, 20260424 + iter_no, k, args.soil_step_scale)
            truth_after = base.set_initial_water_fraction(truth_after, new)
            non_cultivar_changes.append({"factor": "InitialWater.FractionFull", "from": old, "to": new})
        elif action == "wheat_density_only":
            scope = "sowing_only"
            non_cultivar_changed = True
            old = get_wheat_density(truth_before)
            new = mutate_wheat_density(
                old,
                20260424 + iter_no,
                k,
                args.wheat_density_step_scale,
                args.wheat_density_min,
                args.wheat_density_max,
            )
            truth_after = set_wheat_density(truth_after, new)
            non_cultivar_changes.append({"factor": "Wheat Management.density1", "from": old, "to": new})
        elif action == "wheat_fertilizer_only":
            scope = "fertilizer_only"
            non_cultivar_changed = True
            old = get_wheat_sowing_fert_amt(truth_before)
            new = mutate_wheat_fert_amt(old, 20260424 + iter_no, k, args.wheat_fert_step_scale)
            truth_after = set_wheat_sowing_fert_amt(truth_after, new)
            non_cultivar_changes.append({"factor": "Wheat Sowing Fertiliser.fertAmt", "from": old, "to": new})
        else:
            scope = "fertilizer_only"
            non_cultivar_changed = True
            old = base.get_maize_sowing_fert_amt(truth_before)
            new = base.mutate_fert_amt(old, 20260424 + iter_no, k)
            truth_after = base.set_maize_sowing_fert_amt(truth_after, new)
            non_cultivar_changes.append({"factor": "Maize Sowing Fertiliser1.fertAmt", "from": old, "to": new})

        base.write_text(base.WHEAT_PATH, wheat_after)
        base.write_text(base.MAIZE_PATH, maize_after)
        base.write_text(base.TRUTH_PATH, truth_after)

        base.write_text(out_cand / "truth.apsim", truth_after)
        base.run_apsim_on_truth(out_cand / "truth.apsim")
        candidate_eval = base.evaluate_output_dir(out_cand, truth_obj)
        candidate_anchor = extract_wheat_anchor_metrics(out_cand, truth_obj, args.wheat_late_stage_cn)
        baseline_rows = baseline_rows_for_diag
        candidate_rows = collect_prediction_vs_truth_rows(out_cand, truth_obj, "candidate")

        metrics = build_metrics(
            iter_no,
            baseline_eval,
            candidate_eval,
            baseline_anchor,
            candidate_anchor,
            args.pheno_guard_days,
            args.wheat_anchor_weight,
            args.wheat_late_weight,
            args.wheat_anchor_max_weight,
            score_mode=args.score_mode,
            baseline_rows=baseline_rows,
            candidate_rows=candidate_rows,
            group_weights=group_weights,
            missing_truth_penalty=args.missing_truth_penalty,
            truth_rel_error_cap=args.truth_rel_error_cap,
            enable_legacy_wheat_anchor_bonus=args.enable_legacy_wheat_anchor_bonus,
        )
        scored_rows = metrics.pop("_scored_rows", {"baseline": baseline_rows, "candidate": candidate_rows})
        b_custom = metrics["baseline_custom"]
        c_custom = metrics["candidate_custom"]

        # Hard guard: candidate must satisfy phenology constraint to become new best.
        pheno_ok = c_custom["phenology_error_days_max"] is not None and c_custom["phenology_error_days_max"] <= args.pheno_guard_days
        if args.action_mode == "water_yield_search":
            metrics["baseline_water_yield"] = score_water_yield_objective(baseline_eval, b_custom, args)
            metrics["candidate_water_yield"] = score_water_yield_objective(candidate_eval, c_custom, args)
            water_decision = is_water_yield_candidate_acceptable(
                metrics["baseline_water_yield"],
                metrics["candidate_water_yield"],
                args,
            )
            better = bool(water_decision["accepted"])
            b_wys = metrics["baseline_water_yield"].get("water_yield_score")
            c_wys = metrics["candidate_water_yield"].get("water_yield_score")
            if b_wys not in (None, 0) and c_wys is not None:
                metrics["comparison"]["water_yield_score_improvement_pct"] = (b_wys - c_wys) / b_wys * 100.0
            else:
                metrics["comparison"]["water_yield_score_improvement_pct"] = None
            metrics["comparison"]["water_yield_acceptance"] = water_decision
            metrics["comparison"]["is_better_than_baseline"] = better
        elif args.action_mode == "hdsw_water_yield_search":
            metrics["baseline_hdsw_water_yield"] = score_hdsw_water_yield_objective(baseline_eval, b_custom, args)
            metrics["candidate_hdsw_water_yield"] = score_hdsw_water_yield_objective(candidate_eval, c_custom, args)
            water_decision = is_hdsw_water_yield_candidate_acceptable(
                metrics["baseline_hdsw_water_yield"],
                metrics["candidate_hdsw_water_yield"],
                args,
                irrigation_meta,
            )
            better = bool(water_decision["accepted"])
            b_wys = metrics["baseline_hdsw_water_yield"].get("hdsw_water_yield_score")
            c_wys = metrics["candidate_hdsw_water_yield"].get("hdsw_water_yield_score")
            if b_wys not in (None, 0) and c_wys is not None:
                metrics["comparison"]["hdsw_water_yield_score_improvement_pct"] = (b_wys - c_wys) / b_wys * 100.0
            else:
                metrics["comparison"]["hdsw_water_yield_score_improvement_pct"] = None
            metrics["comparison"]["hdsw_water_yield_acceptance"] = water_decision
            metrics["comparison"]["is_better_than_baseline"] = better
        else:
            better = metrics["comparison"]["is_better_than_baseline"] and pheno_ok
            metrics["comparison"]["is_better_than_baseline"] = better

        base_locked = ["weather", "rotation", "sowing_window", "irrigation", "residue", "tillage"]
        if scope == "cultivar_only":
            locked = base_locked + ["soil", "fertilizer"]
            if args.action_mode == "water_yield_search":
                locked = base_locked + ["soil", "fertilizer", "irrigation"]
        elif scope == "irrigation_only":
            locked = ["weather", "rotation", "sowing_window", "residue", "tillage", "soil", "fertilizer", "wheat_cultivar", "maize_cultivar"]
        elif scope == "initial_water_only":
            locked = ["weather", "rotation", "sowing_window", "irrigation", "residue", "tillage", "soil_physical_properties", "fertilizer", "wheat_cultivar", "maize_cultivar"]
        elif scope == "crit_fr_asw_only":
            locked = ["weather", "rotation", "sowing_window", "residue", "tillage", "soil", "fertilizer", "wheat_cultivar", "maize_cultivar"]
        elif scope == "soil_only":
            locked = base_locked + ["fertilizer", "wheat_cultivar", "maize_cultivar"]
        elif scope == "sowing_only":
            locked = base_locked + ["soil", "fertilizer", "wheat_cultivar", "maize_cultivar"]
        else:
            locked = base_locked + ["soil", "wheat_cultivar", "maize_cultivar"]

        manifest = {
            "iteration": iter_no,
            "change_scope": scope,
            "non_cultivar_changed": non_cultivar_changed,
            "cultivar_changes": cultivar_changes,
            "non_cultivar_changes": non_cultivar_changes,
            "locked_items": locked,
            "reason": "生物量优先联合优化：在单轮单变量约束下，同时压低生物量误差与产量误差，并约束物候误差不超过15天。",
            "expected_effect": "优先降低叶/茎/总生物量误差，在可接受物候范围内维持或改善产量误差。",
            "risk": "过度优化生物量可能拉高产量误差或导致物候偏移。",
            "revert_note": "可通过 process_bio/output/best（或旧目录 processing/best）中的快照回退。",
        }
        if sobol_plan:
            manifest.update(
                {
                    "calibration_mode": "sobol_phased",
                    "sobol_phase": sobol_plan.get("sobol_phase"),
                    "crop": sobol_meta.get("crop"),
                    "parameter_changed": sobol_meta.get("parameter_name"),
                    "old_value": sobol_meta.get("old_value"),
                    "new_value": sobol_meta.get("new_value"),
                    "adjustment_direction": sobol_meta.get("adjustment_direction"),
                    "diagnosis_reason": sobol_meta.get("diagnosis_reason"),
                    "expected_effect": sobol_meta.get("expected_effect"),
                    "risk": sobol_meta.get("risk"),
                    "whether_phenology_passed": pheno_ok,
                    "whether_yield_component_improved": (
                        (c_custom.get("group_scores") or {}).get("yield") is not None
                        and (b_custom.get("group_scores") or {}).get("yield") is not None
                        and (c_custom.get("group_scores") or {}).get("yield") <= (b_custom.get("group_scores") or {}).get("yield")
                    ),
                    "whether_total_score_improved": metrics["comparison"]["custom_score_improvement_pct"] > 0,
                    "whether_candidate_accepted": better,
                    "reason": f"Sobol 分阶段品种参数校准：阶段 {sobol_plan.get('sobol_phase')}，优先根据敏感性和 signed 误差诊断微调 {sobol_meta.get('crop')}.{sobol_meta.get('parameter_name')}。",
                    "expected_effect": sobol_meta.get("expected_effect"),
                }
            )
        if args.action_mode == "water_yield_search":
            cand_wy = metrics.get("candidate_water_yield", {})
            manifest.update(
                {
                    "optimization_mode": "water_yield_search",
                    "primary_target": "minimize_soil_water_error",
                    "hard_constraints": {
                        "wheat_yield_error_max": args.target_yield_rel,
                        "maize_yield_error_max": args.target_yield_rel,
                        "phenology_error_days_max": args.pheno_guard_days,
                    },
                    "action_type": (water_plan or {}).get("action_type", action),
                    "irrigation_changed": irrigation_changed,
                    "cultivar_changed": bool(cultivar_changes),
                    "irrigation_change": {
                        "old_config": irrigation_meta.get("old_config"),
                        "new_config": irrigation_meta.get("new_config"),
                        "total_irrigation_mm_before": irrigation_meta.get("total_irrigation_mm_before"),
                        "total_irrigation_mm_after": irrigation_meta.get("total_irrigation_mm_after"),
                        "changed_events": irrigation_meta.get("changed_events", []),
                        "reason": irrigation_meta.get("reason"),
                    },
                    "initial_water_change": initial_water_meta,
                    "crit_fr_asw_change": crit_fr_asw_meta,
                    "cultivar_change": {
                        "crop": sobol_meta.get("crop"),
                        "parameter": sobol_meta.get("parameter_name"),
                        "old_value": sobol_meta.get("old_value"),
                        "new_value": sobol_meta.get("new_value"),
                        "direction": sobol_meta.get("adjustment_direction"),
                        "reason": sobol_meta.get("diagnosis_reason"),
                    },
                    "soil_water_diagnosis": soil_water_diagnosis,
                    "yield_constraint": {
                        "wheat_yield_error": cand_wy.get("wheat_yield_error"),
                        "maize_yield_error": cand_wy.get("maize_yield_error"),
                        "passed": cand_wy.get("yield_constraint_passed"),
                    },
                    "acceptance_decision": {
                        "accepted": better,
                        "reason": water_decision.get("reason"),
                        "recovery_search": water_decision.get("recovery_search"),
                    },
                    "reason": f"water_yield_search：以 soil_water 误差为主目标，在 wheat/maize yield error <= {args.target_yield_rel} 和物候守门约束下选择 {action}。",
                    "expected_effect": "优先压低 soil_water 分层误差，同时避免产量和物候被破坏。",
                    "risk": "灌溉变化可能改善水分但推高产量或物候误差；candidate 不满足硬约束会自动回退。",
                }
            )
        if args.action_mode == "hdsw_water_yield_search":
            cand_wy = metrics.get("candidate_hdsw_water_yield", {})
            base_wy = metrics.get("baseline_hdsw_water_yield", {})
            manifest.update(
                {
                    "optimization_mode": "hdsw_water_yield_search",
                    "soil_base": "HDSW",
                    "hdsw_soil_source": str(args.hdsw_soil_source),
                    "primary_target": "minimize_soil_water_error",
                    "hard_constraints": {
                        "wheat_yield_error_max": args.target_yield_rel,
                        "maize_yield_error_max": args.target_yield_rel,
                        "phenology_error_days_max": args.pheno_guard_days,
                    },
                    "allowed_change_types": [
                        "cultivar_parameters",
                        "irrigation_management",
                        "initial_soil_water",
                        "crit_fr_asw",
                    ],
                    "forbidden_change_types": [
                        "weather",
                        "fertilizer",
                        "sowing_density",
                        "soil_physical_parameters",
                        "rotation",
                        "tillage",
                        "residue",
                        "observations",
                    ],
                    "action_type": (water_plan or {}).get("action_type", action),
                    "soil_water_diagnosis": soil_water_diagnosis,
                    "yield_diagnosis": yield_diagnosis,
                    "irrigation_change": irrigation_meta,
                    "initial_water_change": initial_water_meta,
                    "crit_fr_asw_change": crit_fr_asw_meta,
                    "cultivar_change": {
                        "crop": sobol_meta.get("crop"),
                        "parameter": sobol_meta.get("parameter_name"),
                        "old_value": sobol_meta.get("old_value"),
                        "new_value": sobol_meta.get("new_value"),
                        "direction": sobol_meta.get("adjustment_direction"),
                        "reason": sobol_meta.get("diagnosis_reason"),
                    },
                    "candidate_scores": {
                        "hdsw_water_yield_score": cand_wy.get("hdsw_water_yield_score"),
                        "baseline_hdsw_water_yield_score": base_wy.get("hdsw_water_yield_score"),
                        "soil_water_error": cand_wy.get("soil_water_error"),
                        "wheat_yield_error": cand_wy.get("wheat_yield_error"),
                        "maize_yield_error": cand_wy.get("maize_yield_error"),
                        "phenology_error_days_max": cand_wy.get("phenology_error_days_max"),
                    },
                    "acceptance_decision": {
                        "accepted": better,
                        "reason": water_decision.get("reason"),
                        "recovery_search": water_decision.get("recovery_search"),
                    },
                    "reason": f"HDSW water-yield 搜索：以 HDSW soil 为基础，在 yield<= {args.target_yield_rel} 和物候守门下优先压低 water_1~water_5 误差，本轮选择 {action}。",
                    "expected_effect": "先恢复 yield 约束，再通过 InitialWater、crit_fr_asw 和灌溉管理降低 soil_water 分层误差。",
                    "risk": "HDSW soil 的水分库与原品种/灌溉组合可能不匹配；candidate 不满足 yield/phenology 或水分目标会自动回退。",
                }
            )
        base.ensure_manifest_cn(manifest)
        g = c_custom.get("group_scores", {})
        sobol_summary = ""
        if sobol_plan:
            sobol_summary = (
                f"\nSobol 分阶段校准信息\n"
                f"- 阶段：{sobol_plan.get('sobol_phase')}\n"
                f"- 作物：{sobol_meta.get('crop')}\n"
                f"- 参数：{sobol_meta.get('parameter_name')}\n"
                f"- 修改方向：{sobol_meta.get('adjustment_direction')}\n"
                f"- from -> to：{base.cn(sobol_meta.get('old_value'), 6)} -> {base.cn(sobol_meta.get('new_value'), 6)}\n"
                f"- 诊断依据：{sobol_meta.get('diagnosis_reason')}\n"
                f"- 预期影响：{sobol_meta.get('expected_effect')}\n"
                f"- 风险控制：{sobol_meta.get('risk')}\n"
                f"- 物候守门是否通过：{'是' if pheno_ok else '否'}\n"
                f"- 候选是否接受：{'是' if better else '否'}\n\n"
            )
        water_summary = ""
        if args.action_mode in ("water_yield_search", "hdsw_water_yield_search"):
            bw = metrics.get("baseline_hdsw_water_yield", metrics.get("baseline_water_yield", {}))
            cw = metrics.get("candidate_hdsw_water_yield", metrics.get("candidate_water_yield", {}))
            layer_lines = []
            for layer, info in (soil_water_diagnosis.get("layers") or {}).items():
                layer_lines.append(
                    f"  - {layer}: mean_rel={base.cn(info.get('mean_rel_error'))}, bias={info.get('bias')}, signed={base.cn(info.get('mean_signed_error'))}"
                )
            water_summary = (
                f"\n{'HDSW ' if args.action_mode == 'hdsw_water_yield_search' else ''}Water-yield 搜索信息\n"
                f"- HDSW soil：{'是' if args.action_mode == 'hdsw_water_yield_search' else '否'}；来源：{args.hdsw_soil_source if args.action_mode == 'hdsw_water_yield_search' else 'NA'}\n"
                f"- action_type：{(water_plan or {}).get('action_type', action)}\n"
                f"- soil_water baseline -> candidate：{base.cn(bw.get('soil_water_error'))} -> {base.cn(cw.get('soil_water_error'))}\n"
                f"- water_yield_score baseline -> candidate：{base.cn(bw.get('water_yield_score'), 6)} -> {base.cn(cw.get('water_yield_score'), 6)}\n"
                f"- wheat yield error：{base.cn(cw.get('wheat_yield_error'))}；maize yield error：{base.cn(cw.get('maize_yield_error'))}\n"
                f"- 产量硬约束是否通过：{'是' if cw.get('yield_constraint_passed') else '否'}\n"
                f"- 物候是否过关：{'是' if cw.get('phenology_passed') else '否'}\n"
                f"- soil_water 推荐动作：{soil_water_diagnosis.get('recommended_action')}\n"
                f"- 分层诊断：\n" + "\n".join(layer_lines[:5]) + "\n"
                f"- 灌溉变化：{json.dumps(irrigation_meta, ensure_ascii=False) if irrigation_meta else '无'}\n"
                f"- 初始水变化：{json.dumps(initial_water_meta, ensure_ascii=False) if initial_water_meta else '无'}\n"
                f"- crit_fr_asw 变化：{json.dumps(crit_fr_asw_meta, ensure_ascii=False) if crit_fr_asw_meta else '无'}\n"
                f"- 是否接受 candidate：{'是' if better else '否'}\n"
                f"- 接受/拒绝原因：{water_decision.get('reason') or 'NA'}\n\n"
            )
        summary = (
            f"第 {iter_no} 轮完成\n\n"
            f"一、本轮目标\n"
            f"- 优化对象：{'HDSW Water-yield 水分-产量约束搜索' if args.action_mode == 'hdsw_water_yield_search' else ('Water-yield 水分-产量约束搜索' if args.action_mode == 'water_yield_search' else ('Sobol 分阶段品种参数校准' if sobol_plan else '小麦与玉米（生物量优先）'))}\n"
            f"- 迭代策略：{scope}\n"
            f"- 评分模式：{metrics.get('score_mode')}\n"
            f"- 物候约束：max(小麦/玉米物候误差) <= {args.pheno_guard_days} 天\n\n"
            f"{sobol_summary}"
            f"{water_summary}"
            f"二、本轮修改\n"
            f"- 新增品种：{', '.join([x['new_cultivar'] for x in cultivar_changes]) if cultivar_changes else '无'}\n"
            f"- 继承来源：{', '.join([x['derived_from'] for x in cultivar_changes]) if cultivar_changes else '无'}\n"
            f"- 是否修改非品种因子：{'是' if non_cultivar_changed else '否'}\n"
            f"- 非品种修改：{json.dumps(non_cultivar_changes, ensure_ascii=False) if non_cultivar_changes else '无'}\n"
            f"- 锁定未改：{' / '.join(locked)}\n\n"
            f"三、候选结果（全真值综合分）\n"
            f"- 全真值综合分：{base.cn(c_custom.get('all_truth_score', c_custom.get('custom_score')), 6)}\n"
            f"- 各组误差 total_biomass：{base.cn(g.get('total_biomass'))}\n"
            f"- 各组误差 structure：{base.cn(g.get('structure_biomass_leaf_stem'))}\n"
            f"- 各组误差 yield：{base.cn(g.get('yield'))}\n"
            f"- 各组误差 LAI：{base.cn(g.get('LAI'))}\n"
            f"- 各组误差 soil_water：{base.cn(g.get('soil_water'))}\n"
            f"- 各组误差 phenology：{base.cn(g.get('phenology'))}\n"
            f"- 参与评分真值条数：{c_custom.get('truth_rows_used')}\n"
            f"- 缺失模拟值条数：{c_custom.get('truth_rows_missing_sim')}\n"
            f"- rel_error_cap：{c_custom.get('rel_error_cap')}\n"
            f"- missing_truth_penalty：{c_custom.get('missing_truth_penalty')}\n"
            f"- 物候误差最大值（天）：{base.cn(c_custom['phenology_error_days_max'], 1)}\n"
            f"- 小麦 anchor 诊断（均值）：{base.cn(c_custom.get('wheat_anchor_rel_mean'))}\n"
            f"- 小麦晚期 anchor 诊断（{candidate_anchor.get('wheat_late_anchor_date') or 'NA'}）：{base.cn(c_custom.get('wheat_late_anchor_rel_error'))}\n"
            f"- 小麦 anchor 诊断（最大）：{base.cn(c_custom.get('wheat_anchor_rel_max'))}\n\n"
            f"四、判断\n"
            f"- 是否优于上一轮：{'是' if better else '否'}\n"
            f"- 改善幅度：{metrics['comparison']['custom_score_improvement_pct']:.4f}%\n"
            f"- 是否满足阈值（bio<={args.target_bio_rel}, structure<={args.target_structure_rel}, yield<={args.target_yield_rel}, soil<={args.target_soil_water_rel}, pheno_norm<={args.target_phenology_norm}, pheno_days<={args.pheno_guard_days}）："
            f"{'是' if success_reached(c_custom, args.pheno_guard_days, args.target_bio_rel, args.target_yield_rel, args.target_structure_rel, args.target_soil_water_rel, args.target_phenology_norm) else '否'}\n\n"
            f"五、结果目录\n"
            f"- process_bio/output/iter_{iter_no:03d}/\n"
        )

        base.write_text(before_dir / "truth.apsim", truth_before)
        base.write_text(before_dir / "Wheat.xml", wheat_before)
        base.write_text(before_dir / "Maize.xml", maize_before)
        base.write_text(after_dir / "truth.apsim", truth_after)
        base.write_text(after_dir / "Wheat.xml", wheat_after)
        base.write_text(after_dir / "Maize.xml", maize_after)
        base.write_text(iter_dir / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
        base.write_text(iter_dir / "change_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        base.write_text(iter_dir / "summary_zh.md", summary)
        write_prediction_vs_truth_report(
            iter_dir,
            scored_rows.get("baseline", baseline_rows),
            scored_rows.get("candidate", candidate_rows),
            b_custom,
            c_custom,
        )
        if args.action_mode in ("water_yield_search", "hdsw_water_yield_search"):
            write_water_yield_stage_alignment(iter_dir / "stage_alignment.csv", water_plan or {}, irrigation_meta, sobol_meta, metrics, better, water_decision)
        elif sobol_plan:
            write_sobol_stage_alignment(iter_dir / "stage_alignment.csv", sobol_plan, sobol_meta, metrics, better)
        else:
            base.write_stage_alignment(iter_dir / "stage_alignment.csv")
        base.write_patch(truth_before, truth_after, wheat_before, wheat_after, maize_before, maize_after, iter_dir / "patch.diff")

        cand_w = base.get_manager_cultivar(truth_after, "Wheat Management")
        cand_m = base.get_manager_cultivar(truth_after, "Maize Management")
        write_index_row(
            {
                "iteration": iter_no,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "change_scope": scope,
                "wheat_cultivar": cand_w,
                "maize_cultivar": cand_m,
                "baseline_custom_score": b_custom["custom_score"],
                "candidate_custom_score": c_custom["custom_score"],
                "improvement_pct": metrics["comparison"]["custom_score_improvement_pct"],
                "is_best": str(better),
            }
        )

        if better:
            current_best_wheat = cand_w
            current_best_maize = cand_m
            BEST_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copy2(base.TRUTH_PATH, BEST_DIR / "truth.apsim")
            shutil.copy2(base.WHEAT_PATH, BEST_DIR / "Wheat.xml")
            shutil.copy2(base.MAIZE_PATH, BEST_DIR / "Maize.xml")
            base.write_text(BEST_DIR / "metrics.json", json.dumps(metrics, ensure_ascii=False, indent=2))
            base.write_text(BEST_DIR / "change_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            base.write_text(BEST_DIR / "summary_zh.md", summary)
            shutil.copy2(iter_dir / "prediction_vs_truth.csv", BEST_DIR / "prediction_vs_truth.csv")
            shutil.copy2(iter_dir / "prediction_vs_truth_summary.md", BEST_DIR / "prediction_vs_truth_summary.md")
            if args.action_mode in ("water_yield_search", "hdsw_water_yield_search"):
                shutil.copy2(iter_dir / "stage_alignment.csv", BEST_DIR / "stage_alignment.csv")
            elif sobol_plan:
                shutil.copy2(iter_dir / "stage_alignment.csv", BEST_DIR / "stage_alignment.csv")
            else:
                base.write_stage_alignment(BEST_DIR / "stage_alignment.csv")
            base.write_text(
                BEST_DIR / "best_selection.json",
                json.dumps(
                    {
                        "iteration": iter_no,
                        "custom_score": c_custom["custom_score"],
                        "targets": {
                            "target_bio_rel": args.target_bio_rel,
                            "target_structure_rel": args.target_structure_rel,
                            "target_yield_rel": args.target_yield_rel,
                            "target_soil_water_rel": args.target_soil_water_rel,
                            "target_phenology_norm": args.target_phenology_norm,
                            "pheno_guard_days": args.pheno_guard_days,
                        },
                        "objective_weights": {
                            "wheat_anchor_weight": args.wheat_anchor_weight,
                            "wheat_late_weight": args.wheat_late_weight,
                            "wheat_anchor_max_weight": args.wheat_anchor_max_weight,
                            "wheat_late_stage_cn": args.wheat_late_stage_cn,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        else:
            # Revert to current best state.
            base.write_text(base.TRUTH_PATH, truth_before)
            base.write_text(base.WHEAT_PATH, wheat_before)
            base.write_text(base.MAIZE_PATH, maize_before)

        if args.action_mode == "water_yield_search":
            ok = success_reached_water_yield(metrics.get("candidate_water_yield", {}), args)
            c_soil = (metrics.get("candidate_water_yield") or {}).get("soil_water_error")
            if better and c_soil is not None and (best_water_soil is None or float(c_soil) < float(best_water_soil) - float(args.min_soil_water_improvement)):
                best_water_soil = float(c_soil)
                no_water_improve_count = 0
            else:
                no_water_improve_count += 1
        elif args.action_mode == "hdsw_water_yield_search":
            ok = success_reached_hdsw_water_yield(metrics.get("candidate_hdsw_water_yield", {}), args)
            c_soil = (metrics.get("candidate_hdsw_water_yield") or {}).get("soil_water_error")
            if better and c_soil is not None and (best_water_soil is None or float(c_soil) < float(best_water_soil) - float(args.min_soil_water_improvement)):
                best_water_soil = float(c_soil)
                no_water_improve_count = 0
            else:
                no_water_improve_count += 1
        else:
            ok = success_reached(
                c_custom,
                args.pheno_guard_days,
                args.target_bio_rel,
                args.target_yield_rel,
                args.target_structure_rel,
                args.target_soil_water_rel,
                args.target_phenology_norm,
            )
        print(
            f"iter_{iter_no:03d}: action={action} "
            f"phase={(sobol_plan or {}).get('sobol_phase', 'NA')} "
            f"param={sobol_meta.get('parameter_name', 'NA')} "
            f"better={better} "
            f"custom={c_custom['custom_score']:.6f} "
            f"water_yield_score={(metrics.get('candidate_hdsw_water_yield') or metrics.get('candidate_water_yield') or {}).get('water_yield_score', 'NA')} "
            f"bio={c_custom['total_biomass_error_rel_mean_all_crops']:.6f} "
            f"yield={c_custom['yield_error_rel_mean']:.6f} "
            f"pheno_max={c_custom['phenology_error_days_max']} "
            f"target_ok={ok}"
        )
        if ok:
            found_success = True
            break
        if args.action_mode in ("water_yield_search", "hdsw_water_yield_search") and no_water_improve_count >= int(args.water_yield_patience):
            base.write_text(
                PROCESS_BIO / ("hdsw_water_yield_stop_summary.json" if args.action_mode == "hdsw_water_yield_search" else "water_yield_stop_summary.json"),
                json.dumps(
                    {
                        "stop_reason": "water_yield_patience reached",
                        "patience": args.water_yield_patience,
                        "best_soil_water_error_seen": best_water_soil,
                        "last_iteration": iter_no,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            break

    # Ensure root truth points at process_bio best if exists.
    if (BEST_DIR / "truth.apsim").exists():
        shutil.copy2(BEST_DIR / "truth.apsim", base.TRUTH_PATH)
    if (BEST_DIR / "Wheat.xml").exists():
        shutil.copy2(BEST_DIR / "Wheat.xml", base.WHEAT_PATH)
    if (BEST_DIR / "Maize.xml").exists():
        shutil.copy2(BEST_DIR / "Maize.xml", base.MAIZE_PATH)

    print(f"process_bio_done found_success={found_success}")


if __name__ == "__main__":
    main()
