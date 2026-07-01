"""Generate APSIM Classic files for each system-level Morris sample."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pandas as pd

from system_common import (
    APS_RUN_DIR,
    BASELINE_DIR,
    BASE_APSIM,
    MORRIS_SAMPLES_WIDE_CSV,
    PARAMETER_RANGES_CSV,
    PARAM_TRACE_CSV,
    SIM_INDEX_CSV,
    ensure_dirs,
    json_dumps_compact,
    load_included_ranges,
    modify_apsim_for_system_sample,
    target_crop_rows,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate .apsim files for system-level Morris samples.")
    parser.add_argument("--apsim", type=Path, default=BASE_APSIM, help="Calibrated baseline .apsim file.")
    parser.add_argument("--samples", type=Path, default=MORRIS_SAMPLES_WIDE_CSV, help="Morris wide sample CSV.")
    parser.add_argument("--ranges", type=Path, default=PARAMETER_RANGES_CSV, help="System parameter range CSV.")
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    args = parse_args()
    if not args.apsim.exists():
        raise FileNotFoundError(f"Missing calibrated baseline .apsim: {args.apsim}")
    samples = pd.read_csv(args.samples)
    ranges = load_included_ranges(args.ranges)
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
        trace = modify_apsim_for_system_sample(args.apsim, apsim_file, parameter_rows, values, sid)
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


if __name__ == "__main__":
    main()
