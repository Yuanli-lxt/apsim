"""Generate, run and aggregate APSIM Classic cases for grid resolution tests.

Grid cells sharing the same HWSD unit and NASA POWER cell share one APSIM run.
The case result is then joined back to every represented grid cell, avoiding
thousands of duplicate simulations without changing the experiment semantics.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
EXPERIMENT = PILOT / "grid_resolution_experiment"
UNITS_CSV = EXPERIMENT / "rotation_simulation_units.csv"
TEMPLATE = ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"
RUN_ROOT = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "grid_resolution_experiment"
CASE_ROOT = RUN_ROOT / "cases"
DEFAULT_APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")


def replace_array(parent: ET.Element, tag: str, values: list[float | str], child_tag: str = "double") -> None:
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    for child in list(node):
        node.remove(child)
    for value in values:
        child = ET.SubElement(node, child_tag)
        if isinstance(value, float):
            child.text = f"{value:.8g}"
        else:
            child.text = str(value)


def existing_array(parent: ET.Element, tag: str) -> list[float]:
    node = parent.find(tag)
    if node is None:
        raise KeyError(f"Missing APSIM soil array {parent.tag}/{tag}")
    return [float((child.text or "nan").strip()) for child in node]


def values_at_new_layer_midpoints(
    old_thickness: list[float], old_values: list[float], new_thickness: list[float]
) -> list[float]:
    old_bottoms = np.cumsum(old_thickness)
    new_bottoms = np.cumsum(new_thickness)
    new_tops = np.r_[0.0, new_bottoms[:-1]]
    midpoints = (new_tops + new_bottoms) / 2.0
    return [float(old_values[min(int(np.searchsorted(old_bottoms, depth, side="right")), len(old_values) - 1)]) for depth in midpoints]


def load_profile(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    layers = data["profile"]["layers"]
    for index, layer in enumerate(layers, start=1):
        for key in ("Thickness", "BD", "AirDry", "LL15", "DUL", "SAT", "crop.LL", "crop.KL", "crop.XF"):
            value = float(layer[key])
            if not math.isfinite(value):
                raise ValueError(f"Non-finite {key} in layer {index}: {path}")
    return layers


def set_soil(root: ET.Element, profile_path: Path, lon: float, lat: float, unit: int) -> None:
    layers = load_profile(profile_path)
    soil = root.find(".//Soil")
    if soil is None:
        raise KeyError("Template has no Soil node")
    metadata = {
        "SoilType": f"HWSD v2 mapping unit {unit}",
        "Site": "Qihe County grid-resolution experiment",
        "State": "Shandong",
        "Country": "China",
        "ApsoilNumber": f"HWSD2_MU_{unit}",
        "Latitude": f"{lat:.8f}",
        "Longitude": f"{lon:.8f}",
        "LocationAccuracy": "HWSD raster mapping unit sampled at county-clipped cell representative point",
        "YearOfSampling": "0",
        "DataSource": "HWSD v2.0; converted by scripts/soil/hwsd_to_apsimsoil.py",
        "Comments": "Preliminary regional profile; PTF/default fields remain flagged in soil_profile.json",
    }
    for tag, value in metadata.items():
        node = soil.find(tag)
        if node is None:
            node = ET.SubElement(soil, tag)
        node.text = value

    thickness = [float(layer["Thickness"]) for layer in layers]
    water = soil.find("Water")
    soil_water = soil.find("SoilWater")
    organic = soil.find("SoilOrganicMatter")
    analysis = soil.find("Analysis")
    nitrogen = soil.find("Sample[@name='InitialNitrogen']")
    if any(node is None for node in (water, soil_water, organic, analysis, nitrogen)):
        raise KeyError("Template soil is missing a required APSIM Classic subsection")
    assert water is not None and soil_water is not None and organic is not None and analysis is not None and nitrogen is not None

    old_sw_thickness = existing_array(soil_water, "Thickness")
    drainage = {
        tag: values_at_new_layer_midpoints(old_sw_thickness, existing_array(soil_water, tag), thickness)
        for tag in ("SWCON", "MWCON", "KLAT")
    }

    replace_array(water, "Thickness", thickness)
    for tag in ("BD", "AirDry", "LL15", "DUL", "SAT"):
        replace_array(water, tag, [float(layer[tag]) for layer in layers])
    for tag in ("LL15Metadata", "DULMetadata", "SATMetadata"):
        replace_array(water, tag, ["HWSD/PTF; see soil_profile.json"] * len(layers), "string")
    for crop_name in ("wheat", "maize"):
        crop = water.find(f"SoilCrop[@name='{crop_name}']")
        if crop is None:
            raise KeyError(f"Template has no SoilCrop {crop_name}")
        replace_array(crop, "Thickness", thickness)
        replace_array(crop, "LL", [float(layer["crop.LL"]) for layer in layers])
        replace_array(crop, "KL", [float(layer["crop.KL"]) for layer in layers])
        replace_array(crop, "XF", [float(layer["crop.XF"]) for layer in layers])

    replace_array(soil_water, "Thickness", thickness)
    for tag, values in drainage.items():
        replace_array(soil_water, tag, values)

    replace_array(organic, "Thickness", thickness)
    replace_array(organic, "OC", [float(layer["Carbon"]) for layer in layers])
    replace_array(organic, "OCMetadata", ["HWSD/PTF; see soil_profile.json"] * len(layers), "string")
    replace_array(organic, "FBiom", [float(layer["FBiom"]) for layer in layers])
    replace_array(organic, "FInert", [float(layer["FInert"]) for layer in layers])
    soil_cn = organic.find("SoilCN")
    if soil_cn is not None:
        soil_cn.text = f"{np.average([float(layer['SoilCNRatio']) for layer in layers], weights=thickness):.6g}"

    replace_array(analysis, "Thickness", thickness)
    replace_array(analysis, "Texture", ["Unknown"] * len(layers), "string")
    replace_array(analysis, "TextureMetadata", ["HWSD texture fractions retained in soil_profile.json"] * len(layers), "string")
    replace_array(analysis, "MunsellColour", [""] * len(layers), "string")
    replace_array(analysis, "PH", [float(layer["PH"]) for layer in layers])

    replace_array(nitrogen, "Thickness", thickness)
    replace_array(nitrogen, "NO3", [float(layer["NO3N"]) for layer in layers])
    replace_array(nitrogen, "NH4", [float(layer["NH4N"]) for layer in layers])


def prepare_case(case: pd.Series) -> tuple[Path, Path, Path]:
    case_id = str(case.case_id)
    case_dir = CASE_ROOT / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    model_path = case_dir / f"{case_id}.apsim"
    # APSIM Classic's report component normalises the separator before the
    # report name to a space, so use that canonical filename explicitly.
    harvest_path = case_dir / f"{case_id} Harvest.out"
    phases_path = case_dir / f"{case_id} Phases.out"

    tree = ET.parse(TEMPLATE)
    root = tree.getroot()
    simulation = root.find("simulation")
    if simulation is None:
        raise KeyError("Template has no simulation")
    simulation.set("name", case_id)
    met_filename = simulation.find("metfile/filename")
    if met_filename is None:
        raise KeyError("Template has no metfile/filename")
    met_filename.text = str((ROOT / case.weather_file_path).resolve())
    start = simulation.find("clock/start_date")
    end = simulation.find("clock/end_date")
    if start is None or end is None:
        raise KeyError("Template has no clock dates")
    start.text = "01/10/2019"
    end.text = "30/12/2020"

    set_soil(
        root,
        (ROOT / case.soil_profile_path).resolve(),
        float(case.input_longitude),
        float(case.input_latitude),
        int(case.hwsd_soil_unit),
    )
    harvest = simulation.find(".//outputfile[@name='Harvest']/filename")
    phases = simulation.find(".//outputfile[@name='Phases']/filename")
    if harvest is None or phases is None:
        raise KeyError("Template Harvest/Phases output files not found")
    harvest.text = harvest_path.name
    phases.text = phases_path.name
    for output in simulation.findall(".//outputfile"):
        title = output.find("title")
        if title is not None:
            title.text = f"{case_id} {output.get('name', '')}".strip()
    ET.indent(tree, space="  ")
    tree.write(model_path, encoding="utf-8", xml_declaration=False)
    return model_path, harvest_path, phases_path


def read_apsim_output(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_index = next((i for i, line in enumerate(lines) if line.strip().startswith("Date")), None)
    if header_index is None:
        raise ValueError(f"Cannot locate APSIM output header: {path}")
    frame = pd.read_csv(path, sep=r"\s+", skiprows=header_index)
    if not frame.empty and str(frame.iloc[0, 0]).startswith("("):
        frame = frame.iloc[1:].reset_index(drop=True)
    return frame


def first_matching_column(frame: pd.DataFrame, suffixes: tuple[str, ...]) -> str | None:
    for column in frame.columns:
        key = str(column).lower()
        if any(key.endswith(suffix.lower()) for suffix in suffixes):
            return str(column)
    return None


def numeric_sum(frame: pd.DataFrame, suffixes: tuple[str, ...]) -> float:
    column = first_matching_column(frame, suffixes)
    if column is None:
        return float("nan")
    return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())


def numeric_max(frame: pd.DataFrame, suffixes: tuple[str, ...]) -> float:
    column = first_matching_column(frame, suffixes)
    if column is None:
        return float("nan")
    values = pd.to_numeric(frame[column], errors="coerce")
    return float(values.max()) if values.notna().any() else float("nan")


def collect_case_result(case_id: str, harvest_path: Path, phases_path: Path) -> dict[str, float | str]:
    harvest = read_apsim_output(harvest_path)
    phases = read_apsim_output(phases_path)
    if "Date" in phases.columns:
        phases = phases.drop_duplicates("Date", keep="last")
    result: dict[str, float | str] = {
        "case_id": case_id,
        "wheat_yield_kg_ha": numeric_sum(harvest, ("wheat.yield", "wheatyield")),
        "maize_yield_kg_ha": numeric_sum(harvest, ("maize.yield", "maizeyield")),
        "wheat_biomass_kg_ha": numeric_sum(harvest, ("wheat.biomass", "wheatbiomass")),
        "maize_biomass_kg_ha": numeric_sum(harvest, ("maize.biomass", "maizebiomass")),
        "rainfall_mm": numeric_sum(phases, ("rainfall",)),
        "surface_runoff_mm": numeric_sum(phases, ("surfacerunoff",)),
        "irrigation_mm": numeric_max(phases, ("irrigationtotal",)),
        "final_soil_water_mm": numeric_max(phases.tail(1), ("soilwater",)),
    }
    return result


def aggregate_results(units: pd.DataFrame, case_results: pd.DataFrame) -> None:
    mapped = units.merge(case_results, on="case_id", how="left", validate="many_to_one")
    if mapped.wheat_yield_kg_ha.isna().any():
        missing = mapped.loc[mapped.wheat_yield_kg_ha.isna(), "case_id"].unique().tolist()
        raise RuntimeError(f"Missing APSIM results for cases: {missing}")
    mapped["wheat_production_t"] = mapped.wheat_yield_kg_ha * mapped.rotation_area_ha / 1000.0
    mapped["maize_production_t"] = mapped.maize_yield_kg_ha * mapped.rotation_area_ha / 1000.0
    mapped.to_csv(RUN_ROOT / "grid_cell_results.csv", index=False, encoding="utf-8-sig")

    rows = []
    for resolution, group in mapped.groupby("resolution_m", sort=True):
        area = float(group.rotation_area_ha.sum())
        rows.append({
            "resolution_m": int(resolution),
            "simulation_units": len(group),
            "represented_rotation_area_ha": area,
            "wheat_area_weighted_yield_kg_ha": float(group.wheat_production_t.sum() * 1000.0 / area),
            "maize_area_weighted_yield_kg_ha": float(group.maize_production_t.sum() * 1000.0 / area),
            "wheat_production_t": float(group.wheat_production_t.sum()),
            "maize_production_t": float(group.maize_production_t.sum()),
            "rainfall_area_weighted_mm": float(np.average(group.rainfall_mm, weights=group.rotation_area_ha)),
            "runoff_area_weighted_mm": float(np.average(group.surface_runoff_mm, weights=group.rotation_area_ha)),
            "irrigation_area_weighted_mm": float(np.average(group.irrigation_mm, weights=group.rotation_area_ha)),
            "soil_water_area_weighted_mm": float(np.average(group.final_soil_water_mm, weights=group.rotation_area_ha)),
            "unique_soil_units": int(group.hwsd_soil_unit.nunique()),
            "unique_weather_cells": int(group.weather_grid_id.nunique()),
            "unique_cases": int(group.case_id.nunique()),
        })
    summary = pd.DataFrame(rows)
    baseline = summary.loc[summary.resolution_m == summary.resolution_m.min()].iloc[0]
    for variable in ("wheat_area_weighted_yield_kg_ha", "maize_area_weighted_yield_kg_ha", "wheat_production_t", "maize_production_t"):
        summary[f"{variable}_relative_to_finest_pct"] = (summary[variable] / float(baseline[variable]) - 1.0) * 100.0
    summary.to_csv(RUN_ROOT / "resolution_sensitivity_summary.csv", index=False, encoding="utf-8-sig")


def main(args: argparse.Namespace) -> None:
    for path in (UNITS_CSV, TEMPLATE, args.apsim_exe):
        if not path.exists():
            raise FileNotFoundError(path)
    RUN_ROOT.mkdir(parents=True, exist_ok=True)
    units = pd.read_csv(UNITS_CSV)
    cases = units.drop_duplicates("case_id").sort_values("case_id").reset_index(drop=True)
    if args.limit is not None:
        cases = cases.head(args.limit)

    status_rows: list[dict] = []
    result_rows: list[dict] = []
    for index, case in cases.iterrows():
        model_path, harvest_path, phases_path = prepare_case(case)
        started = time.perf_counter()
        status = "prepared"
        return_code: int | None = None
        if not args.prepare_only:
            if args.force or not (harvest_path.exists() and phases_path.exists()):
                process = subprocess.run(
                    [str(args.apsim_exe), model_path.name],
                    cwd=model_path.parent,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    check=False,
                    timeout=args.timeout_seconds,
                )
                return_code = process.returncode
                (model_path.parent / "apsim_stdout.log").write_text(process.stdout, encoding="utf-8")
                (model_path.parent / "apsim_stderr.log").write_text(process.stderr, encoding="utf-8")
            else:
                return_code = 0
            if return_code == 0 and harvest_path.exists() and phases_path.exists():
                status = "success"
                result_rows.append(collect_case_result(str(case.case_id), harvest_path, phases_path))
            else:
                status = "failed"
        status_rows.append({
            "case_id": case.case_id,
            "status": status,
            "return_code": return_code,
            "elapsed_seconds": time.perf_counter() - started,
            "model_path": str(model_path.relative_to(ROOT)).replace("\\", "/"),
        })
        print(f"[{index + 1}/{len(cases)}] {case.case_id}: {status}")

    status_frame = pd.DataFrame(status_rows)
    status_frame.to_csv(RUN_ROOT / "case_run_status.csv", index=False, encoding="utf-8-sig")
    if not args.prepare_only:
        results = pd.DataFrame(result_rows)
        results.to_csv(RUN_ROOT / "unique_case_results.csv", index=False, encoding="utf-8-sig")
        if len(cases) == units.case_id.nunique() and (status_frame.status == "success").all():
            aggregate_results(units, results)
        if (status_frame.status == "failed").any():
            raise RuntimeError(f"{int((status_frame.status == 'failed').sum())} APSIM cases failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apsim-exe", type=Path, default=DEFAULT_APSIM_EXE)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
