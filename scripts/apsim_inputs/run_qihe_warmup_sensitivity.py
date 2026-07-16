"""Run continuous 10 km Qihe warm-up and initial-N sensitivity experiments."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_grid_resolution_experiment import read_apsim_output, replace_array, set_soil  # noqa: E402
from run_corrected_baseline import manager, scheduled_manager, set_force_harvest, set_ui  # noqa: E402


PILOT = ROOT / "data" / "processed" / "spatial" / "county_pilot_2020"
CONFIG = ROOT / "configs" / "spatial" / "qihe_warmup_sensitivity_scenarios.json"
BASE_MANAGEMENT = ROOT / "configs" / "spatial" / "qihe_2020_management_scenarios.json"
TEMPLATE = ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"
DEFAULT_APSIM_EXE = Path(r"F:\APSIM710-r4221\Model\Apsim.exe")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def annual_rates(config: dict, scenario_name: str) -> dict[int, float]:
    scenario = config["management_scenarios"][scenario_name]
    if "multiplier_of" in scenario:
        base = annual_rates(config, scenario["multiplier_of"])
        return {year: rate * float(scenario["multiplier"]) for year, rate in base.items()}
    values = scenario["annual_total_n_kg_ha"]
    if "default" in values:
        start = datetime.fromisoformat(config["simulation_start"]).year
        end = datetime.fromisoformat(config["simulation_end"]).year
        return {year: float(values["default"]) for year in range(start, end + 1)}
    return {int(year): float(rate) for year, rate in values.items()}


def scale_initial_n(root: ET.Element, multiplier: float) -> None:
    sample = root.find(".//Soil/Sample[@name='InitialNitrogen']")
    if sample is None:
        raise KeyError("InitialNitrogen sample is missing")
    for tag in ("NO3", "NH4"):
        node = sample.find(tag)
        if node is None:
            raise KeyError(f"InitialNitrogen/{tag} is missing")
        values = [float(child.text) * multiplier for child in node]
        replace_array(sample, tag, values)


def full_year_events(config: dict, rates: dict[int, float]) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    common = config["common_management"]
    fertiliser: list[tuple[str, float]] = []
    irrigation: list[tuple[str, float]] = []
    start = datetime.fromisoformat(config["simulation_start"]).date()
    end = datetime.fromisoformat(config["simulation_end"]).date()
    for harvest_year, total in sorted(rates.items()):
        # Wheat N is indexed by harvest/statistical year; basal N is applied in the preceding autumn.
        wheat_sow_year = harvest_year - 1
        wm, wd = map(int, common["wheat_sowing_month_day"].split("-"))
        tm, td = map(int, common["wheat_topdress_month_day"].split("-"))
        mm, md = map(int, common["maize_sowing_month_day"].split("-"))
        mtm, mtd = map(int, common["maize_topdress_month_day"].split("-"))
        candidates = [
            (date(wheat_sow_year, wm, wd), total * float(common["wheat_sowing_n_fraction"])),
            (date(harvest_year, tm, td), total * float(common["wheat_topdress_n_fraction"])),
            (date(harvest_year, mm, md), total * float(common["maize_sowing_n_fraction"])),
            (date(harvest_year, mtm, mtd), total * float(common["maize_topdress_n_fraction"])),
        ]
        fertiliser.extend((day.isoformat(), amount) for day, amount in candidates if start <= day <= end)
        for month, day, amount in ((3, 20, 75.0), (4, 20, 75.0), (6, 16, 60.0)):
            event = date(harvest_year, month, day)
            if start <= event <= end:
                irrigation.append((event.isoformat(), amount))
    return sorted(fertiliser), sorted(irrigation)


def configure_management(root: ET.Element, config: dict, rates: dict[int, float]) -> None:
    baseline = json.loads(BASE_MANAGEMENT.read_text(encoding="utf-8"))["scenarios"]["ordinary_farmer"]
    for crop, name in (("wheat", "Wheat Management"), ("maize", "Maize Management")):
        values = baseline[crop]
        node = manager(root, name)
        for tag, value in (
            ("date1", values["sowing_start"]), ("date2", values["sowing_end"]),
            ("density1", values["density_plants_m2"]), ("depth1", values["depth_mm"]),
            ("row_spacing1", values["row_spacing_mm"]), ("rain_amount", 0), ("esw_amount", 0),
        ):
            set_ui(node, tag, value)
    set_ui(manager(root, "Wheat Sowing Fertiliser"), "fertAmt", 0)
    set_ui(manager(root, "Maize Sowing Fertiliser1"), "fertAmt", 0)
    folder = root.find(".//folder[@name='Manager folder']")
    if folder is None:
        raise KeyError("Manager folder is missing")
    for name in ("maize Fertilise on fixed date", "Corrected baseline fertiliser schedule", "Corrected baseline irrigation schedule"):
        node = folder.find(f"manager2[@name='{name}']")
        if node is not None:
            folder.remove(node)
    fertiliser, irrigation = full_year_events(config, rates)
    folder.append(scheduled_manager("Warmup annual fertiliser schedule", fertiliser, "fertiliser"))
    folder.append(scheduled_manager("Warmup irrigation schedule", irrigation, "irrigation"))
    automatic = root.find(".//irrigation/automatic_irrigation")
    if automatic is None:
        raise KeyError("automatic_irrigation is missing")
    automatic.text = "off"
    set_force_harvest(manager(root, "Maize Management"), baseline["maize"]["harvest_month_day"])


def add_n_outputs(root: ET.Element) -> None:
    variables = root.find(".//outputfile[@name='Phases']/variables")
    if variables is None:
        raise KeyError("Phases variables are missing")
    existing = {node.text for node in variables.findall("variable")}
    for text in ("no3() as NO3Total", "nh4() as NH4Total", "dlt_n_min() as NetNMineralisation"):
        if text not in existing:
            ET.SubElement(variables, "variable").text = text


def prepare_case(case: pd.Series, config: dict, scenario_name: str, multiplier: float, run_root: Path) -> tuple[Path, Path, Path]:
    combo = f"{scenario_name}__initialN_{multiplier:g}x"
    case_id = str(case.case_id)
    case_dir = run_root / combo / "cases" / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    model = case_dir / f"{case_id}.apsim"
    harvest = case_dir / f"{case_id} Harvest.out"
    phases = case_dir / f"{case_id} Phases.out"
    root = ET.parse(TEMPLATE).getroot()
    simulation = root.find("simulation")
    if simulation is None:
        raise KeyError("simulation is missing")
    simulation.set("name", case_id)
    simulation.find("metfile/filename").text = str((ROOT / case.weather_file_path).resolve())
    simulation.find("clock/start_date").text = datetime.fromisoformat(config["simulation_start"]).strftime("%d/%m/%Y")
    simulation.find("clock/end_date").text = datetime.fromisoformat(config["simulation_end"]).strftime("%d/%m/%Y")
    set_soil(root, ROOT / case.soil_profile_path, float(case.input_longitude), float(case.input_latitude), int(case.hwsd_soil_unit))
    scale_initial_n(root, multiplier)
    configure_management(root, config, annual_rates(config, scenario_name))
    add_n_outputs(root)
    simulation.find(".//outputfile[@name='Harvest']/filename").text = harvest.name
    simulation.find(".//outputfile[@name='Phases']/filename").text = phases.name
    ET.indent(root, space="  ")
    ET.ElementTree(root).write(model, encoding="utf-8", xml_declaration=False)
    return model, harvest, phases


def annual_results(case_id: str, scenario: str, multiplier: float, harvest_path: Path, phases_path: Path) -> list[dict]:
    harvest = read_apsim_output(harvest_path)
    phases = read_apsim_output(phases_path)
    harvest["Date"] = pd.to_datetime(harvest.Date, format="%d/%m/%Y", errors="coerce")
    phases["Date"] = pd.to_datetime(phases.Date, format="%d/%m/%Y", errors="coerce")
    phases = phases.drop_duplicates("Date", keep="last")
    rows = []
    for year in range(2011, 2024):
        annual_h = harvest.loc[harvest.Date.dt.year == year]
        presow = phases.loc[(phases.Date.dt.year == year) & (phases.Date.dt.month == 10) & (phases.Date.dt.day == 1)]
        if presow.empty:
            presow = phases.loc[phases.Date.dt.year == year].tail(1)
        def total(frame: pd.DataFrame, suffix: str) -> float:
            cols = [c for c in frame if str(c).lower().endswith(suffix.lower())]
            return float(pd.to_numeric(frame[cols[0]], errors="coerce").fillna(0).sum()) if len(cols) == 1 else float("nan")
        rows.append({
            "case_id": case_id, "management_scenario": scenario, "initial_n_multiplier": multiplier, "year": year,
            "wheat_yield_kg_ha": total(annual_h, "wheat.yield"), "maize_yield_kg_ha": total(annual_h, "maize.yield"),
            "oct1_no3_kg_ha": total(presow.tail(1), "NO3Total"), "oct1_nh4_kg_ha": total(presow.tail(1), "NH4Total"),
        })
    return rows


def main() -> None:
    run_started_at = datetime.now().astimezone()
    run_started_clock = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="qihe_warmup_2010_2023_v1")
    parser.add_argument(
        "--data-run-id",
        help="Prepared weather/input run ID; defaults to --run-id so pilot outputs can remain separate.",
    )
    parser.add_argument("--apsim-exe", type=Path, default=DEFAULT_APSIM_EXE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--force", action="store_true", help="Explicitly allow reuse/overwrite within this run ID.")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--workers", type=int, default=1, help="Concurrent APSIM processes (default: 1).")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    data_run_id = args.data_run_id or args.run_id
    data_root = PILOT / "warmup_sensitivity" / data_run_id
    units_path = data_root / "simulation_units_10km.csv"
    run_root = ROOT / "outputs" / "spatial" / "county_pilot_2020" / "warmup_sensitivity" / args.run_id
    if not units_path.exists():
        raise FileNotFoundError(f"Prepare weather first: {units_path}")
    if run_root.exists() and any(run_root.iterdir()) and not args.force:
        raise FileExistsError(f"Run directory is nonempty; choose a new --run-id: {run_root}")
    run_root.mkdir(parents=True, exist_ok=True)
    units = pd.read_csv(units_path)
    cases = units.drop_duplicates("case_id").sort_values("case_id")
    if args.limit:
        cases = cases.head(args.limit)
    status_rows, annual_rows = [], []
    combinations = [
        (scenario, float(multiplier))
        for scenario in config["management_scenarios"]
        for multiplier in config["initial_mineral_n_multipliers"]
    ]
    tasks = [(scenario, multiplier, case) for scenario, multiplier in combinations for _, case in cases.iterrows()]

    def run_one(task: tuple[str, float, pd.Series]) -> tuple[dict, list[dict]]:
        scenario, multiplier, case = task
        try:
            model, harvest, phases = prepare_case(case, config, scenario, multiplier, run_root)
            started = time.perf_counter(); return_code = None; status = "prepared"
            if not args.prepare_only:
                if args.force or not (harvest.exists() and phases.exists()):
                    process = subprocess.run(
                        [str(args.apsim_exe), model.name], cwd=model.parent, capture_output=True, text=True,
                        errors="replace", timeout=args.timeout_seconds, check=False,
                    )
                    return_code = process.returncode
                    (model.parent / "apsim_stdout.log").write_text(process.stdout, encoding="utf-8")
                    (model.parent / "apsim_stderr.log").write_text(process.stderr, encoding="utf-8")
                else:
                    return_code = 0
                if return_code == 0 and harvest.exists() and phases.exists():
                    status = "success"
                else:
                    status = "failed"
            status_row = {
                "management_scenario": scenario, "initial_n_multiplier": multiplier, "case_id": case.case_id,
                "status": status, "return_code": return_code, "elapsed_seconds": time.perf_counter()-started,
                "model_path": str(model.relative_to(ROOT)).replace("\\", "/"),
            }
            rows = annual_results(str(case.case_id), scenario, multiplier, harvest, phases) if status == "success" else []
            return status_row, rows
        except Exception as exc:  # keep the batch running while preserving the case-level failure
            return ({
                "management_scenario": scenario, "initial_n_multiplier": multiplier, "case_id": case.case_id,
                "status": "failed_exception", "return_code": None, "elapsed_seconds": 0.0,
                "model_path": "", "error": f"{type(exc).__name__}: {exc}",
            }, [])

    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(run_one, task) for task in tasks]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            status_row, rows = future.result()
            status_rows.append(status_row)
            annual_rows.extend(rows)
            print(
                f"[{completed}/{len(tasks)}] {status_row['management_scenario']} "
                f"N0={status_row['initial_n_multiplier']:g} {status_row['case_id']}: {status_row['status']}"
            )
            if completed % 25 == 0:
                pd.DataFrame(status_rows).to_csv(run_root / "case_run_status.partial.csv", index=False, encoding="utf-8-sig")
                if annual_rows:
                    pd.DataFrame(annual_rows).to_csv(
                        run_root / "annual_case_yield_and_mineral_n.partial.csv", index=False, encoding="utf-8-sig"
                    )
    pd.DataFrame(status_rows).to_csv(run_root / "case_run_status.csv", index=False, encoding="utf-8-sig")
    if annual_rows:
        pd.DataFrame(annual_rows).to_csv(run_root / "annual_case_yield_and_mineral_n.csv", index=False, encoding="utf-8-sig")
    metadata = {
        "run_id": args.run_id, "data_run_id": data_run_id,
        "started_at": run_started_at.isoformat(), "finished_at": datetime.now().astimezone().isoformat(),
        "elapsed_seconds": time.perf_counter() - run_started_clock,
        "config": str(CONFIG.relative_to(ROOT)).replace("\\", "/"),
        "config_sha256": sha256(CONFIG), "apsim_exe": str(args.apsim_exe), "apsim_exe_sha256": sha256(args.apsim_exe),
        "apsim_release": "APSIM Classic 7.10 r4221",
        "template": str(TEMPLATE.relative_to(ROOT)).replace("\\", "/"), "template_sha256": sha256(TEMPLATE),
        "simulation_units": str(units_path.relative_to(ROOT)).replace("\\", "/"),
        "simulation_units_sha256": sha256(units_path),
        "combinations": len(combinations), "unique_cases_requested": len(cases), "prepare_only": args.prepare_only,
        "workers": args.workers,
        "case_status_counts": pd.Series([row["status"] for row in status_rows]).value_counts().to_dict(),
        "warning": "2010-2011 statistical-central rates are explicit 2012 spin-up fallbacks, not observed annual management.",
    }
    (run_root / "run_metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
