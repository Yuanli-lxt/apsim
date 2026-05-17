"""
08 绘制 Sobol 结果图，输出 png 和 pdf。
"""

from __future__ import annotations

import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from sobol_common import FIG_DIR, INDICES_SUMMARY_CSV, RANGES_CSV, clean_text, ensure_dirs, setup_logging


def safe_file_part(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", clean_text(text))[:120]


def savefig(name: str) -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ["png", "pdf"]:
        plt.savefig(FIG_DIR / f"{name}.{ext}", dpi=300, bbox_inches="tight")
    plt.close()


def add_parameter_labels(df: pd.DataFrame) -> pd.DataFrame:
    if RANGES_CSV.exists():
        ranges = pd.read_csv(RANGES_CSV)
        label = ranges.set_index("parameter_key")["parameter_name"].to_dict()
        df = df.copy()
        df["parameter_label"] = df["parameter_key"].map(label).fillna(df["parameter_key"])
    else:
        df = df.copy()
        df["parameter_label"] = df["parameter_key"]
    return df


def main() -> None:
    ensure_dirs()
    logger = setup_logging("08_plot_sobol_results")
    if not INDICES_SUMMARY_CSV.exists():
        raise FileNotFoundError(f"缺少 Sobol 指数汇总: {INDICES_SUMMARY_CSV}")
    df = pd.read_csv(INDICES_SUMMARY_CSV)
    df = df[df["S1"].notna() & df["ST"].notna()].copy()
    if df.empty:
        logger.warning("没有可绘图的一阶/总效应结果。")
        return
    df["S1"] = pd.to_numeric(df["S1"], errors="coerce")
    df["ST"] = pd.to_numeric(df["ST"], errors="coerce")
    df = add_parameter_labels(df)
    sns.set_theme(style="whitegrid", font="Arial")

    for (crop, cultivar, target), g in df.groupby(["crop", "cultivar", "target_variable"], dropna=False):
        g = g.sort_values("ST", ascending=False).head(20)
        for metric in ["S1", "ST"]:
            plt.figure(figsize=(8, max(4, 0.35 * len(g))))
            sns.barplot(data=g, y="parameter_label", x=metric, color="#4C78A8")
            plt.xlabel(metric)
            plt.ylabel("Parameter")
            plt.title(f"{target} - {crop} - {cultivar}")
            savefig(f"{metric}_{safe_file_part(target)}_{safe_file_part(crop)}_{safe_file_part(cultivar)}")

        plt.figure(figsize=(6, 5))
        sns.scatterplot(data=g, x="S1", y="ST", hue="parameter_label", s=55)
        lim = max(float(g["ST"].max()), float(g["S1"].max()), 0.1)
        plt.plot([0, lim], [0, lim], color="black", linewidth=1, linestyle="--")
        plt.xlabel("S1")
        plt.ylabel("ST")
        plt.title(f"S1 vs ST - {target} - {crop}")
        plt.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7)
        savefig(f"S1_vs_ST_{safe_file_part(target)}_{safe_file_part(crop)}_{safe_file_part(cultivar)}")

    # 参数 x 输出变量 heatmap，默认用 ST。
    heat = df.pivot_table(index="parameter_label", columns="target_variable", values="ST", aggfunc="mean")
    if not heat.empty:
        plt.figure(figsize=(max(7, 0.7 * heat.shape[1]), max(5, 0.3 * heat.shape[0])))
        sns.heatmap(heat, cmap="viridis", annot=False, cbar_kws={"label": "ST"})
        plt.xlabel("Output variable")
        plt.ylabel("Parameter")
        plt.title("Sobol total-effect heatmap")
        savefig("heatmap_parameter_by_output_ST")

    # 小麦和玉米敏感性对比。
    compare_crop = df.groupby(["crop", "target_variable", "parameter_label"], as_index=False)["ST"].mean()
    if compare_crop["crop"].nunique() >= 2:
        top = compare_crop.groupby("parameter_label")["ST"].mean().sort_values(ascending=False).head(15).index
        plot_df = compare_crop[compare_crop["parameter_label"].isin(top)]
        plt.figure(figsize=(9, max(4, 0.35 * len(top))))
        sns.barplot(data=plot_df, y="parameter_label", x="ST", hue="crop")
        plt.xlabel("Mean ST")
        plt.ylabel("Parameter")
        plt.title("Wheat vs maize sensitivity")
        savefig("crop_comparison_mean_ST")

    # 多品种对比。
    compare_cultivar = df.groupby(["cultivar", "target_variable", "parameter_label"], as_index=False)["ST"].mean()
    if compare_cultivar["cultivar"].nunique() >= 2:
        top = compare_cultivar.groupby("parameter_label")["ST"].mean().sort_values(ascending=False).head(15).index
        plot_df = compare_cultivar[compare_cultivar["parameter_label"].isin(top)]
        plt.figure(figsize=(9, max(4, 0.35 * len(top))))
        sns.barplot(data=plot_df, y="parameter_label", x="ST", hue="cultivar")
        plt.xlabel("Mean ST")
        plt.ylabel("Parameter")
        plt.title("Cultivar sensitivity comparison")
        savefig("cultivar_comparison_mean_ST")

    logger.info("图表已保存到: %s", FIG_DIR)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("08_plot_sobol_results")
        logger.exception("脚本失败: %s", exc)
        raise
