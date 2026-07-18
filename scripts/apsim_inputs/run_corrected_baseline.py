"""Run the corrected 5 km Qihe baseline with explicit weather, soil and management."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_grid_resolution_experiment import (  # noqa: E402
    numeric_max,
    numeric_sum,
    read_apsim_output,
    set_soil,
)
from fertilizer_management import entry_amount_kg_ha, validate_scenario_n_budgets  # noqa: E402


PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
UNITS_CSV = PILOT / "corrected_baseline" / "corrected_baseline_units_5km.csv"
CONFIG = ROOT / "configs" / "spatial" / "qihe_2020_management_scenarios.json"
TEMPLATE = ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"
OUTPUT_ROOT = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "corrected_baseline"
DEFAULT_APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")
START_YEAR = 2017
END_YEAR = 2020


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def set_ui(manager: ET.Element, tag: str, value: str | float) -> None:
    node = manager.find(f"ui/{tag}")
    if node is None:
        raise KeyError(f"Manager {manager.get('name')} has no ui/{tag}")
    node.text = str(value)


def manager(root: ET.Element, name: str) -> ET.Element:
    node = root.find(f".//manager2[@name='{name}']")
    if node is None:
        raise KeyError(f"Template has no manager2 named {name}")
    return node


def dated_events(crop: str, entries: list[dict], total_n_kg_ha: float | None = None) -> list[tuple[str, float]]:
    events: list[tuple[str, float]] = []
    for year in range(START_YEAR, END_YEAR + 1):
        for entry in entries:
            month, day = (int(part) for part in entry["month_day"].split("-"))
            event_year = year
            # Wheat is sown in autumn and top-dressed/irrigated in the following year.
            if crop == "wheat" and month <= 6:
                event_year = year + 1
            if START_YEAR <= event_year <= END_YEAR:
                if "amount_mm" in entry:
                    amount = float(entry["amount_mm"])
                elif total_n_kg_ha is not None:
                    amount = entry_amount_kg_ha(entry, total_n_kg_ha)
                else:
                    raise ValueError("Nitrogen entries require total_n_kg_ha")
                events.append((date(event_year, month, day).isoformat(), amount))
    return events


def scheduled_manager(name: str, events: list[tuple[str, float]], kind: str) -> ET.Element:
    dates = ";".join(item[0] for item in events)
    amounts = ";".join(f"{item[1]:.6g}" for item in events)
    if kind == "fertiliser":
        action = 'Fertiliser.Apply((float)amount, 50, "urea_N");'
        link = "[Link] Fertiliser Fertiliser;"
    elif kind == "irrigation":
        action = "IrrigationApplicationType data = new IrrigationApplicationType();\n         data.Amount = (int)Math.Round(amount);\n         Irrigation.Set(\"irrigation_efficiency\", 1.0);\n         Irrigation.Apply(data);"
        link = "[Link] Irrigation Irrigation;"
    else:
        raise ValueError(kind)
    xml = f"""<manager2 name="{name}">
  <ui>
    <event_dates type="text">{dates}</event_dates>
    <event_amounts type="text">{amounts}</event_amounts>
  </ui>
  <text>using System;
using System.Globalization;
using ModelFramework;

public class Script
{{
   {link}
   [Input] private DateTime today;
   [Param] private string event_dates;
   [Param] private string event_amounts;

   [EventHandler] public void OnPrepare()
   {{
      string[] dates = (event_dates ?? "").Split(';');
      string[] amounts = (event_amounts ?? "").Split(';');
      for (int i = 0; i &lt; dates.Length; i++)
      {{
         DateTime eventDate;
         if (!DateTime.TryParseExact(dates[i].Trim(), "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out eventDate))
            continue;
         if (today.Date != eventDate.Date)
            continue;
         double amount = 0.0;
         if (i &lt; amounts.Length)
            Double.TryParse(amounts[i].Trim(), NumberStyles.Any, CultureInfo.InvariantCulture, out amount);
         if (amount &lt;= 0.0)
            continue;
         {action}
      }}
   }}
}}
  </text>
</manager2>"""
    return ET.fromstring(xml)


def set_force_harvest(maize_manager: ET.Element, month_day: str) -> None:
    ui = maize_manager.find("ui")
    text = maize_manager.find("text")
    if ui is None or text is None or text.text is None:
        raise KeyError("Invalid Maize Management manager")
    force = ET.SubElement(ui, "force_harvest_date", {"type": "text", "description": "Forced maize harvest date"})
    month, day = month_day.split("-")
    force.text = f"{int(day)}-{date(2000, int(month), 1).strftime('%b')}"
    marker = "[Param] private string row_spacing1;"
    if marker not in text.text:
        # APSIM templates differ in the declaration order; insert before the first event handler.
        marker = "[EventHandler]"
        declaration = "[Param] private string force_harvest_date;\n   [EventHandler]"
    else:
        declaration = marker + "\n   [Param] private string force_harvest_date;"
    text.text = text.text.replace(marker, declaration, 1)
    marker = 'if (plantStatus == "out")\n            return -1;'
    replacement = marker + "\n\n         if (DateUtility.DatesEqual(force_harvest_date, today))\n            return 1;"
    if marker not in text.text:
        raise ValueError("Cannot inject forced maize harvest into canLeave")
    text.text = text.text.replace(marker, replacement, 1)


def configure_management(root: ET.Element, scenario: dict) -> None:
    validate_scenario_n_budgets(scenario)
    mappings = (("wheat", "Wheat Management"), ("maize", "Maize Management"))
    for crop, name in mappings:
        values = scenario[crop]
        node = manager(root, name)
        set_ui(node, "date1", values["sowing_start"])
        set_ui(node, "date2", values["sowing_end"])
        set_ui(node, "density1", values["density_plants_m2"])
        set_ui(node, "depth1", values["depth_mm"])
        set_ui(node, "row_spacing1", values["row_spacing_mm"])
        # Use deterministic windows for the baseline; weather still controls growth after sowing.
        set_ui(node, "rain_amount", 0)
        set_ui(node, "esw_amount", 0)

    wheat_fert = manager(root, "Wheat Sowing Fertiliser")
    maize_fert = manager(root, "Maize Sowing Fertiliser1")
    set_ui(wheat_fert, "fertAmt", scenario["wheat"]["sowing_n_kg_ha"])
    set_ui(maize_fert, "fertAmt", scenario["maize"]["sowing_n_kg_ha"])

    folder = root.find(".//folder[@name='Manager folder']")
    if folder is None:
        raise KeyError("Template has no Manager folder")
    obsolete = folder.find("manager2[@name='maize Fertilise on fixed date']")
    if obsolete is not None:
        folder.remove(obsolete)
    fertiliser_events = dated_events(
        "wheat", scenario["wheat"]["topdress_n"], float(scenario["wheat"]["total_n_kg_ha"])
    )
    fertiliser_events += dated_events(
        "maize", scenario["maize"]["topdress_n"], float(scenario["maize"]["total_n_kg_ha"])
    )
    irrigation_events = dated_events("wheat", scenario["wheat"]["irrigation"])
    irrigation_events += dated_events("maize", scenario["maize"]["irrigation"])
    folder.append(scheduled_manager("Corrected baseline fertiliser schedule", sorted(fertiliser_events), "fertiliser"))
    folder.append(scheduled_manager("Corrected baseline irrigation schedule", sorted(irrigation_events), "irrigation"))
    irrigation = root.find(".//irrigation/automatic_irrigation")
    if irrigation is None:
        raise KeyError("Template has no automatic_irrigation setting")
    irrigation.text = "off"
    set_force_harvest(manager(root, "Maize Management"), scenario["maize"]["harvest_month_day"])


def prepare_case(case: pd.Series, scenario_name: str, scenario: dict, output_root: Path = OUTPUT_ROOT) -> tuple[Path, Path, Path]:
    case_id = str(case.case_id)
    case_dir = output_root / scenario_name / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    model_path = case_dir / f"{case_id}.apsim"
    harvest_path = case_dir / f"{case_id} Harvest.out"
    phases_path = case_dir / f"{case_id} Phases.out"
    tree = ET.parse(TEMPLATE)
    root = tree.getroot()
    simulation = root.find("simulation")
    if simulation is None:
        raise KeyError("Template has no simulation")
    simulation.set("name", case_id)
    simulation.find("metfile/filename").text = str((ROOT / case.weather_file_path).resolve())
    simulation.find("clock/start_date").text = "01/10/2017"
    simulation.find("clock/end_date").text = "30/12/2020"
    set_soil(root, ROOT / case.soil_profile_path, float(case.input_longitude), float(case.input_latitude), int(case.hwsd_soil_unit))
    soil = root.find(".//Soil")
    if soil is not None:
        soil.find("Site").text = "Qihe County corrected 2020 baseline"
        resolution_km = int(case.resolution_m) // 1000
        soil.find("LocationAccuracy").text = f"HWSD unit area split within each {resolution_km} km rotation grid cell"
    configure_management(root, scenario)
    simulation.find(".//outputfile[@name='Harvest']/filename").text = harvest_path.name
    simulation.find(".//outputfile[@name='Phases']/filename").text = phases_path.name
    for output in simulation.findall(".//outputfile"):
        title = output.find("title")
        if title is not None:
            title.text = f"{case_id} {output.get('name', '')}".strip()
    ET.indent(tree, space="  ")
    tree.write(model_path, encoding="utf-8", xml_declaration=False)
    return model_path, harvest_path, phases_path


def collect_target_result(case_id: str, harvest_path: Path, phases_path: Path) -> dict[str, float | str]:
    """Collect only the 2019-2020 target season, excluding the two-year spin-up."""
    harvest = read_apsim_output(harvest_path)
    phases = read_apsim_output(phases_path)
    cutoff = pd.Timestamp("2019-10-01")
    for frame in (harvest, phases):
        parsed = pd.to_datetime(frame["Date"], format="%d/%m/%Y", errors="coerce")
        frame.drop(frame.index[parsed < cutoff], inplace=True)
    phases = phases.drop_duplicates("Date", keep="last")
    return {
        "case_id": case_id,
        "wheat_yield_kg_ha": numeric_sum(harvest, ("wheat.yield", "wheatyield")),
        "maize_yield_kg_ha": numeric_sum(harvest, ("maize.yield", "maizeyield")),
        "wheat_biomass_kg_ha": numeric_sum(harvest, ("wheat.biomass", "wheatbiomass")),
        "maize_biomass_kg_ha": numeric_sum(harvest, ("maize.biomass", "maizebiomass")),
        "rainfall_mm": numeric_sum(phases, ("rainfall",)),
        "surface_runoff_mm": numeric_sum(phases, ("surfacerunoff",)),
        # IrrigationTotal is cumulative from the 2017 simulation start; sum the
        # daily target-season applications instead.
        "irrigation_mm": numeric_sum(phases, ("irrigationapplied",)),
        "final_soil_water_mm": numeric_max(phases.tail(1), ("soilwater",)),
    }


def aggregate(units: pd.DataFrame, results: pd.DataFrame, outdir: Path) -> None:
    mapped = units.merge(results, on="case_id", how="left", validate="many_to_one")
    if mapped.wheat_yield_kg_ha.isna().any():
        raise RuntimeError("Some soil subunits have no APSIM result")
    area = mapped.soil_rotation_area_ha
    for crop in ("wheat", "maize"):
        mapped[f"{crop}_production_t"] = mapped[f"{crop}_yield_kg_ha"] * area / 1000.0
    mapped.to_csv(outdir / "soil_subunit_results.csv", index=False, encoding="utf-8-sig")
    summary = {
        "soil_subunits": len(mapped),
        "unique_cases": int(mapped.case_id.nunique()),
        "rotation_area_ha": float(area.sum()),
        "wheat_area_weighted_yield_kg_ha": float(np.average(mapped.wheat_yield_kg_ha, weights=area)),
        "maize_area_weighted_yield_kg_ha": float(np.average(mapped.maize_yield_kg_ha, weights=area)),
        "wheat_production_t": float(mapped.wheat_production_t.sum()),
        "maize_production_t": float(mapped.maize_production_t.sum()),
        "irrigation_area_weighted_mm": float(np.average(mapped.irrigation_mm, weights=area)),
    }
    (outdir / "baseline_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main(args: argparse.Namespace) -> None:
    run_started = time.perf_counter()
    started_at = datetime.now(timezone.utc).astimezone().isoformat()
    units_csv = PILOT / "corrected_baseline" / f"corrected_baseline_units_{args.resolution_m // 1000}km.csv"
    output_root = args.output_root.resolve() if args.output_root else (
        OUTPUT_ROOT if args.resolution_m == 5000 else OUTPUT_ROOT / f"resolution_{args.resolution_m // 1000}km"
    )
    for path in (units_csv, CONFIG, TEMPLATE, args.apsim_exe):
        if not path.exists():
            raise FileNotFoundError(path)
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    scenario_name = args.scenario or settings["default_scenario"]
    scenario = settings["scenarios"][scenario_name]
    units = pd.read_csv(units_csv)
    cases = units.drop_duplicates("case_id").sort_values("case_id").reset_index(drop=True)
    if args.limit is not None:
        cases = cases.head(args.limit)
    outdir = output_root / scenario_name
    outdir.mkdir(parents=True, exist_ok=True)
    metadata_path = outdir / "run_metadata.json"
    metadata = {
        "status": "running", "started_at": started_at,
        "command": [sys.executable, *sys.argv], "resolution_m": args.resolution_m,
        "scenario": scenario_name, "output_root": str(output_root),
        "total_unique_cases": int(len(cases)),
        "input_paths": {
            "units": str(units_csv.resolve()), "management_config": str(CONFIG.resolve()),
            "apsim_template": str(TEMPLATE.resolve()), "apsim_executable": str(args.apsim_exe.resolve()),
        },
        "sha256": {
            "units": sha256(units_csv), "management_config": sha256(CONFIG),
            "apsim_template": sha256(TEMPLATE), "runner_script": sha256(Path(__file__)),
            "apsim_executable": sha256(args.apsim_exe),
        },
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    (outdir / "management_scenario.json").write_text(
        json.dumps({"scenario": scenario_name, **scenario}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    statuses, results = [], []
    for index, case in cases.iterrows():
        model_path, harvest_path, phases_path = prepare_case(case, scenario_name, scenario, output_root)
        started = time.perf_counter()
        return_code = None
        status = "prepared"
        if not args.prepare_only:
            for stale_output in (harvest_path, phases_path):
                if stale_output.exists():
                    stale_output.unlink()
            process = subprocess.run([str(args.apsim_exe), model_path.name], cwd=model_path.parent, capture_output=True, text=True, errors="replace", timeout=args.timeout_seconds)
            return_code = process.returncode
            (model_path.parent / "apsim_stdout.log").write_text(process.stdout, encoding="utf-8")
            (model_path.parent / "apsim_stderr.log").write_text(process.stderr, encoding="utf-8")
            if return_code == 0 and harvest_path.exists() and phases_path.exists():
                status = "success"
                results.append(collect_target_result(case.case_id, harvest_path, phases_path))
            else:
                status = "failed"
        statuses.append({"case_id": case.case_id, "status": status, "return_code": return_code, "elapsed_seconds": time.perf_counter() - started, "model_path": str(model_path.relative_to(ROOT)).replace("\\", "/")})
        print(f"[{index + 1}/{len(cases)}] {case.case_id}: {status}")
    status_frame = pd.DataFrame(statuses)
    status_frame.to_csv(outdir / "case_run_status.csv", index=False, encoding="utf-8-sig")
    if not args.prepare_only:
        result_frame = pd.DataFrame(results)
        result_frame.to_csv(outdir / "unique_case_results.csv", index=False, encoding="utf-8-sig")
        if len(cases) == units.case_id.nunique() and (status_frame.status == "success").all():
            aggregate(units, result_frame, outdir)
        if (status_frame.status == "failed").any():
            raise RuntimeError(f"{int((status_frame.status == 'failed').sum())} APSIM cases failed")
    metadata.update({
        "status": "prepared" if args.prepare_only else "success",
        "finished_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "elapsed_seconds": time.perf_counter() - run_started,
        "successful_cases": int((status_frame.status == "success").sum()),
        "failed_cases": int((status_frame.status == "failed").sum()),
        "prepared_cases": int((status_frame.status == "prepared").sum()),
    })
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apsim-exe", type=Path, default=DEFAULT_APSIM_EXE)
    parser.add_argument("--scenario", choices=("ordinary_farmer", "standard_high_yield", "demonstration_high_yield"))
    parser.add_argument("--resolution-m", type=int, choices=(1000, 2000, 5000, 10000), default=5000)
    parser.add_argument("--output-root", type=Path,
                        help="Optional new run root; use this to avoid overwriting an earlier baseline.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    return parser.parse_args()


if __name__ == "__main__":
    main(parse_args())
