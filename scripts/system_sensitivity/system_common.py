"""Shared utilities for APSIM Classic system-level sensitivity analysis.

This workflow starts from a calibrated cultivar baseline and perturbs only
system/process parameters in the .apsim file. Weather, initial water, initial
nitrogen, and cultivar XML files are treated as fixed inputs.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd
from lxml import etree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BEST_DIR = PROJECT_ROOT / "results" / "local_sobol_guided_search_20260519_output_sobol" / "best"
BASELINE_DIR = Path(
    os.environ.get(
        "SYSTEM_BASELINE_DIR",
        PROJECT_ROOT / "models" / "apsim_classic" / "calibrated_baseline",
    )
)
BASE_APSIM = Path(
    os.environ.get(
        "SYSTEM_BASE_APSIM",
        BASELINE_DIR / "baseline_after_cultivar_sobol.apsim",
    )
)
DEFAULT_WEATHER_FILE = PROJECT_ROOT / "data" / "weather" / "apsim_met" / "p0-1-24-25.met"
SYSTEM_DIR = Path(
    os.environ.get(
        "SYSTEM_SENSITIVITY_OUTPUT_DIR",
        PROJECT_ROOT / "outputs" / "system_sensitivity",
    )
)
FINAL_RESULTS_DIR = SYSTEM_DIR / "final_results"
INTERMEDIATE_DIR = SYSTEM_DIR / "intermediate_and_raw_files"
HELPER_DIR = SYSTEM_DIR / "helper_files"
APS_RUN_DIR = INTERMEDIATE_DIR / "apsim_runs"
LOG_DIR = INTERMEDIATE_DIR / "logs"
FIG_DIR = FINAL_RESULTS_DIR / "figures"

PARAMETER_RANGES_CSV = FINAL_RESULTS_DIR / "system_parameter_ranges_template.csv"
MORRIS_SAMPLES_WIDE_CSV = FINAL_RESULTS_DIR / "morris_samples_wide.csv"
MORRIS_SAMPLES_LONG_CSV = INTERMEDIATE_DIR / "morris_samples_long.csv"
MORRIS_PROBLEM_JSON = INTERMEDIATE_DIR / "morris_problem_definition.json"
SIM_INDEX_CSV = FINAL_RESULTS_DIR / "simulation_index.csv"
PARAM_TRACE_CSV = INTERMEDIATE_DIR / "parameter_trace_long.csv"
MORRIS_INDICES_CSV = FINAL_RESULTS_DIR / "morris_indices_summary.csv"
SOBOL_PARAMETER_RANGES_CSV = FINAL_RESULTS_DIR / "sobol_screened_parameter_ranges.csv"
SOBOL_SAMPLES_WIDE_CSV = FINAL_RESULTS_DIR / "sobol_samples_wide.csv"
SOBOL_SAMPLES_LONG_CSV = INTERMEDIATE_DIR / "sobol_samples_long.csv"
SOBOL_PROBLEM_JSON = INTERMEDIATE_DIR / "sobol_problem_definition.json"
SOBOL_INDICES_SUMMARY_CSV = FINAL_RESULTS_DIR / "sobol_indices_summary.csv"
SOBOL_MISSING_VALUES_REPORT_CSV = INTERMEDIATE_DIR / "sobol_missing_values_report.csv"

TARGET_CROP_CULTIVARS = [
    ("wheat", "calibrated_wheat"),
    ("maize", "calibrated_maize"),
]

SYSTEM_REPORT_VARIABLES = [
    "wheat.FloweringDAS as WheatFloweringDAS",
    "maize.FloweringDAS as MaizeFloweringDAS",
    "wheat.maturity_das as WheatMaturityDAS",
    "maize.maturity_das as MaizeMaturityDAS",
    "wheat.DaysToMaturity as WheatDaysToMaturity",
    "maize.DaysToMaturity as MaizeDaysToMaturity",
    "wheat.GrainNo as WheatGrainNo",
    "maize.GrainNo as MaizeGrainNo",
    "wheat.GrainSize as WheatGrainSize",
    "maize.GrainSize as MaizeGrainSize",
    "es as SoilEvaporation",
]

for i in range(1, 17):
    SYSTEM_REPORT_VARIABLES.append(f"wheat.sw_uptake({i}) as WheatWaterUptake{i}")
    SYSTEM_REPORT_VARIABLES.append(f"maize.sw_uptake({i}) as MaizeWaterUptake{i}")

SCALAR_SPECS = [
    ("soilwater", "SoilWater", "CN2Bare", "soil_water_process", 0.85, 1.15, "runoff curve number for bare soil"),
    ("soilwater", "SoilWater", "CNRed", "soil_water_process", 0.85, 1.15, "cover effect on curve number"),
    ("soilwater", "SoilWater", "CNCov", "soil_water_process", 0.85, 1.15, "cover threshold for runoff"),
    ("soilwater", "SoilWater", "Salb", "soil_water_process", 0.8, 1.2, "bare soil albedo"),
    ("soilwater", "SoilWater", "U", "soil_water_process", 0.7, 1.3, "stage-1 soil evaporation limit"),
    ("soilwater", "SoilWater", "Cona", "soil_water_process", 0.7, 1.3, "stage-2 soil evaporation coefficient"),
    ("soilwater", "SoilWater", "DiffusConst", "soil_water_process", 0.7, 1.3, "soil water diffusivity constant"),
    ("soilwater", "SoilWater", "DiffusSlope", "soil_water_process", 0.7, 1.3, "soil water diffusivity slope"),
    ("som", "SoilOrganicMatter", "RootCN", "soil_nitrogen_organic_matter", 0.85, 1.15, "root residue carbon:nitrogen ratio"),
    ("som", "SoilOrganicMatter", "SoilCN", "soil_nitrogen_organic_matter", 0.85, 1.15, "soil carbon:nitrogen ratio"),
    ("irrigation", "irrigation", "irrigation_efficiency", "management_optional", 0.8, 1.05, "irrigation application efficiency"),
]

VECTOR_GROUP_SPECS = [
    ("soilwater", "SoilWater", "SWCON", "soil_water_process", 0.7, 1.3, "drainage coefficient", "all_layers"),
    ("som", "SoilOrganicMatter", "OC", "soil_nitrogen_organic_matter", 0.8, 1.2, "organic carbon", "all_layers"),
    ("som", "SoilOrganicMatter", "FBiom", "soil_nitrogen_organic_matter", 0.8, 1.2, "biomass carbon fraction", "all_layers"),
    ("som", "SoilOrganicMatter", "FInert", "soil_nitrogen_organic_matter", 0.8, 1.0, "inert carbon fraction", "all_layers"),
]

ROOT_VECTOR_NAMES = ["KL", "XF"]
FIXED_INITIAL_SPECS = [
    ("InitialWater", "FractionFull", "initialwater__FractionFull__fixed"),
    ("InitialWater", "DepthWetSoil", "initialwater__DepthWetSoil__fixed"),
    ("Sample", "NO3", "initialnitrogen__NO3__fixed"),
    ("Sample", "NH4", "initialnitrogen__NH4__fixed"),
]


def ensure_dirs() -> None:
    for path in [BASELINE_DIR, SYSTEM_DIR, FINAL_RESULTS_DIR, INTERMEDIATE_DIR, HELPER_DIR, APS_RUN_DIR, LOG_DIR, FIG_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    return etree.parse(str(path), parser)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(path), encoding="utf-8", xml_declaration=False, pretty_print=True)


def ensure_system_report_variables(apsim_path: Path) -> int:
    """Add derived-output source variables to the Phases report if missing."""
    tree = parse_xml(apsim_path)
    reports = tree.xpath(".//*[local-name()='outputfile'][@name='Phases']")
    if not reports:
        return 0
    report = reports[0]
    variables_nodes = report.xpath("./*[local-name()='variables']")
    if variables_nodes:
        variables = variables_nodes[0]
    else:
        variables = etree.SubElement(report, "variables", name="Variables")
    existing = {clean_text(node.text) for node in variables.xpath("./*[local-name()='variable']")}
    added = 0
    for text in SYSTEM_REPORT_VARIABLES:
        if text in existing:
            continue
        node = etree.SubElement(variables, "variable")
        node.text = text
        added += 1
    if added:
        write_xml(tree, apsim_path)
    return added


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


def format_float(value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.10g}"


def safe_key_part(text: object) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", clean_text(text))
    return value.strip("_") or "unknown"


def local_xpath(element: etree._Element) -> str:
    parts = []
    cur = element
    while cur is not None and isinstance(cur.tag, str):
        tag = etree.QName(cur).localname
        name = cur.get("name")
        label = f"*[local-name()='{tag}']"
        if name:
            label += f"[@name='{name}']"
        parts.append(label)
        cur = cur.getparent()
    ordered = list(reversed(parts))
    if ordered:
        ordered = ordered[1:]
    return "." if not ordered else "./" + "/".join(ordered)


def first_node(root: etree._Element, parent_name: str, child_name: str) -> etree._Element | None:
    nodes = root.xpath(f".//*[local-name()=$parent]/*[local-name()=$child]", parent=parent_name, child=child_name)
    return nodes[0] if nodes else None


def vector_values(node: etree._Element) -> list[float]:
    children = node.xpath("./*[local-name()='double']")
    values = [as_float(child.text) for child in children]
    return [v for v in values if v is not None]


def scalar_row(
    root: etree._Element,
    prefix: str,
    parent: str,
    child: str,
    group: str,
    lower_factor: float,
    upper_factor: float,
    meaning: str,
) -> dict | None:
    node = first_node(root, parent, child)
    if node is None:
        return None
    baseline = as_float(node.text)
    if baseline is None:
        return None
    lower = min(baseline * lower_factor, baseline * upper_factor)
    upper = max(baseline * lower_factor, baseline * upper_factor)
    if child == "irrigation_efficiency":
        lower = max(0.0, lower)
        upper = min(1.0, upper)
    return {
        "parameter_key": f"{prefix}__{child}__scalar",
        "group": group,
        "module": parent,
        "parameter_name": child,
        "baseline_value": baseline,
        "lower_bound": lower,
        "upper_bound": upper,
        "perturbation_type": "absolute_scalar",
        "include_in_morris": "TRUE",
        "include_in_sobol": "FALSE",
        "fixed_reason": "",
        "biological_meaning": meaning,
        "xml_path": local_xpath(node),
        "target_kind": "apsim_scalar",
        "layer_indices": "",
        "crop": "",
        "notes": "System process parameter; review bounds before formal run.",
    }


def vector_multiplier_row(
    root: etree._Element,
    prefix: str,
    parent: str,
    child: str,
    group: str,
    lower_factor: float,
    upper_factor: float,
    meaning: str,
    layer_group: str,
) -> dict | None:
    node = first_node(root, parent, child)
    if node is None or not vector_values(node):
        return None
    return {
        "parameter_key": f"{prefix}__{child}__{layer_group}_multiplier",
        "group": group,
        "module": parent,
        "parameter_name": child,
        "baseline_value": 1.0,
        "lower_bound": lower_factor,
        "upper_bound": upper_factor,
        "perturbation_type": "vector_multiplier",
        "include_in_morris": "TRUE",
        "include_in_sobol": "FALSE",
        "fixed_reason": "",
        "biological_meaning": meaning,
        "xml_path": local_xpath(node),
        "target_kind": "apsim_vector",
        "layer_indices": "",
        "crop": "",
        "notes": "Multiplier applied to all existing layer values.",
    }


def root_vector_rows(root: etree._Element) -> list[dict]:
    rows = []
    for crop_node in root.xpath(".//*[local-name()='SoilCrop']"):
        crop = clean_text(crop_node.get("name"))
        if not crop:
            continue
        for vector_name in ROOT_VECTOR_NAMES:
            nodes = crop_node.xpath("./*[local-name()=$name]", name=vector_name)
            if not nodes or not vector_values(nodes[0]):
                continue
            meaning = "root water extraction coefficient" if vector_name == "KL" else "root exploration factor"
            lower, upper = (0.7, 1.3) if vector_name == "KL" else (0.8, 1.1)
            rows.append(
                {
                    "parameter_key": f"soilcrop__{safe_key_part(crop)}__{vector_name}__all_layers_multiplier",
                    "group": "root_water_uptake",
                    "module": "SoilCrop",
                    "parameter_name": vector_name,
                    "baseline_value": 1.0,
                    "lower_bound": lower,
                    "upper_bound": upper,
                    "perturbation_type": "vector_multiplier",
                    "include_in_morris": "TRUE",
                    "include_in_sobol": "FALSE",
                    "fixed_reason": "",
                    "biological_meaning": meaning,
                    "xml_path": local_xpath(nodes[0]),
                    "target_kind": "apsim_vector",
                    "layer_indices": "",
                    "crop": crop,
                    "notes": "Multiplier applied to all existing layer values.",
                }
            )
    return rows


def fixed_initial_rows(root: etree._Element) -> list[dict]:
    rows = []
    for parent, child, key in FIXED_INITIAL_SPECS:
        node = first_node(root, parent, child)
        if node is None:
            continue
        values = vector_values(node)
        baseline = ";".join(format_float(v) for v in values) if values else clean_text(node.text)
        rows.append(
            {
                "parameter_key": key,
                "group": "fixed_real_initial_condition",
                "module": parent,
                "parameter_name": child,
                "baseline_value": baseline,
                "lower_bound": "",
                "upper_bound": "",
                "perturbation_type": "fixed",
                "include_in_morris": "FALSE",
                "include_in_sobol": "FALSE",
                "fixed_reason": "reliable_real_initial_condition",
                "biological_meaning": "Measured/reliable initial condition; fixed by study design.",
                "xml_path": local_xpath(node),
                "target_kind": "fixed_initial_condition",
                "layer_indices": "",
                "crop": "",
                "notes": "Do not perturb in this system sensitivity workflow.",
            }
        )
    return rows


def build_system_parameter_rows(apsim_path: Path) -> list[dict]:
    tree = parse_xml(apsim_path)
    root = tree.getroot()
    rows: list[dict] = []
    for spec in SCALAR_SPECS:
        row = scalar_row(root, *spec)
        if row:
            rows.append(row)
    for spec in VECTOR_GROUP_SPECS:
        row = vector_multiplier_row(root, *spec)
        if row:
            rows.append(row)
    rows.extend(root_vector_rows(root))
    rows.extend(fixed_initial_rows(root))
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("No rows to write")
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_included_ranges(path: Path = PARAMETER_RANGES_CSV, include_col: str = "include_in_morris") -> pd.DataFrame:
    df = pd.read_csv(path)
    if include_col not in df.columns:
        raise ValueError(f"Missing {include_col} column in {path}")
    included = df[df[include_col].astype(str).str.strip().str.upper() == "TRUE"].copy()
    if included.empty:
        raise ValueError(f"No parameters with {include_col}=TRUE in {path}")
    for col in ["lower_bound", "upper_bound"]:
        included[col] = pd.to_numeric(included[col], errors="coerce")
    bad = included[included[["lower_bound", "upper_bound"]].isna().any(axis=1)]
    if not bad.empty:
        raise ValueError(f"Included parameters have invalid bounds: {bad['parameter_key'].tolist()[:10]}")
    return included


def repair_weather_path(apsim_path: Path, weather_path: Path | None) -> str:
    tree = parse_xml(apsim_path)
    nodes = tree.xpath(".//*[local-name()='metfile']/*[local-name()='filename']")
    if not nodes:
        return ""
    current = Path(clean_text(nodes[0].text))
    if current.exists():
        return str(current)
    candidate = weather_path or DEFAULT_WEATHER_FILE
    if candidate and Path(candidate).exists():
        nodes[0].text = str(Path(candidate))
        write_xml(tree, apsim_path)
        return str(Path(candidate))
    return str(current)


def copy_calibrated_baseline(best_dir: Path, target_dir: Path = BASELINE_DIR, weather_path: Path | None = None) -> dict:
    best_dir = Path(best_dir)
    target_dir = Path(target_dir)
    apsim_src = best_dir / "truth.apsim"
    if not apsim_src.exists():
        apsim_files = sorted(best_dir.glob("*.apsim"))
        if not apsim_files:
            raise FileNotFoundError(f"No .apsim file found in calibrated best dir: {best_dir}")
        apsim_src = apsim_files[0]
    target_dir.mkdir(parents=True, exist_ok=True)
    apsim_dst = target_dir / "baseline_after_cultivar_sobol.apsim"
    shutil.copy2(apsim_src, apsim_dst)
    fixed_weather = repair_weather_path(apsim_dst, weather_path)
    added_report_variables = ensure_system_report_variables(apsim_dst)
    copied_crop_xml = {}
    for crop_xml in ["Wheat.xml", "Maize.xml"]:
        src = best_dir / crop_xml
        if src.exists():
            dst = target_dir / crop_xml
            shutil.copy2(src, dst)
            copied_crop_xml[crop_xml] = str(dst)
    best_selection = read_json_if_exists(best_dir / "best_selection.json")
    metrics = read_json_if_exists(best_dir / "metrics.json")
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_best_dir": str(best_dir),
        "baseline_apsim": str(apsim_dst),
        "crop_xml": copied_crop_xml,
        "fixed_inputs": {
            "cultivar_parameters": "fixed_from_calibrated_best",
            "weather": "fixed_reliable_real_driver",
            "weather_file": fixed_weather,
            "initial_soil_water": "fixed_reliable_real_initial_condition",
            "initial_soil_nitrogen": "fixed_reliable_real_initial_condition",
        },
        "best_selection": best_selection,
        "metrics": metrics,
        "report_variables_added": added_report_variables,
    }
    with open(target_dir / "baseline_manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
    return manifest


def read_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8", errors="ignore") as handle:
        return json.load(handle)


def set_output_filenames(tree: etree._ElementTree, sample_id: int) -> str:
    out_dir = APS_RUN_DIR / "outputs" / f"sample_{sample_id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_files = []
    for node in tree.xpath(".//*[local-name()='outputfile']/*[local-name()='filename']"):
        old_name = clean_text(node.text) or f"sample_{sample_id:06d}.out"
        new_path = out_dir / Path(old_name).name
        node.text = str(new_path)
        output_files.append(str(new_path))
    return ";".join(output_files)


def apply_scalar(root: etree._Element, row: dict, value: float) -> tuple[bool, str]:
    nodes = root.xpath(clean_text(row["xml_path"]))
    if not nodes:
        return False, f"xml_path not found: {row['xml_path']}"
    nodes[0].text = format_float(value)
    return True, "ok"


def apply_vector_multiplier(root: etree._Element, row: dict, multiplier: float) -> tuple[bool, str]:
    nodes = root.xpath(clean_text(row["xml_path"]))
    if not nodes:
        return False, f"xml_path not found: {row['xml_path']}"
    children = nodes[0].xpath("./*[local-name()='double']")
    if not children:
        return False, f"no <double> layer values under {row['xml_path']}"
    for child in children:
        base = as_float(child.text)
        if base is None:
            return False, f"non-numeric layer value under {row['parameter_key']}: {child.text}"
        child.text = format_float(base * multiplier)
    return True, "ok"


def modify_apsim_for_system_sample(
    apsim_in: Path,
    apsim_out: Path,
    parameter_rows: Iterable[dict],
    sample_values: dict[str, float],
    sample_id: int,
) -> list[dict]:
    tree = parse_xml(apsim_in)
    root = tree.getroot()
    output_files = set_output_filenames(tree, sample_id)
    trace = []
    for row in parameter_rows:
        key = clean_text(row["parameter_key"])
        if key not in sample_values:
            continue
        value = float(sample_values[key])
        if clean_text(row["perturbation_type"]) == "absolute_scalar":
            ok, msg = apply_scalar(root, row, value)
        elif clean_text(row["perturbation_type"]) == "vector_multiplier":
            ok, msg = apply_vector_multiplier(root, row, value)
        else:
            ok, msg = False, f"unsupported perturbation_type: {row['perturbation_type']}"
        trace.append(
            {
                "sample_id": sample_id,
                "crop": clean_text(row.get("crop")) or "system",
                "cultivar": "calibrated_baseline",
                "parameter_name": row["parameter_name"],
                "baseline_value": row["baseline_value"],
                "sampled_value": value,
                "scenario_id": f"sample_{sample_id:06d}",
                "simulation_file": str(apsim_out),
                "output_file": output_files,
                "parameter_key": key,
                "status": "ok" if ok else "failed",
                "message": msg,
                "group": row.get("group", ""),
                "module": row.get("module", ""),
                "perturbation_type": row.get("perturbation_type", ""),
            }
        )
    write_xml(tree, apsim_out)
    return trace


def json_dumps_compact(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def target_crop_rows(sample_id: int, apsim_file: Path, output_files: str) -> list[dict]:
    rows = []
    for crop, cultivar in TARGET_CROP_CULTIVARS:
        rows.append(
            {
                "sample_id": sample_id,
                "crop": crop,
                "cultivar": cultivar,
                "parameter_name": "system_parameter_set",
                "baseline_value": "",
                "sampled_value": "",
                "scenario_id": f"sample_{sample_id:06d}",
                "simulation_file": str(apsim_file),
                "output_file": output_files,
                "parameter_key": "system_parameter_set",
                "status": "ok",
                "message": "crop target row for output collection",
                "group": "target_crop",
                "module": "",
                "perturbation_type": "",
            }
        )
    return rows
