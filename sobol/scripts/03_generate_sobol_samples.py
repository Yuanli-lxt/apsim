"""
03 使用 SALib 生成 Sobol / Saltelli 样本。

默认 N=64。总模拟次数：
calc_second_order=True  时为 N * (2D + 2)
calc_second_order=False 时为 N * (D + 2)

参数很多时建议：
1. 先把 include_in_sobol 控制在 5-15 个核心参数；
2. 或先用 Morris 方法筛选，再对少数关键参数做 Sobol。
"""

from __future__ import annotations

import argparse

import pandas as pd

from sobol_common import (
    SAMPLES_LONG_CSV,
    SAMPLES_WIDE_CSV,
    ensure_dirs,
    load_included_ranges,
    setup_logging,
    write_problem_json,
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--N", type=int, default=64, help="Sobol 基础样本量。建议先用 64 或 128 测试。")
    parser.add_argument("--second-order", action="store_true", help="是否计算二阶交互，模拟次数会明显增加。")
    return parser.parse_args()


def main() -> None:
    ensure_dirs()
    logger = setup_logging("03_generate_sobol_samples")
    args = parse_args()
    ranges = load_included_ranges()
    D = len(ranges)
    expected = args.N * (2 * D + 2) if args.second_order else args.N * (D + 2)
    logger.info("参数数量 D=%s, N=%s, 二阶=%s, 预计模拟次数=%s", D, args.N, args.second_order, expected)
    if D > 20:
        logger.warning("参数数量较多，Sobol 成本很高；建议先用 Morris 筛选。")

    problem = write_problem_json(ranges, args.N, args.second_order)
    try:
        from SALib.sample import sobol as sobol_sample

        X = sobol_sample.sample(problem, args.N, calc_second_order=args.second_order, scramble=True)
    except Exception:
        # 兼容较旧 SALib
        from SALib.sample import saltelli

        X = saltelli.sample(problem, args.N, calc_second_order=args.second_order)

    names = problem["names"]
    wide = pd.DataFrame(X, columns=names)
    wide.insert(0, "sample_id", range(1, len(wide) + 1))
    wide.to_csv(SAMPLES_WIDE_CSV, index=False, encoding="utf-8-sig")

    meta = ranges.set_index("parameter_key")
    long_rows = []
    for _, sample in wide.iterrows():
        sid = int(sample["sample_id"])
        for key in names:
            m = meta.loc[key]
            long_rows.append(
                {
                    "sample_id": sid,
                    "crop": m["crop"],
                    "cultivar": m["cultivar"],
                    "parameter_name": m["parameter_name"],
                    "sampled_value": sample[key],
                    "parameter_key": key,
                }
            )
    pd.DataFrame(long_rows).to_csv(SAMPLES_LONG_CSV, index=False, encoding="utf-8-sig")
    logger.info("已输出长格式样本表: %s", SAMPLES_LONG_CSV)
    logger.info("已输出宽格式样本表: %s", SAMPLES_WIDE_CSV)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("03_generate_sobol_samples")
        logger.exception("脚本失败: %s", exc)
        raise
