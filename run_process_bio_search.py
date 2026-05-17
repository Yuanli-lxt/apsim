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
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import run_joint_single_factor_rounds as base


PROCESS_BIO = Path(r"F:\APSIM710-r4221\process_bio\output")
INDEX_PATH = PROCESS_BIO / "iteration_index.csv"
BEST_DIR = PROCESS_BIO / "best"
WHEAT_LATE_STAGE_CN_DEFAULT = "蜡熟期"
DEFAULT_VALIDATION_CSV = Path(__file__).resolve().parent / "independent_validation_observations_p02_maize_p01_wheat.csv"
DEFAULT_TRUTH_TEMPLATE = Path(__file__).resolve().parent / "modified_from_truth.apsim"
EPS = 1e-9

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
    p.add_argument("--pheno_guard_days", type=int, default=15)
    p.add_argument("--target_bio_rel", type=float, default=0.30, help="target for total_biomass group rel error")
    p.add_argument("--target_structure_rel", type=float, default=0.45, help="target for structure_biomass_leaf_stem group rel error")
    p.add_argument("--target_yield_rel", type=float, default=0.06, help="target for yield group rel error")
    p.add_argument("--target_soil_water_rel", type=float, default=0.25, help="target for soil_water group rel error")
    p.add_argument("--target_phenology_norm", type=float, default=0.20, help="target for phenology normalized error group")
    p.add_argument("--soil_step_scale", type=float, default=0.15)
    p.add_argument("--maize_step_scale", type=float, default=0.25)
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
        choices=["mixed", "wheat_focus", "conservative"],
        help="mixed: joint search; wheat_focus: freeze maize and only adjust wheat variables; conservative: wheat_focus + occasional soil-only",
    )
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

    # Use explicit truth template first; otherwise fallback to existing best snapshots.
    if args.truth_template and Path(args.truth_template).exists():
        shutil.copy2(Path(args.truth_template), base.TRUTH_PATH)
    else:
        seed_dir = None
        if (BEST_DIR / "truth.apsim").exists():
            seed_dir = BEST_DIR
        elif (base.PROCESSING / "best" / "truth.apsim").exists():
            seed_dir = base.PROCESSING / "best"
        if seed_dir is not None:
            shutil.copy2(seed_dir / "truth.apsim", base.TRUTH_PATH)
            shutil.copy2(seed_dir / "Wheat.xml", base.WHEAT_PATH)
            shutil.copy2(seed_dir / "Maize.xml", base.MAIZE_PATH)

    current_truth = base.read_text(base.TRUTH_PATH)
    current_wheat = base.read_text(base.WHEAT_PATH)
    current_maize = base.read_text(base.MAIZE_PATH)
    current_best_wheat = base.get_manager_cultivar(current_truth, "Wheat Management")
    current_best_maize = base.get_manager_cultivar(current_truth, "Maize Management")

    last_iter = read_last_iter()
    found_success = False

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

        action = pick_action(k, baseline_eval, args.action_mode)
        truth_after = truth_before
        wheat_after = wheat_before
        maize_after = maize_before
        cultivar_changes = []
        non_cultivar_changes = []
        scope = "cultivar_only"
        non_cultivar_changed = False

        if action == "wheat_cultivar":
            p0 = base.parse_wheat_params(wheat_before, current_best_wheat)
            if args.wheat_cultivar_mode == "standard":
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
            cultivar_changes.append(
                {
                    "crop": "maize",
                    "new_cultivar": name,
                    "derived_from": current_best_maize,
                    "changed_params": changed,
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
        baseline_rows = collect_prediction_vs_truth_rows(out_base, truth_obj, "baseline")
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
        better = metrics["comparison"]["is_better_than_baseline"] and pheno_ok
        metrics["comparison"]["is_better_than_baseline"] = better

        base_locked = ["weather", "rotation", "sowing_window", "irrigation", "residue", "tillage"]
        if scope == "cultivar_only":
            locked = base_locked + ["soil", "fertilizer"]
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
        base.ensure_manifest_cn(manifest)
        g = c_custom.get("group_scores", {})
        summary = (
            f"第 {iter_no} 轮完成\n\n"
            f"一、本轮目标\n"
            f"- 优化对象：小麦与玉米（生物量优先）\n"
            f"- 迭代策略：{scope}\n"
            f"- 评分模式：{metrics.get('score_mode')}\n"
            f"- 物候约束：max(小麦/玉米物候误差) <= {args.pheno_guard_days} 天\n\n"
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
            f"iter_{iter_no:03d}: action={action} better={better} "
            f"custom={c_custom['custom_score']:.6f} "
            f"bio={c_custom['total_biomass_error_rel_mean_all_crops']:.6f} "
            f"yield={c_custom['yield_error_rel_mean']:.6f} "
            f"pheno_max={c_custom['phenology_error_days_max']} "
            f"target_ok={ok}"
        )
        if ok:
            found_success = True
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
