from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "yield_simulation_runs"
    / "01_main_searches"
    / "cultivar_relaxed_search"
)

LAYER_LABELS = {
    "water_1": "10 cm",
    "water_2": "20 cm",
    "water_3": "30 cm",
    "water_4": "40 cm",
    "water_5": "50 cm",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot soil-water prediction versus truth curves for a yield simulation run."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=DEFAULT_RUN_DIR,
        help="Run directory containing best/prediction_vs_truth.csv.",
    )
    parser.add_argument(
        "--selection",
        default="best",
        help="Subdirectory to plot, for example best or iter_015.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Output directory. Defaults to <run-dir>/<selection>/figures.",
    )
    return parser.parse_args()


def load_soil_water(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    needed = ["scenario", "date", "group", "variable", "obs_value", "sim_value", "rel_error"]
    missing = [col for col in needed if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in {csv_path}: {missing}")

    soil = df.loc[df["group"].eq("soil_water"), needed].copy()
    if soil.empty:
        raise ValueError(f"No soil_water rows found in {csv_path}")

    soil["date"] = pd.to_datetime(soil["date"], errors="coerce")
    for col in ["obs_value", "sim_value", "rel_error"]:
        soil[col] = pd.to_numeric(soil[col], errors="coerce")
    soil = soil.dropna(subset=["date", "obs_value", "sim_value"])
    soil["layer"] = soil["variable"].map(LAYER_LABELS).fillna(soil["variable"])
    soil = soil.sort_values(["scenario", "variable", "date"]).reset_index(drop=True)
    return soil


def write_layer_summary(soil: pd.DataFrame, out_path: Path) -> None:
    summary = (
        soil.assign(bias=soil["sim_value"] - soil["obs_value"])
        .groupby(["scenario", "variable", "layer"], as_index=False)
        .agg(
            n=("rel_error", "count"),
            mean_obs=("obs_value", "mean"),
            mean_sim=("sim_value", "mean"),
            mean_bias=("bias", "mean"),
            mean_rel_error=("rel_error", "mean"),
        )
        .sort_values(["scenario", "variable"])
    )
    summary.to_csv(out_path, index=False, encoding="utf-8-sig")


def plot_curves(soil: pd.DataFrame, out_png: Path, out_pdf: Path, title_suffix: str) -> None:
    candidate = soil.loc[soil["scenario"].eq("candidate")].copy()
    baseline = soil.loc[soil["scenario"].eq("baseline")].copy()
    if candidate.empty:
        raise ValueError("No candidate soil_water rows found.")

    variables = [v for v in LAYER_LABELS if v in set(candidate["variable"])]
    variables.extend(v for v in candidate["variable"].unique() if v not in variables)

    fig, axes = plt.subplots(len(variables), 1, figsize=(12, 10.8), sharex=True)
    if len(variables) == 1:
        axes = [axes]

    for ax, variable in zip(axes, variables):
        c = candidate.loc[candidate["variable"].eq(variable)]
        b = baseline.loc[baseline["variable"].eq(variable)]
        label = LAYER_LABELS.get(variable, variable)

        ax.plot(c["date"], c["obs_value"], color="#222222", linewidth=1.5, label="Truth")
        ax.plot(c["date"], c["sim_value"], color="#2A6FBB", linewidth=1.4, label="Best prediction")
        if not b.empty:
            ax.plot(
                b["date"],
                b["sim_value"],
                color="#9A9A9A",
                linewidth=1.0,
                linestyle="--",
                alpha=0.75,
                label="Baseline prediction",
            )

        mean_rel = c["rel_error"].mean()
        mean_bias = (c["sim_value"] - c["obs_value"]).mean()
        ax.set_title(f"{label}: mean relative error={mean_rel:.3f}, mean bias={mean_bias:+.2f}")
        ax.set_ylabel("VWC (%)")
        ax.grid(True, linewidth=0.5, alpha=0.35)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 0.982))
    axes[-1].set_xlabel("Date")
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    fig.suptitle(f"Soil-water prediction vs truth - {title_suffix}", y=0.998)
    fig.tight_layout(rect=(0, 0, 1, 0.955))
    fig.savefig(out_png, dpi=220)
    fig.savefig(out_pdf)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    selection_dir = args.run_dir / args.selection
    csv_path = selection_dir / "prediction_vs_truth.csv"
    out_dir = args.out_dir or selection_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    soil = load_soil_water(csv_path)
    clean_csv = out_dir / "soil_water_prediction_truth_long.csv"
    summary_csv = out_dir / "soil_water_prediction_truth_summary.csv"
    out_png = out_dir / "soil_water_prediction_truth_curve.png"
    out_pdf = out_dir / "soil_water_prediction_truth_curve.pdf"

    soil.to_csv(clean_csv, index=False, encoding="utf-8-sig")
    write_layer_summary(soil, summary_csv)
    plot_curves(soil, out_png, out_pdf, f"{args.run_dir.name} {args.selection}")

    print(f"Saved: {out_png}")
    print(f"Saved: {out_pdf}")
    print(f"Saved: {clean_csv}")
    print(f"Saved: {summary_csv}")


if __name__ == "__main__":
    main()
