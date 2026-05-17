"""
02 根据 inventory 生成 Sobol 参数范围模板。

重要：默认上下界仅为自动建议，正式运行前必须由研究者逐项检查。
"""

from __future__ import annotations

import re

import pandas as pd

from sobol_common import INVENTORY_CSV, RANGES_CSV, as_float, clean_text, ensure_dirs, setup_logging


CANDIDATE_KEYWORDS = [
    "tt",
    "thermal",
    "flower",
    "floral",
    "matur",
    "grain",
    "kernel",
    "fill",
    "photop",
    "photo",
    "vern",
    "rue",
    "height",
    "leaf",
    "lai",
    "endjuv",
    "juvenile",
    "init",
]

LIKELY_FIXED_KEYWORDS = [
    "derived_from",
    "name",
    "cultivar",
    "x_stem_wt",
]


def biological_hint(name: str) -> str:
    n = name.lower()
    if "vern" in n:
        return "春化敏感性相关参数"
    if "photop" in n or "photo" in n:
        return "光周期响应相关参数"
    if "tt" in n or "thermal" in n or "endjuv" in n or "init" in n:
        return "物候热时间相关参数"
    if "flower" in n or "floral" in n:
        return "开花/花序形成相关参数"
    if "matur" in n:
        return "成熟期相关参数"
    if "grain" in n or "kernel" in n or "fill" in n:
        return "籽粒形成或灌浆相关参数"
    if "leaf" in n or "lai" in n:
        return "叶片/叶面积相关参数"
    if "height" in n:
        return "株高相关参数"
    if "rue" in n:
        return "辐射利用效率相关参数"
    return ""


def main() -> None:
    ensure_dirs()
    logger = setup_logging("02_build_parameter_ranges")
    if not INVENTORY_CSV.exists():
        raise FileNotFoundError(f"请先运行 01_inventory_cultivar_parameters.py: {INVENTORY_CSV}")

    inv = pd.read_csv(INVENTORY_CSV)
    rows = []
    for _, row in inv.iterrows():
        pname = clean_text(row.get("parameter_name"))
        baseline = clean_text(row.get("baseline_value"))
        val = as_float(baseline)
        is_numeric = val is not None
        is_used = clean_text(row.get("is_used_cultivar")).upper() == "TRUE"
        target_kind = clean_text(row.get("target_kind"))
        lower = ""
        upper = ""
        perturbation_type = "none"
        include = False
        reason = ""

        lname = pname.lower()
        if not is_numeric:
            reason = "非数值参数，默认不纳入 Sobol。"
        elif abs(val) == 0:
            reason = "baseline 为 0，百分比扰动不合适；需人工设置上下界。"
        elif target_kind != "external_cultivar_parameter":
            reason = "不是外部 crop XML 中的 cultivar 生理参数，默认不纳入品种 Sobol。"
        elif any(k in lname for k in LIKELY_FIXED_KEYWORDS):
            reason = "疑似查找表轴、继承关系或标识符，默认不扰动。"
        else:
            lower = min(val * 0.9, val * 1.1)
            upper = max(val * 0.9, val * 1.1)
            perturbation_type = "relative_pm10_percent"
            if is_used and any(k in lname for k in CANDIDATE_KEYWORDS):
                include = True
                reason = "Manager 当前调用 cultivar 的候选生理参数，自动建议纳入；正式运行前必须人工确认。"
            elif is_used:
                include = False
                reason = "Manager 当前调用 cultivar 的数值参数，但关键词不强；建议人工判断。"
            else:
                include = False
                reason = "未被当前 .apsim Manager 调用的 cultivar，默认不纳入本次 Sobol。"

        rows.append(
            {
                "crop": row.get("crop", "unknown"),
                "cultivar": row.get("cultivar", "unknown"),
                "parameter_name": pname,
                "baseline_value": baseline,
                "lower_bound": lower,
                "upper_bound": upper,
                "unit": row.get("unit_if_available", ""),
                "perturbation_type": perturbation_type,
                "include_in_sobol": "TRUE" if include else "FALSE",
                "biological_meaning": biological_hint(pname),
                "reason": reason,
                "parameter_key": row.get("parameter_key", ""),
                "source_file": row.get("source_file", ""),
                "target_kind": target_kind,
                "base_parameter_name": row.get("base_parameter_name", pname),
                "value_index": row.get("value_index", ""),
                "xml_path_or_text_location": row.get("xml_path_or_text_location", ""),
                "notes": "自动模板：最终参数范围必须由研究者检查后再正式运行。",
            }
        )

    out = pd.DataFrame(rows)
    out.to_csv(RANGES_CSV, index=False, encoding="utf-8-sig")
    logger.info("已生成参数范围模板: %s", RANGES_CSV)
    logger.info("自动建议 include_in_sobol=TRUE 的参数数: %s", (out["include_in_sobol"] == "TRUE").sum())
    logger.info("请打开 CSV 人工检查 lower_bound/upper_bound/include_in_sobol 后再运行抽样。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("02_build_parameter_ranges")
        logger.exception("脚本失败: %s", exc)
        raise
