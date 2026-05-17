"""
10 比较 N=64 与 N=128 Sobol 结果稳定性。

使用方法示例：
python scripts\10_compare_n64_n128_stability.py ^
  --n64-final "F:\APSIM710-r4221\process_bio\sobol\organized_outputs_screened_N64_20260515_163418\final_results" ^
  --n128-final "F:\APSIM710-r4221\process_bio\sobol\organized_outputs_screened_N128_YYYYMMDD_HHMMSS\final_results"

输出：
- sobol_N64_vs_N128_stability_report.csv/.xlsx/.md
- figures/stability_N64_vs_N128_ST_scatter.png/.pdf
- figures/stability_N64_vs_N128_S1_scatter.png/.pdf
- figures/stability_rank_change_heatmap.png/.pdf
- figures/stability_top5_overlap_bar.png/.pdf
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_N64 = Path(
    r"F:\APSIM710-r4221\process_bio\sobol"
    r"\organized_outputs_screened_N64_20260515_163418\final_results"
)


def parameter_name_from_key(parameter_key: str) -> str:
    parts = str(parameter_key).split("__")
    if len(parts) >= 3:
        return parts[2].replace("_1", "[1]").replace("_2", "[2]").replace("_0", "[0]")
    return str(parameter_key)


def load_indices(final_dir: Path, label: str) -> pd.DataFrame:
    p = final_dir / "sobol_indices_summary.csv"
    if not p.exists():
        raise FileNotFoundError(f"缺少 {label} Sobol 指数文件: {p}")
    df = pd.read_csv(p)
    for col in ["S1", "S1_conf", "ST", "ST_conf"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[df["ST"].notna()].copy()
    df["parameter_name"] = df["parameter_key"].map(parameter_name_from_key)
    # 用稳定的顺序排名而不是并列 min-rank，避免大量 0 值参数都被算入 Top 5。
    df = df.sort_values(
        ["crop", "cultivar", "target_variable", "ST", "parameter_key"],
        ascending=[True, True, True, False, True],
    )
    df[f"rank_ST_{label}"] = df.groupby(["crop", "cultivar", "target_variable"]).cumcount() + 1
    df = df.sort_values(
        ["crop", "cultivar", "target_variable", "S1", "parameter_key"],
        ascending=[True, True, True, False, True],
    )
    df[f"rank_S1_{label}"] = df.groupby(["crop", "cultivar", "target_variable"]).cumcount() + 1
    return df


def spearman_from_ranks(a: pd.Series, b: pd.Series) -> float:
    if len(a) < 2:
        return np.nan
    return float(pd.Series(a).corr(pd.Series(b), method="spearman"))


def write_excel(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_excel(path, index=False)
    except Exception as exc:
        print(f"[WARNING] 无法写入 {path.name}: {exc}")


def save_fig(fig, out_base: Path) -> None:
    fig.savefig(out_base.with_suffix(".png"), dpi=600, bbox_inches="tight")
    fig.savefig(out_base.with_suffix(".pdf"), bbox_inches="tight")


def make_plots(report: pd.DataFrame, out_dir: Path) -> None:
    import matplotlib.pyplot as plt
    import seaborn as sns

    fig_dir = out_dir / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    sns.set_theme(style="whitegrid", context="paper")

    for metric in ["ST", "S1"]:
        x = f"{metric}_N64"
        y = f"{metric}_N128"
        fig, ax = plt.subplots(figsize=(5.0, 4.2))
        sns.scatterplot(
            data=report,
            x=x,
            y=y,
            hue="crop",
            style="target_variable",
            s=45,
            ax=ax,
        )
        lim = max(report[[x, y]].max().max(), 0.05)
        ax.plot([0, lim], [0, lim], color="0.35", lw=1, ls="--")
        ax.set_xlim(min(report[[x, y]].min().min(), 0) - 0.02, lim + 0.02)
        ax.set_ylim(min(report[[x, y]].min().min(), 0) - 0.02, lim + 0.02)
        ax.set_xlabel(f"{metric} at N=64")
        ax.set_ylabel(f"{metric} at N=128")
        ax.set_title(f"N64 vs N128 {metric}")
        ax.legend(fontsize=6, bbox_to_anchor=(1.02, 1), loc="upper left")
        save_fig(fig, fig_dir / f"stability_N64_vs_N128_{metric}_scatter")
        plt.close(fig)

    heat = report.pivot_table(
        index="parameter_name",
        columns=["crop", "target_variable"],
        values="rank_change_ST",
        aggfunc="mean",
    )
    fig_w = max(7, 0.45 * len(heat.columns))
    fig_h = max(5, 0.28 * len(heat.index))
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    sns.heatmap(heat, cmap="coolwarm", center=0, linewidths=0.3, linecolor="white", ax=ax)
    ax.set_title("ST rank change: N128 rank - N64 rank")
    ax.set_xlabel("crop / target")
    ax.set_ylabel("parameter")
    save_fig(fig, fig_dir / "stability_rank_change_heatmap")
    plt.close(fig)

    overlap = (
        report.groupby(["crop", "cultivar", "target_variable"], dropna=False)
        .agg(top3_overlap=("in_both_top3_ST", "sum"), top5_overlap=("in_both_top5_ST", "sum"))
        .reset_index()
    )
    overlap["group"] = overlap["crop"] + " | " + overlap["target_variable"]
    plot_df = overlap.melt(
        id_vars=["group"], value_vars=["top3_overlap", "top5_overlap"], var_name="metric", value_name="overlap"
    )
    fig, ax = plt.subplots(figsize=(7.0, 4.2))
    sns.barplot(data=plot_df, x="group", y="overlap", hue="metric", ax=ax)
    ax.set_ylabel("Number of overlapping parameters")
    ax.set_xlabel("")
    ax.set_ylim(0, 5)
    ax.tick_params(axis="x", rotation=45)
    ax.set_title("Top-parameter overlap between N64 and N128")
    save_fig(fig, fig_dir / "stability_top5_overlap_bar")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="比较 N64 与 N128 Sobol 结果稳定性。")
    parser.add_argument("--n64-final", type=Path, default=DEFAULT_N64, help="N64 final_results 目录")
    parser.add_argument("--n128-final", type=Path, required=True, help="N128 final_results 目录")
    parser.add_argument("--out-dir", type=Path, default=None, help="输出目录；默认写入 N128 final_results")
    args = parser.parse_args()

    out_dir = args.out_dir or args.n128_final
    out_dir.mkdir(parents=True, exist_ok=True)

    n64 = load_indices(args.n64_final, "N64")
    n128 = load_indices(args.n128_final, "N128")
    keys = ["crop", "cultivar", "target_variable", "parameter_key", "parameter_name"]
    merged = n64[keys + ["S1", "S1_conf", "ST", "ST_conf", "rank_ST_N64", "rank_S1_N64"]].merge(
        n128[keys + ["S1", "S1_conf", "ST", "ST_conf", "rank_ST_N128", "rank_S1_N128"]],
        on=keys,
        how="inner",
        suffixes=("_N64", "_N128"),
    )

    merged["ST_diff_N128_minus_N64"] = merged["ST_N128"] - merged["ST_N64"]
    merged["S1_diff_N128_minus_N64"] = merged["S1_N128"] - merged["S1_N64"]
    merged["rank_change_ST"] = merged["rank_ST_N128"] - merged["rank_ST_N64"]
    merged["rank_change_S1"] = merged["rank_S1_N128"] - merged["rank_S1_N64"]
    merged["in_top3_N64_ST"] = merged["rank_ST_N64"] <= 3
    merged["in_top3_N128_ST"] = merged["rank_ST_N128"] <= 3
    merged["in_top5_N64_ST"] = merged["rank_ST_N64"] <= 5
    merged["in_top5_N128_ST"] = merged["rank_ST_N128"] <= 5
    merged["in_both_top3_ST"] = merged["in_top3_N64_ST"] & merged["in_top3_N128_ST"]
    merged["in_both_top5_ST"] = merged["in_top5_N64_ST"] & merged["in_top5_N128_ST"]
    merged["S1_gt_ST_N64"] = merged["S1_N64"] > merged["ST_N64"]
    merged["S1_gt_ST_N128"] = merged["S1_N128"] > merged["ST_N128"]
    merged["S1_conf_change"] = merged["S1_conf_N128"] - merged["S1_conf_N64"]
    merged["ST_conf_change"] = merged["ST_conf_N128"] - merged["ST_conf_N64"]

    group_stats = []
    for keys_group, g in merged.groupby(["crop", "cultivar", "target_variable"], dropna=False):
        top3_64 = set(g.loc[g["rank_ST_N64"] <= 3, "parameter_key"])
        top3_128 = set(g.loc[g["rank_ST_N128"] <= 3, "parameter_key"])
        top5_64 = set(g.loc[g["rank_ST_N64"] <= 5, "parameter_key"])
        top5_128 = set(g.loc[g["rank_ST_N128"] <= 5, "parameter_key"])
        group_stats.append(
            {
                "crop": keys_group[0],
                "cultivar": keys_group[1],
                "target_variable": keys_group[2],
                "top3_overlap_count": len(top3_64 & top3_128),
                "top3_jaccard": len(top3_64 & top3_128) / max(len(top3_64 | top3_128), 1),
                "top5_overlap_count": len(top5_64 & top5_128),
                "top5_jaccard": len(top5_64 & top5_128) / max(len(top5_64 | top5_128), 1),
                "spearman_ST_rank": spearman_from_ranks(g["rank_ST_N64"], g["rank_ST_N128"]),
                "spearman_S1_rank": spearman_from_ranks(g["rank_S1_N64"], g["rank_S1_N128"]),
                "mean_abs_ST_diff": float(g["ST_diff_N128_minus_N64"].abs().mean()),
                "mean_abs_S1_diff": float(g["S1_diff_N128_minus_N64"].abs().mean()),
                "n_S1_gt_ST_N64": int(g["S1_gt_ST_N64"].sum()),
                "n_S1_gt_ST_N128": int(g["S1_gt_ST_N128"].sum()),
                "mean_ST_conf_change": float(g["ST_conf_change"].mean()),
                "mean_S1_conf_change": float(g["S1_conf_change"].mean()),
            }
        )

    group_df = pd.DataFrame(group_stats)
    report = merged.merge(group_df, on=["crop", "cultivar", "target_variable"], how="left")
    report["stability_note"] = np.where(
        (report["in_both_top5_ST"]) & (report["rank_change_ST"].abs() <= 2),
        "stable_top5",
        np.where(report["rank_change_ST"].abs() >= 5, "rank_unstable", "moderate_change"),
    )

    report_csv = out_dir / "sobol_N64_vs_N128_stability_report.csv"
    report_xlsx = out_dir / "sobol_N64_vs_N128_stability_report.xlsx"
    report_md = out_dir / "sobol_N64_vs_N128_stability_report.md"
    report.to_csv(report_csv, index=False, encoding="utf-8-sig")
    write_excel(report, report_xlsx)

    lines = ["# N64 vs N128 Sobol 稳定性比较报告", ""]
    lines.append(f"N64 目录：`{args.n64_final}`")
    lines.append(f"N128 目录：`{args.n128_final}`")
    lines.append("")
    lines.append("## Crop × target 汇总")
    lines.append("")
    lines.append("| crop | target | Top3 overlap | Top5 overlap | Spearman ST | Spearman S1 | mean |ST diff| | S1>ST N64 | S1>ST N128 |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in group_df.iterrows():
        lines.append(
            f"| {r['crop']} | {r['target_variable']} | {int(r['top3_overlap_count'])}/3 | "
            f"{int(r['top5_overlap_count'])}/5 | {r['spearman_ST_rank']:.3f} | "
            f"{r['spearman_S1_rank']:.3f} | {r['mean_abs_ST_diff']:.3f} | "
            f"{int(r['n_S1_gt_ST_N64'])} | {int(r['n_S1_gt_ST_N128'])} |"
        )
    lines.append("")
    lines.append("解释建议：如果 Top3/Top5 重叠度高、Spearman 排名相关高，并且 N128 的置信区间没有变大，则 N64 的主结论可以视为稳定。若单个参数排名变化较大，应优先报告 ST 和参数组层面的结论。")
    report_md.write_text("\n".join(lines), encoding="utf-8")

    make_plots(report, out_dir)
    print(f"已输出稳定性比较表: {report_csv}")
    print(f"已输出稳定性比较报告: {report_md}")
    print(f"图表目录: {out_dir / 'figures'}")


if __name__ == "__main__":
    main()
