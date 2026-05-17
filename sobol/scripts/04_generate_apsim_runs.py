"""
04 为每个 Sobol 样本生成独立 .apsim 文件和对应 crop XML override。

说明：
APSIM Classic 的 cultivar 参数通常来自 Model/Wheat.xml、Model/Maize.xml。
因此每个 sample 会生成：
1. apsim_runs/sample_000001.apsim
2. apsim_runs/model_overrides/sample_000001/Wheat.xml 或 Maize.xml
3. apsim_runs/outputs/sample_000001/ 作为输出目录
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from sobol_common import (
    APS_RUN_DIR,
    BASE_APSIM,
    CROP_XML_FILES,
    PARAM_TRACE_CSV,
    RANGES_CSV,
    SAMPLES_WIDE_CSV,
    SIM_INDEX_CSV,
    backup_file,
    clean_text,
    ensure_dirs,
    json_dumps_compact,
    parse_xml,
    setup_logging,
    update_xml_parameter,
    write_xml,
)


def modify_output_filenames(apsim_in: Path, apsim_out: Path, sample_id: int) -> str:
    tree = parse_xml(apsim_in)
    out_dir = APS_RUN_DIR / "outputs" / f"sample_{sample_id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_files = []
    for node in tree.xpath(".//*[local-name()='outputfile']/*[local-name()='filename']"):
        old_name = clean_text(node.text) or f"sample_{sample_id:06d}.out"
        suffix = Path(old_name).name
        new_path = out_dir / suffix
        node.text = str(new_path)
        output_files.append(str(new_path))
    write_xml(tree, apsim_out)
    return ";".join(output_files)


def main() -> None:
    ensure_dirs()
    logger = setup_logging("04_generate_apsim_runs")
    if not BASE_APSIM.exists():
        raise FileNotFoundError(f"baseline .apsim 不存在: {BASE_APSIM}")
    if not SAMPLES_WIDE_CSV.exists():
        raise FileNotFoundError(f"请先运行 03_generate_sobol_samples.py: {SAMPLES_WIDE_CSV}")
    if not RANGES_CSV.exists():
        raise FileNotFoundError(f"缺少参数范围表: {RANGES_CSV}")

    backup = backup_file(BASE_APSIM, "baseline_apsim")
    logger.info("修改前已备份 baseline .apsim: %s", backup)
    for crop, xml_file in CROP_XML_FILES.items():
        if xml_file.exists():
            b = backup_file(xml_file, "baseline_model_xml")
            logger.info("已备份 baseline crop XML: %s -> %s", crop, b)

    samples = pd.read_csv(SAMPLES_WIDE_CSV)
    ranges = pd.read_csv(RANGES_CSV)
    ranges = ranges[ranges["include_in_sobol"].astype(str).str.upper() == "TRUE"].copy()
    range_by_key = ranges.set_index("parameter_key").to_dict(orient="index")

    index_rows = []
    trace_rows = []
    failures = []
    override_root = APS_RUN_DIR / "model_overrides"
    override_root.mkdir(parents=True, exist_ok=True)
    fail_file = APS_RUN_DIR / "parameter_modification_failures.csv"
    if fail_file.exists():
        fail_file.unlink()

    for _, sample in samples.iterrows():
        sid = int(sample["sample_id"])
        sample_tag = f"sample_{sid:06d}"
        apsim_file = APS_RUN_DIR / f"{sample_tag}.apsim"
        output_files = modify_output_filenames(BASE_APSIM, apsim_file, sid)

        sample_override_dir = override_root / sample_tag
        sample_override_dir.mkdir(parents=True, exist_ok=True)
        copied_crop_xml = {}
        parameter_values = {}
        status = "created"

        # 先复制本样本需要的作物 XML。
        crops_needed = sorted({clean_text(range_by_key[k]["crop"]) for k in range_by_key if k in sample.index})
        for crop in crops_needed:
            src = CROP_XML_FILES.get(crop)
            if src is None or not src.exists():
                failures.append({"sample_id": sid, "parameter_key": "", "reason": f"缺少 crop XML: {crop}"})
                status = "failed_missing_crop_xml"
                continue
            dst = sample_override_dir / src.name
            shutil.copy2(src, dst)
            copied_crop_xml[crop] = dst

        for key, meta in range_by_key.items():
            if key not in sample.index:
                continue
            value = float(sample[key])
            crop = clean_text(meta["crop"])
            cultivar = clean_text(meta["cultivar"])
            base_name = clean_text(meta.get("base_parameter_name") or meta["parameter_name"])
            value_index = meta.get("value_index", "")
            parameter_values[key] = value
            crop_xml = copied_crop_xml.get(crop)
            if crop_xml is None:
                msg = f"没有可修改的 crop XML: {crop}"
                failures.append({"sample_id": sid, "parameter_key": key, "reason": msg})
                status = "failed_parameter_modification"
                continue
            ok, msg = update_xml_parameter(crop_xml, crop, cultivar, base_name, value, value_index)
            if not ok:
                failures.append({"sample_id": sid, "parameter_key": key, "reason": msg})
                status = "failed_parameter_modification"
            trace_rows.append(
                {
                    "sample_id": sid,
                    "crop": crop,
                    "cultivar": cultivar,
                    "parameter_name": meta["parameter_name"],
                    "baseline_value": meta["baseline_value"],
                    "sampled_value": value,
                    "scenario_id": sample_tag,
                    "simulation_file": str(apsim_file),
                    "output_file": output_files,
                    "parameter_key": key,
                    "status": "ok" if ok else "failed",
                    "message": msg,
                }
            )

        index_rows.append(
            {
                "sample_id": sid,
                "apsim_file": str(apsim_file),
                "parameter_values": json_dumps_compact(parameter_values),
                "status": status,
                "model_override_dir": str(sample_override_dir),
                "output_files": output_files,
            }
        )

    pd.DataFrame(index_rows).to_csv(SIM_INDEX_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(trace_rows).to_csv(PARAM_TRACE_CSV, index=False, encoding="utf-8-sig")
    if failures:
        pd.DataFrame(failures).to_csv(fail_file, index=False, encoding="utf-8-sig")
        logger.warning("存在参数修改失败，详见: %s", fail_file)
    logger.info("已生成模拟索引: %s", SIM_INDEX_CSV)
    logger.info("已生成参数追踪表: %s", PARAM_TRACE_CSV)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("04_generate_apsim_runs")
        logger.exception("脚本失败: %s", exc)
        raise
