"""
07 计算 Sobol 敏感性指数。

对每个 crop/cultivar/目标变量分别计算 S1、ST；如果 problem JSON 中
calc_second_order=True，则同时输出 S2。若输出变量缺失或存在 NaN，本脚本
会写出 sobol_missing_values_report.csv，便于回查 sample 和目标变量。
"""

from __future__ import annotations

import re
import os

import numpy as np
import pandas as pd
from SALib.analyze import sobol

from sobol_common import (
    INDICES_SUMMARY_CSV,
    MISSING_VALUES_REPORT_CSV,
    OUTPUTS_CSV,
    PROBLEM_JSON,
    SAMPLES_WIDE_CSV,
    clean_text,
    ensure_dirs,
    read_problem_json,
    setup_logging,
)


DEFAULT_TARGET_VARIABLES = [
    "grain_yield",
    "flowering_date",
    "maturity_date",
    "biomass",
    "lai",
    "grain_number",
    "grain_weight",
    "water_use_efficiency",
]


def get_target_variables() -> list[str]:
    """允许用 SOBOL_TARGET_VARIABLES 指定本轮要计算的目标变量。"""
    value = os.environ.get("SOBOL_TARGET_VARIABLES", "").strip()
    if not value:
        return DEFAULT_TARGET_VARIABLES
    return [x.strip() for x in value.split(",") if x.strip()]

MISSING_REPORT_CSV = MISSING_VALUES_REPORT_CSV


def target_to_numeric(series: pd.Series) -> pd.Series:
    if series.dtype == object:
        dt = pd.to_datetime(series, errors="coerce")
        if dt.notna().sum() >= series.notna().sum() * 0.5 and dt.notna().sum() > 0:
            return (dt - pd.Timestamp("1970-01-01")).dt.days.astype(float)
    return pd.to_numeric(series, errors="coerce")


def safe_file_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", clean_text(text))[:120]


def main() -> None:
    ensure_dirs()
    logger = setup_logging("07_calculate_sobol_indices")
    if not OUTPUTS_CSV.exists():
        raise FileNotFoundError(f"缺少模型输出: {OUTPUTS_CSV}")
    if not SAMPLES_WIDE_CSV.exists():
        raise FileNotFoundError(f"缺少 Sobol 样本宽表: {SAMPLES_WIDE_CSV}")
    if not PROBLEM_JSON.exists():
        raise FileNotFoundError(f"缺少 Sobol problem 定义: {PROBLEM_JSON}")

    problem = read_problem_json(PROBLEM_JSON)
    calc_second_order = bool(problem.get("metadata", {}).get("calc_second_order", False))
    samples = pd.read_csv(SAMPLES_WIDE_CSV)
    outputs = pd.read_csv(OUTPUTS_CSV)

    summary_rows = []
    missing_rows = []
    names = problem["names"]
    target_variables = get_target_variables()
    logger.info("本轮 Sobol 目标变量: %s", ", ".join(target_variables))
    for (crop, cultivar), group in outputs.groupby(["crop", "cultivar"], dropna=False):
        ordered = samples[["sample_id"]].merge(group, on="sample_id", how="left")
        for target in target_variables:
            if target not in ordered.columns:
                missing_rows.append(
                    {
                        "crop": crop,
                        "cultivar": cultivar,
                        "target_variable": target,
                        "issue": "target_column_missing",
                        "sample_id": "",
                        "message": f"sobol_model_outputs.csv 中没有列 {target}",
                    }
                )
                logger.warning("%s/%s/%s 缺少目标变量列，跳过。", crop, cultivar, target)
                continue

            Y = target_to_numeric(ordered[target])
            if Y.isna().any():
                bad_ids = ordered.loc[Y.isna(), "sample_id"].astype(int).tolist()
                for sid in bad_ids:
                    missing_rows.append(
                        {
                            "crop": crop,
                            "cultivar": cultivar,
                            "target_variable": target,
                            "issue": "missing_or_non_numeric_value",
                            "sample_id": sid,
                            "message": "该 sample 的目标变量缺失或无法转成数值；请检查 APSIM 输出和 VARIABLE_MAP。",
                        }
                    )
                logger.warning(
                    "%s/%s/%s 存在 %s 个缺失或非数值样本，跳过 Sobol。前几个 sample_id=%s",
                    crop,
                    cultivar,
                    target,
                    len(bad_ids),
                    bad_ids[:10],
                )
                continue

            if np.isclose(Y.var(), 0):
                missing_rows.append(
                    {
                        "crop": crop,
                        "cultivar": cultivar,
                        "target_variable": target,
                        "issue": "zero_variance",
                        "sample_id": "",
                        "message": "目标变量无变化，无法计算有意义的 Sobol 指数。",
                    }
                )
                logger.warning("%s/%s/%s 输出无变化，跳过 Sobol。", crop, cultivar, target)
                continue

            try:
                Si = sobol.analyze(problem, Y.values.astype(float), calc_second_order=calc_second_order, print_to_console=False)
            except Exception as exc:
                missing_rows.append(
                    {
                        "crop": crop,
                        "cultivar": cultivar,
                        "target_variable": target,
                        "issue": "sobol_analyze_failed",
                        "sample_id": "",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                )
                logger.warning("%s/%s/%s Sobol 计算失败: %s", crop, cultivar, target, exc)
                continue

            rows = []
            for i, param in enumerate(names):
                row = {
                    "crop": crop,
                    "cultivar": cultivar,
                    "target_variable": target,
                    "parameter_key": param,
                    "S1": Si["S1"][i],
                    "S1_conf": Si["S1_conf"][i],
                    "ST": Si["ST"][i],
                    "ST_conf": Si["ST_conf"][i],
                    "S2_parameter": "",
                    "S2": "",
                    "S2_conf": "",
                }
                rows.append(row)
                summary_rows.append(row.copy())

            if calc_second_order and "S2" in Si:
                for i, p1 in enumerate(names):
                    for j, p2 in enumerate(names):
                        if j <= i:
                            continue
                        row = {
                            "crop": crop,
                            "cultivar": cultivar,
                            "target_variable": target,
                            "parameter_key": p1,
                            "S1": "",
                            "S1_conf": "",
                            "ST": "",
                            "ST_conf": "",
                            "S2_parameter": p2,
                            "S2": Si["S2"][i, j],
                            "S2_conf": Si["S2_conf"][i, j],
                        }
                        rows.append(row)
                        summary_rows.append(row.copy())

            out_file = INDICES_SUMMARY_CSV.parent / (
                f"sobol_indices_{safe_file_part(target)}_{safe_file_part(crop)}_{safe_file_part(cultivar)}.csv"
            )
            pd.DataFrame(rows).to_csv(out_file, index=False, encoding="utf-8-sig")
            logger.info("已输出: %s", out_file)

    pd.DataFrame(summary_rows).to_csv(INDICES_SUMMARY_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(missing_rows).to_csv(MISSING_REPORT_CSV, index=False, encoding="utf-8-sig")
    logger.info("已输出总汇总: %s", INDICES_SUMMARY_CSV)
    if missing_rows:
        logger.warning("存在缺失/跳过项，详见: %s", MISSING_REPORT_CSV)
    if not summary_rows:
        logger.warning("没有任何目标变量成功计算 Sobol 指数；请先检查输出文件和缺失值报告。")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("07_calculate_sobol_indices")
        logger.exception("脚本失败: %s", exc)
        raise
