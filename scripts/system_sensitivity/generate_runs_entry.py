"""Shared APSIM sample generation entry point for Morris and Sobol phases."""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

from system_common import (
    APS_RUN_DIR,
    BASELINE_DIR,
    PARAM_TRACE_CSV,
    SIM_INDEX_CSV,
    ensure_dirs,
    json_dumps_compact,
    load_included_ranges,
    modify_apsim_for_system_sample,
    target_crop_rows,
)


def generate(apsim: Path, samples_path: Path, ranges_path: Path, include_col: str) -> None:
    ensure_dirs()
    if not apsim.exists():
        raise FileNotFoundError(f"Missing calibrated baseline .apsim: {apsim}")
    samples = pd.read_csv(samples_path)
    ranges = load_included_ranges(ranges_path, include_col=include_col)
    range_by_key = {}
    for _, row in ranges.iterrows():
        item = row.to_dict()
        range_by_key[item["parameter_key"]] = item
    parameter_rows = list(range_by_key.values())
    index_rows = []
    trace_rows = []
    override_root = APS_RUN_DIR / "model_overrides"
    override_root.mkdir(parents=True, exist_ok=True)
    for _, sample in samples.iterrows():
        sid = int(sample["sample_id"])
        tag = f"sample_{sid:06d}"
        apsim_file = APS_RUN_DIR / f"{tag}.apsim"
        values = {key: float(sample[key]) for key in range_by_key if key in sample.index}
        trace = modify_apsim_for_system_sample(apsim, apsim_file, parameter_rows, values, sid)
        output_files = trace[0]["output_file"] if trace else ""
        sample_override_dir = override_root / tag
        sample_override_dir.mkdir(parents=True, exist_ok=True)
        copied = []
        for crop_xml in ["Wheat.xml", "Maize.xml"]:
            src = BASELINE_DIR / crop_xml
            if src.exists():
                dst = sample_override_dir / crop_xml
                shutil.copy2(src, dst)
                copied.append(str(dst))
        trace_rows.extend(trace)
        trace_rows.extend(target_crop_rows(sid, apsim_file, output_files))
        status = "created" if all(row["status"] == "ok" for row in trace) else "failed_parameter_modification"
        index_rows.append(
            {
                "sample_id": sid,
                "apsim_file": str(apsim_file),
                "parameter_values": json_dumps_compact(values),
                "status": status,
                "model_override_dir": str(sample_override_dir),
                "output_files": output_files,
                "fixed_crop_xml": ";".join(copied),
            }
        )
    pd.DataFrame(index_rows).to_csv(SIM_INDEX_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(trace_rows).to_csv(PARAM_TRACE_CSV, index=False, encoding="utf-8-sig")
    print(f"Wrote {SIM_INDEX_CSV}")
    print(f"Wrote {PARAM_TRACE_CSV}")
