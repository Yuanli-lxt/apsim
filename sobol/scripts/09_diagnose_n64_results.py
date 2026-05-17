"""
09 诊断 Sobol 结果，并生成论文优先使用的结果表。

本脚本只读取已经完成的 N=64 结果，不重新运行 APSIM。
输出：
1. <RUN_LABEL>_result_diagnosis.md
2. publication_table_top5_sobol.csv/.xlsx
3. publication_table_top3_sobol.csv/.xlsx
4. publication_table_parameter_groups.csv/.xlsx

注意：
- S1 > ST 在有限样本 Sobol 估计中可能出现，通常反映 Monte Carlo 误差或置信区间较宽。
- 本脚本把这类结果标记为“需要 N=128 验证”，不把它解释为模型错误。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_FINAL = Path(
    r"F:\APSIM710-r4221\process_bio\sobol"
    r"\organized_outputs_screened_N64_20260515_163418\final_results"
)

CORE_TARGETS = ["grain_yield", "biomass", "lai", "flowering_date", "maturity_date"]
SKIPPED_TARGETS = ["grain_number", "grain_weight", "water_use_efficiency"]


def parameter_name_from_key(parameter_key: str) -> str:
    """从 parameter_key 中保守解析参数名。"""
    text = str(parameter_key)
    parts = text.split("__")
    if len(parts) >= 3:
        name = parts[2]
        name = name.replace("_1", "[1]").replace("_2", "[2]").replace("_0", "[0]")
        return name
    return text


def classify_parameter(parameter_name: str) -> tuple[str, str]:
    """根据参数名进行保守功能分类；不确定时标记 other/unclear。"""
    p = str(parameter_name).lower()
    if "photop" in p:
        return "photoperiod", "按参数名包含 photop 归类为光周期响应"
    if "vern" in p:
        return "vernalization", "按参数名包含 vern 归类为春化响应"
    if "grain_fill" in p or "flower_to_maturity" in p or "maturity_to_ripe" in p:
        return "grain filling", "与灌浆或开花至成熟阶段有关"
    if "grain_size" in p or "max_grain" in p or "grainwt" in p or "kernel" in p:
        return "grain size", "与潜在籽粒大小或粒重有关"
    if "leaf" in p or "lai" in p or "largestleaf" in p:
        return "canopy", "与叶片或冠层结构有关"
    if p == "rue" or "partition" in p or "alloc" in p or "harvest" in p:
        return "biomass allocation", "与辐射利用效率或生物量形成/分配有关"
    if p.startswith("tt_") or "_tt" in p or "thermal" in p:
        return "thermal time", "热时间参数，通常也属于物候控制"
    if any(k in p for k in ["flower", "juvenile", "endjuv", "floral", "maturity", "emerg"]):
        return "phenology", "与发育阶段转换有关"
    return "other/unclear", "仅凭参数名无法可靠判断"


def write_excel(df: pd.DataFrame, path: Path) -> None:
    """写 Excel；如果环境没有 openpyxl/xlsxwriter，则只保留 CSV。"""
    try:
        df.to_excel(path, index=False)
    except Exception as exc:
        print(f"[WARNING] 无法写入 {path.name}: {exc}")


def fmt_float(x, ndigits: int = 3) -> str:
    if pd.isna(x):
        return ""
    try:
        return f"{float(x):.{ndigits}f}"
    except Exception:
        return str(x)


def load_tables(final_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    required = [
        "sobol_indices_summary.csv",
        "sobol_model_outputs.csv",
        "screened_N64_QC_summary.md",
    ]
    # N128 目录的 QC 摘要文件名可能不同；如果没有 screened_N64_QC_summary.md，
    # 诊断仍然可以继续，只在报告中记录为空。
    hard_required = ["sobol_indices_summary.csv", "sobol_model_outputs.csv"]
    for name in hard_required:
        if not (final_dir / name).exists():
            raise FileNotFoundError(f"缺少文件: {final_dir / name}")

    idx = pd.read_csv(final_dir / "sobol_indices_summary.csv")
    top5_path = final_dir / "sobol_top5_by_target.csv"
    top5 = pd.read_csv(top5_path) if top5_path.exists() else pd.DataFrame()
    outputs = pd.read_csv(final_dir / "sobol_model_outputs.csv")
    qc_candidates = [
        final_dir / "screened_N64_QC_summary.md",
        final_dir / "screened_N128_QC_summary.md",
        final_dir / "N128_run_manifest.md",
    ]
    qc_text = ""
    for p in qc_candidates:
        if p.exists():
            qc_text = p.read_text(encoding="utf-8", errors="ignore")
            break
    return idx, top5, outputs, qc_text


def clean_indices(idx: pd.DataFrame) -> pd.DataFrame:
    idx = idx.copy()
    for col in ["S1", "S1_conf", "ST", "ST_conf"]:
        idx[col] = pd.to_numeric(idx[col], errors="coerce")
    idx = idx[idx["ST"].notna()].copy()
    idx["parameter_name"] = idx["parameter_key"].map(parameter_name_from_key)
    idx[["parameter_group", "group_reason"]] = idx["parameter_name"].apply(
        lambda x: pd.Series(classify_parameter(x))
    )
    idx["s1_gt_st"] = idx["S1"] > (idx["ST"] + 1e-12)
    idx["s1_gt_st_margin"] = idx["S1"] - idx["ST"]
    idx["wide_s1_conf"] = idx["S1_conf"] > np.maximum(0.10, idx["S1"].abs())
    idx["wide_st_conf"] = idx["ST_conf"] > np.maximum(0.10, idx["ST"].abs())
    idx["high_ST_low_S1"] = (idx["ST"] >= 0.10) & (idx["S1"].abs() <= 0.05)
    return idx


def make_publication_tables(idx: pd.DataFrame, final_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_cols = [
        "crop",
        "cultivar",
        "target_variable",
        "rank_ST",
        "parameter_name",
        "parameter_key",
        "parameter_group",
        "S1",
        "S1_conf",
        "ST",
        "ST_conf",
        "interpretation_note",
    ]

    ranked = idx.sort_values(["crop", "target_variable", "ST"], ascending=[True, True, False]).copy()
    ranked["rank_ST"] = ranked.groupby(["crop", "cultivar", "target_variable"]).cumcount() + 1
    ranked["interpretation_note"] = np.where(
        ranked["s1_gt_st"],
        "S1 > ST，提示有限样本估计不稳定；建议用 N=128 验证",
        np.where(
            ranked["high_ST_low_S1"],
            "ST 较高而 S1 较低，可能存在非线性或交互影响",
            "可按 ST 排序作描述性解释",
        ),
    )

    top5 = ranked[ranked["rank_ST"] <= 5][base_cols].copy()
    top3 = ranked[ranked["rank_ST"] <= 3][base_cols].copy()

    group_table = (
        idx.groupby(["crop", "cultivar", "target_variable", "parameter_group"], dropna=False)
        .agg(
            n_parameters=("parameter_key", "count"),
            ST_sum=("ST", "sum"),
            ST_mean=("ST", "mean"),
            ST_max=("ST", "max"),
            S1_sum=("S1", "sum"),
            S1_mean=("S1", "mean"),
            n_s1_gt_st=("s1_gt_st", "sum"),
            n_wide_conf=("wide_st_conf", "sum"),
        )
        .reset_index()
        .sort_values(["crop", "target_variable", "ST_sum"], ascending=[True, True, False])
    )

    top5.to_csv(final_dir / "publication_table_top5_sobol.csv", index=False, encoding="utf-8-sig")
    top3.to_csv(final_dir / "publication_table_top3_sobol.csv", index=False, encoding="utf-8-sig")
    group_table.to_csv(final_dir / "publication_table_parameter_groups.csv", index=False, encoding="utf-8-sig")
    # 保持与 N64 目录一致的通用 Top 5 文件名，便于后续脚本和人工查看。
    top5[[
        "crop",
        "cultivar",
        "target_variable",
        "rank_ST",
        "parameter_name",
        "parameter_key",
        "S1",
        "ST",
        "S1_conf",
        "ST_conf",
    ]].rename(columns={"rank_ST": "rank"}).to_csv(
        final_dir / "sobol_top5_by_target.csv", index=False, encoding="utf-8-sig"
    )
    write_excel(top5, final_dir / "publication_table_top5_sobol.xlsx")
    write_excel(top3, final_dir / "publication_table_top3_sobol.xlsx")
    write_excel(group_table, final_dir / "publication_table_parameter_groups.xlsx")
    return top5, top3, group_table


def make_diagnosis(
    idx: pd.DataFrame,
    top5: pd.DataFrame,
    outputs: pd.DataFrame,
    qc_text: str,
    final_dir: Path,
    run_label: str,
    sobol_n: int,
) -> None:
    lines: list[str] = []
    expected_runs = sobol_n * (idx["parameter_key"].nunique() + 2)
    lines.append(f"# {run_label} Sobol 结果诊断报告")
    lines.append("")
    lines.append(f"结果目录：`{final_dir}`")
    lines.append("")
    lines.append("## 1. 运行和输出概况")
    lines.append("")
    lines.append(f"- Sobol 基础样本量：N = {sobol_n}")
    lines.append(f"- 参数数量：D = {idx['parameter_key'].nunique()}")
    lines.append(f"- 不计算二阶交互，预期总模拟数 = {sobol_n} × ({idx['parameter_key'].nunique()} + 2) = {expected_runs}")
    lines.append(f"- APSIM 输出记录数：{len(outputs)}")
    lines.append(f"- 样本数：{outputs['sample_id'].nunique()}")
    lines.append("- 可用于解释的目标变量：grain_yield, biomass, lai, flowering_date, maturity_date")
    lines.append("- 当前跳过变量：grain_number, grain_weight, water_use_efficiency")
    lines.append("")

    lines.append("## 2. 每个 crop × target 的诊断")
    lines.append("")
    for (crop, target), g in idx.groupby(["crop", "target_variable"], dropna=False):
        valid_n = g["parameter_key"].nunique()
        effective_n = int((g["ST"] > 0.01).sum())
        s1_range = (g["S1"].min(), g["S1"].max())
        st_range = (g["ST"].min(), g["ST"].max())
        s1c_range = (g["S1_conf"].min(), g["S1_conf"].max())
        stc_range = (g["ST_conf"].min(), g["ST_conf"].max())
        s1_gt = g[g["s1_gt_st"]].sort_values("s1_gt_st_margin", ascending=False)
        wide = g[g["wide_s1_conf"] | g["wide_st_conf"]]
        nonlinear = g[g["high_ST_low_S1"]].sort_values("ST", ascending=False)
        low_all = bool(g["ST"].max() < 0.05)

        lines.append(f"### {crop} - {target}")
        lines.append("")
        lines.append(f"- 有效参数数量：{valid_n}；ST > 0.01 的参数数量：{effective_n}")
        lines.append(f"- S1 范围：{fmt_float(s1_range[0])} 到 {fmt_float(s1_range[1])}")
        lines.append(f"- ST 范围：{fmt_float(st_range[0])} 到 {fmt_float(st_range[1])}")
        lines.append(f"- S1_conf 范围：{fmt_float(s1c_range[0])} 到 {fmt_float(s1c_range[1])}")
        lines.append(f"- ST_conf 范围：{fmt_float(stc_range[0])} 到 {fmt_float(stc_range[1])}")
        lines.append(f"- 是否存在 S1 > ST：{'是' if len(s1_gt) else '否'}")
        lines.append(f"- 是否存在置信区间偏宽参数：{'是' if len(wide) else '否'}")
        lines.append(f"- 是否存在 ST 高但 S1 低参数：{'是' if len(nonlinear) else '否'}")
        lines.append(f"- 是否所有参数敏感性都很低：{'是' if low_all else '否'}")
        lines.append("")

        lines.append("Top 5 参数（按 ST 排序）：")
        lines.append("")
        lines.append("| rank | parameter | group | S1 | S1_conf | ST | ST_conf | note |")
        lines.append("|---:|---|---|---:|---:|---:|---:|---|")
        top = g.sort_values("ST", ascending=False).head(5)
        for rank, (_, r) in enumerate(top.iterrows(), 1):
            note = []
            if r["s1_gt_st"]:
                note.append("S1>ST")
            if r["wide_s1_conf"] or r["wide_st_conf"]:
                note.append("wide CI")
            if r["high_ST_low_S1"]:
                note.append("high ST/low S1")
            lines.append(
                f"| {rank} | {r['parameter_name']} | {r['parameter_group']} | "
                f"{fmt_float(r['S1'])} | {fmt_float(r['S1_conf'])} | "
                f"{fmt_float(r['ST'])} | {fmt_float(r['ST_conf'])} | {', '.join(note)} |"
            )
        lines.append("")

        if len(s1_gt):
            lines.append("S1 > ST 参数清单：")
            for _, r in s1_gt.iterrows():
                lines.append(
                    f"- {r['parameter_name']}: S1={fmt_float(r['S1'])}, "
                    f"ST={fmt_float(r['ST'])}, margin={fmt_float(r['s1_gt_st_margin'])}"
                )
            lines.append("")

        if len(nonlinear):
            lines.append("ST 较高但 S1 较低的参数：")
            for _, r in nonlinear.iterrows():
                lines.append(
                    f"- {r['parameter_name']}: S1={fmt_float(r['S1'])}, ST={fmt_float(r['ST'])}。"
                    "这可能表示非线性响应或与其他参数共同作用。"
                )
            lines.append("")

        if low_all:
            lines.append("解释提示：该目标变量在当前参数范围内整体敏感性较低，论文中应谨慎表述。")
            lines.append("")

    lines.append("## 3. 可作为论文初稿解释的结果")
    lines.append("")
    lines.append("- maize grain_yield 对 tt_flag_to_flower、tt_flower_to_maturity、tt_endjuv_to_init 的敏感性较突出，可作为初稿主结果。")
    lines.append("- wheat grain_yield 对 max_grain_size、tt_start_grain_fill、tt_floral_initiation、tt_end_of_juvenile、photop_sens 的 ST 较高，可作为初稿主结果，但应关注置信区间。")
    lines.append("- flowering_date 和 maturity_date 的主要敏感参数多为物候/热时间参数，生理解释清晰。")
    lines.append("- lai 对冠层、RUE 或前期发育参数的响应可以作为冠层形成路径的辅助结果。")
    lines.append("")
    lines.append("## 4. 需要 N=128 稳定性验证的结果")
    lines.append("")
    lines.append("- 出现 S1 > ST 的 crop × target × parameter 组合。")
    lines.append("- S1_conf 或 ST_conf 相对指数值偏大的参数。")
    lines.append("- ST 较高但 S1 较低的参数，尤其是可能涉及非线性或交互作用的结果。")
    lines.append("- 小麦 grain_yield 中多个参数 ST 接近时的排序。")
    lines.append("- 玉米 biomass 和 maturity_date 中 S1 > ST 或置信区间偏宽的排序。")
    lines.append("")
    lines.append("## 5. 读取的 QC 摘要")
    lines.append("")
    lines.append("```")
    lines.append(qc_text.strip())
    lines.append("```")

    out_name = f"{run_label}_result_diagnosis.md"
    (final_dir / out_name).write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="诊断 N=64 Sobol 结果并生成论文表格。")
    parser.add_argument("--final-dir", type=Path, default=DEFAULT_FINAL, help="N64 final_results 目录")
    parser.add_argument("--run-label", default="N64", help="报告标签，例如 N64 或 N128")
    parser.add_argument("--N", type=int, default=64, help="Sobol 基础样本量")
    args = parser.parse_args()

    final_dir = args.final_dir
    idx_raw, top5_raw, outputs, qc_text = load_tables(final_dir)
    idx = clean_indices(idx_raw)
    make_publication_tables(idx, final_dir)
    make_diagnosis(idx, top5_raw, outputs, qc_text, final_dir, args.run_label, args.N)
    print(f"已生成诊断报告: {final_dir / (args.run_label + '_result_diagnosis.md')}")
    print(f"已生成论文表格: {final_dir / 'publication_table_top5_sobol.csv'}")
    print(f"已生成论文表格: {final_dir / 'publication_table_top3_sobol.csv'}")
    print(f"已生成参数组表: {final_dir / 'publication_table_parameter_groups.csv'}")


if __name__ == "__main__":
    main()
