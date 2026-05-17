from pathlib import Path
from io import StringIO

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


# =============================================================================
# User configuration
# =============================================================================

# Use raw strings or pathlib.Path to avoid Windows backslash problems.
truth_file = Path(
    r"F:\APSIM710-r4221\process_bio\independent_validation_observations_p02_maize_p01_wheat.csv"
)

prediction_file = Path(
    r"F:\APSIM710-r4221\process_bio\output\iter_981\outputs\candidate\Rotation Sample Phases.out"
)

output_dir = Path.cwd() / "figures"

# Matching keys. Each tuple is:
#     (column name in truth data, column name in prediction data)
#
# Keep only keys that exist in BOTH files and describe the same sampling unit.
# For the provided files, date + crop/state is a reasonable starting point.
merge_keys = [
    ("date", "Date"),
    ("crop", "currentState"),
    # Examples you can add if both files contain them:
    # ("site", "site"),
    # ("treatment", "treatment"),
    # ("plot_id", "plot_id"),
]

# Optional variable-specific matching keys. Use this when one variable has a
# different observation structure.
#
# In the provided truth file, soil water observations have crop = "soil", while
# APSIM prediction rows use currentState = "wheat" or "maize". Therefore soil
# water should be matched by date only.
variable_merge_keys = {
    "soil_water": [("date", "Date")],
}

# Date parsing. APSIM .out files commonly use dd/mm/yyyy.
truth_date_dayfirst = False
prediction_date_dayfirst = True

# If True, text keys such as crop/site/treatment are stripped and lower-cased.
normalize_text_keys = True

# The provided truth file is a long table:
#     date, crop, plot_id, variable_name, value, unit, ...
# If your truth file is already wide, set truth_is_long_format = False and fill
# truth_value_columns instead.
truth_is_long_format = True

# Columns used when truth_is_long_format = True.
truth_long_variable_column = "variable_name"
truth_long_value_column = "value"
truth_long_record_keys = ["date", "crop", "plot_id"]

# Truth variable names in the long truth table. A list means "aggregate these
# rows into one variable value" using truth_long_multi_value_aggregation.
truth_long_variable_names = {
    "yield": "产量/kg/公顷",
    "biomass": "总生物量/kg/ha",
    "soil_water": [
        "土壤含水量_10cm(water_1)",
        "土壤含水量_20cm(water_2)",
        "土壤含水量_30cm(water_3)",
        "土壤含水量_40cm(water_4)",
        "土壤含水量_50cm(water_5)",
    ],
}

# Used when a variable is represented by multiple truth rows or columns.
truth_multi_value_aggregation = "mean"

# If truth_is_long_format = False, set these to the wide truth columns.
# A list means the columns will be aggregated into one variable value.
truth_value_columns = {
    "yield": "yield",
    "biomass": "biomass",
    "soil_water": "soil_water",
}

# Unit conversion after reading truth values. The provided soil-water truth is
# already in percent, so it remains unchanged.
truth_value_scale = {
    "yield": 1.0,
    "biomass": 1.0,
    "soil_water": 1.0,
}

# Prediction columns. A string is one column. A list aggregates multiple columns.
# A dict chooses crop-specific columns using prediction_crop_column.
prediction_crop_column = "currentState"
prediction_value_columns = {
    "yield": {
        "maize": "MaizeYield",
        "wheat": "WheatYield",
    },
    "biomass": {
        "maize": "MaizeBio",
        "wheat": "WheatBio",
    },
    # Use water_1...water_5 for water content (mm/mm). Use "SoilWater" if you
    # want total soil water storage in mm instead.
    "soil_water": ["water_1", "water_2", "water_3", "water_4", "water_5"],
}

# Unit conversion after reading prediction values.
# APSIM water_1...water_5 are mm/mm fractions, while the provided truth soil
# water observations are percent volumetric water content. Multiplying by 100
# puts prediction soil_water on the same scale as truth.
prediction_value_scale = {
    "yield": 1.0,
    "biomass": 1.0,
    "soil_water": 100.0,
}

prediction_multi_value_aggregation = "mean"

# If multiple prediction rows have the same merge keys, aggregate them before
# merging. This avoids accidental many-to-many joins.
prediction_duplicate_key_aggregation = "mean"
truth_duplicate_key_aggregation = "mean"

variables_to_compare = ["yield", "biomass", "soil_water"]


# =============================================================================
# Reading utilities
# =============================================================================


def read_text_with_fallback(path):
    """Read text using common encodings for Windows/CSV/APSIM files."""
    encodings = ["utf-8-sig", "utf-8", "gbk", "latin1"]
    last_error = None
    for encoding in encodings:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        1,
        f"Could not decode {path}. Last error: {last_error}",
    )


def read_csv_with_fallback(path):
    """Read a CSV file using common encodings."""
    encodings = ["utf-8-sig", "utf-8", "gbk", "latin1"]
    last_error = None
    for encoding in encodings:
        try:
            return pd.read_csv(path, encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not decode CSV file {path}. Last error: {last_error}")


def read_apsim_out(path):
    """Read an APSIM .out file with a header line and a units line."""
    text, encoding = read_text_with_fallback(path)
    lines = text.splitlines()

    header_index = None
    for i, line in enumerate(lines):
        fields = line.strip().split()
        if fields and fields[0].lower() == "date":
            header_index = i
            break

    if header_index is None:
        raise RuntimeError(
            f"Could not find a header line beginning with 'Date' in APSIM file: {path}"
        )

    columns = lines[header_index].strip().split()
    data_lines = lines[header_index + 2 :]
    data_text = "\n".join(data_lines)

    df = pd.read_csv(
        StringIO(data_text),
        sep=r"\s+",
        names=columns,
        engine="python",
        na_values=["?", "NA", "NaN", "nan", ""],
    )
    return df, encoding


def read_table(path):
    """Read CSV-like files and APSIM .out files."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File does not exist: {path}")

    if path.suffix.lower() == ".out":
        return read_apsim_out(path)

    df, encoding = read_csv_with_fallback(path)
    return df, encoding


# =============================================================================
# Data preparation
# =============================================================================


def print_columns(name, df):
    print(f"\n{name} columns ({len(df.columns)}):")
    for col in df.columns:
        print(f"  - {col}")


def print_truth_long_variables(df):
    if truth_long_variable_column in df.columns:
        values = df[truth_long_variable_column].dropna().astype(str).unique()
        print(f"\nTruth variable names in '{truth_long_variable_column}':")
        for value in values:
            print(f"  - {value}")


def ensure_columns(df, required_columns, data_name):
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        available = ", ".join(map(str, df.columns))
        raise KeyError(
            f"{data_name} is missing required column(s): {missing}\n"
            f"Available columns are: {available}"
        )


def as_list(value):
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def aggregate_frame_values(df, columns, aggregation):
    """Aggregate one or more numeric columns row-wise."""
    columns = as_list(columns)
    ensure_columns(df, columns, "Data")
    numeric = df[columns].apply(pd.to_numeric, errors="coerce")

    if len(columns) == 1:
        return numeric[columns[0]]
    if aggregation == "mean":
        return numeric.mean(axis=1)
    if aggregation == "sum":
        return numeric.sum(axis=1)
    if aggregation == "median":
        return numeric.median(axis=1)

    raise ValueError(f"Unsupported aggregation: {aggregation}")


def convert_date_columns(df, dayfirst):
    """Convert any column whose name contains 'date' to pandas datetime."""
    out = df.copy()
    for col in out.columns:
        if "date" in str(col).lower():
            out[col] = pd.to_datetime(out[col], errors="coerce", dayfirst=dayfirst)
    return out


def prepare_truth(truth_raw):
    """Create one truth column per comparison variable."""
    if truth_is_long_format:
        required = list(truth_long_record_keys) + [
            truth_long_variable_column,
            truth_long_value_column,
        ]
        ensure_columns(truth_raw, required, "Truth data")

        pieces = []
        for variable in variables_to_compare:
            names = as_list(truth_long_variable_names[variable])
            sub = truth_raw[truth_raw[truth_long_variable_column].isin(names)].copy()

            if sub.empty:
                raise ValueError(
                    f"No truth rows found for variable '{variable}'. "
                    f"Expected {names} in column '{truth_long_variable_column}'."
                )

            sub[truth_long_value_column] = pd.to_numeric(
                sub[truth_long_value_column], errors="coerce"
            )
            grouped = (
                sub.groupby(truth_long_record_keys, dropna=False)[truth_long_value_column]
                .agg(truth_multi_value_aggregation)
                .reset_index()
                .rename(columns={truth_long_value_column: f"{variable}_truth"})
            )
            grouped[f"{variable}_truth"] = (
                grouped[f"{variable}_truth"] * truth_value_scale.get(variable, 1.0)
            )
            pieces.append(grouped)

        prepared = pieces[0]
        for piece in pieces[1:]:
            prepared = prepared.merge(piece, on=truth_long_record_keys, how="outer")
        return prepared

    required = []
    for variable in variables_to_compare:
        required.extend(as_list(truth_value_columns[variable]))
    ensure_columns(truth_raw, required, "Truth data")

    prepared = truth_raw.copy()
    for variable in variables_to_compare:
        prepared[f"{variable}_truth"] = aggregate_frame_values(
            prepared,
            truth_value_columns[variable],
            truth_multi_value_aggregation,
        ) * truth_value_scale.get(variable, 1.0)
    return prepared


def prepare_prediction(pred_raw):
    """Create one prediction column per comparison variable."""
    prepared = pred_raw.copy()

    for variable in variables_to_compare:
        spec = prediction_value_columns[variable]

        if isinstance(spec, dict):
            ensure_columns(prepared, [prediction_crop_column], "Prediction data")
            result = pd.Series(np.nan, index=prepared.index, dtype="float64")
            crop_values = prepared[prediction_crop_column].astype(str).str.strip().str.lower()

            for crop_name, columns in spec.items():
                columns = as_list(columns)
                ensure_columns(prepared, columns, "Prediction data")
                mask = crop_values == str(crop_name).strip().lower()
                if mask.any():
                    result.loc[mask] = aggregate_frame_values(
                        prepared.loc[mask],
                        columns,
                        prediction_multi_value_aggregation,
                    )

            prepared[f"{variable}_prediction"] = (
                result * prediction_value_scale.get(variable, 1.0)
            )
        else:
            prepared[f"{variable}_prediction"] = aggregate_frame_values(
                prepared,
                spec,
                prediction_multi_value_aggregation,
            ) * prediction_value_scale.get(variable, 1.0)

    return prepared


def normalize_merge_keys(df, key_columns):
    out = df.copy()
    for key in key_columns:
        if key not in out.columns:
            continue
        if pd.api.types.is_datetime64_any_dtype(out[key]):
            out[key] = out[key].dt.normalize()
        elif normalize_text_keys and out[key].dtype == object:
            out[key] = out[key].astype(str).str.strip().str.lower()
    return out


def add_internal_merge_keys(df, external_keys, prefix):
    out = df.copy()
    for i, external_key in enumerate(external_keys):
        internal_key = f"__merge_key_{i}"
        out[internal_key] = out[external_key]
    return out


def reduce_duplicate_keys(df, key_columns, value_columns, aggregation, label):
    duplicate_count = df.duplicated(key_columns).sum()
    if duplicate_count:
        print(
            f"\n{label}: found {duplicate_count} duplicate row(s) for merge keys. "
            f"Aggregating duplicate values with '{aggregation}'."
        )

    keep_columns = key_columns + value_columns
    reduced = (
        df[keep_columns]
        .groupby(key_columns, dropna=False, as_index=False)
        .agg({col: aggregation for col in value_columns})
    )
    return reduced


def get_merge_keys_for_variable(variable):
    return variable_merge_keys.get(variable, merge_keys)


def merge_single_variable(truth_prepared, pred_prepared, variable):
    """Merge observed and predicted data for one variable.

    Different variables may need different keys. For example, crop variables can
    use date + crop, while soil water often uses date only.
    """
    key_pairs = get_merge_keys_for_variable(variable)
    truth_keys = [item[0] for item in key_pairs]
    pred_keys = [item[1] for item in key_pairs]
    truth_col = f"{variable}_truth"
    pred_col = f"{variable}_prediction"

    ensure_columns(truth_prepared, truth_keys + [truth_col], "Prepared truth data")
    ensure_columns(pred_prepared, pred_keys + [pred_col], "Prepared prediction data")

    truth_data = normalize_merge_keys(truth_prepared, truth_keys)
    pred_data = normalize_merge_keys(pred_prepared, pred_keys)

    truth_data = add_internal_merge_keys(truth_data, truth_keys, "truth")
    pred_data = add_internal_merge_keys(pred_data, pred_keys, "prediction")

    internal_keys = [f"__merge_key_{i}" for i in range(len(key_pairs))]
    truth_reduced = reduce_duplicate_keys(
        truth_data,
        internal_keys,
        [truth_col],
        truth_duplicate_key_aggregation,
        f"Truth data for {variable}",
    )
    pred_reduced = reduce_duplicate_keys(
        pred_data,
        internal_keys,
        [pred_col],
        prediction_duplicate_key_aggregation,
        f"Prediction data for {variable}",
    )

    merged = truth_reduced.merge(pred_reduced, on=internal_keys, how="inner")
    print(
        f"\n{variable}: rows after inner merge using keys "
        f"{key_pairs}: {len(merged)}"
    )
    if merged.empty:
        raise RuntimeError(
            f"Merge produced zero rows for variable '{variable}'. "
            f"Check variable_merge_keys, date formats, and key names."
        )

    return merged, internal_keys, key_pairs


# =============================================================================
# Metrics and plotting
# =============================================================================


def compute_metrics(y_true, y_pred):
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    r2 = r2_score(y_true, y_pred) if len(y_true) >= 2 else np.nan
    bias = np.mean(y_pred - y_true)
    return {
        "RMSE": rmse,
        "MAE": mae,
        "R2": r2,
        "Bias": bias,
    }


def metrics_text(metrics):
    return "\n".join(
        [
            f"RMSE = {metrics['RMSE']:.3g}",
            f"MAE = {metrics['MAE']:.3g}",
            f"R2 = {metrics['R2']:.3g}",
            f"Bias = {metrics['Bias']:.3g}",
        ]
    )


def pretty_variable_name(variable):
    return variable.replace("_", " ").title()


def find_date_key(internal_keys, configured_keys):
    for internal_key, (truth_key, pred_key) in zip(internal_keys, configured_keys):
        if "date" in str(truth_key).lower() or "date" in str(pred_key).lower():
            return internal_key
    return None


def clean_variable_data(merged, variable):
    truth_col = f"{variable}_truth"
    pred_col = f"{variable}_prediction"

    data = merged.copy()
    data[truth_col] = pd.to_numeric(data[truth_col], errors="coerce")
    data[pred_col] = pd.to_numeric(data[pred_col], errors="coerce")

    before = len(data)
    data = data.dropna(subset=[truth_col, pred_col])
    dropped = before - len(data)
    if dropped:
        print(f"{variable}: dropped {dropped} row(s) with missing values.")

    if data.empty:
        raise RuntimeError(f"No valid paired data left for variable '{variable}'.")

    return data, truth_col, pred_col


def plot_sequence(data, variable, truth_col, pred_col, date_key, metrics):
    if date_key and pd.api.types.is_datetime64_any_dtype(data[date_key]):
        data = data.sort_values(date_key)
        x = data[date_key]
        xlabel = "Date"
    else:
        data = data.reset_index(drop=True)
        x = np.arange(1, len(data) + 1)
        xlabel = "Sample index"

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x, data[truth_col], marker="o", linewidth=1.6, label="Observed")
    ax.plot(x, data[pred_col], marker="s", linewidth=1.6, label="Predicted")
    ax.set_title(f"{pretty_variable_name(variable)}: Observed vs Predicted")
    ax.set_xlabel(xlabel)
    ax.set_ylabel(pretty_variable_name(variable))
    ax.legend()
    ax.grid(True, alpha=0.35)
    ax.text(
        0.02,
        0.98,
        metrics_text(metrics),
        transform=ax.transAxes,
        va="top",
        ha="left",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
    )
    fig.tight_layout()
    path = output_dir / f"{variable}_sequence_comparison.png"
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return path


def plot_scatter(data, variable, truth_col, pred_col, metrics):
    y_true = data[truth_col].to_numpy(dtype=float)
    y_pred = data[pred_col].to_numpy(dtype=float)

    finite = np.isfinite(y_true) & np.isfinite(y_pred)
    y_true = y_true[finite]
    y_pred = y_pred[finite]

    vmin = min(y_true.min(), y_pred.min())
    vmax = max(y_true.max(), y_pred.max())
    padding = (vmax - vmin) * 0.05 if vmax > vmin else 1.0
    line_min = vmin - padding
    line_max = vmax + padding

    fig, ax = plt.subplots(figsize=(6, 6))
    ax.scatter(y_true, y_pred, alpha=0.8, edgecolor="black", linewidth=0.4)
    ax.plot([line_min, line_max], [line_min, line_max], "r--", label="1:1 line")
    ax.set_xlim(line_min, line_max)
    ax.set_ylim(line_min, line_max)
    ax.set_title(f"{pretty_variable_name(variable)}: Scatter Plot")
    ax.set_xlabel("Observed")
    ax.set_ylabel("Predicted")
    ax.legend()
    ax.grid(True, alpha=0.35)
    ax.text(
        0.05,
        0.95,
        metrics_text(metrics),
        transform=ax.transAxes,
        va="top",
        ha="left",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
    )
    fig.tight_layout()
    path = output_dir / f"{variable}_scatter_observed_vs_predicted.png"
    fig.savefig(path, dpi=300)
    plt.close(fig)
    return path


def run_comparison():
    output_dir.mkdir(parents=True, exist_ok=True)

    truth_raw, truth_encoding = read_table(truth_file)
    pred_raw, pred_encoding = read_table(prediction_file)

    print(f"Truth file: {truth_file}")
    print(f"Truth encoding used: {truth_encoding}")
    print(f"Prediction file: {prediction_file}")
    print(f"Prediction encoding used: {pred_encoding}")

    print_columns("Truth data", truth_raw)
    print_truth_long_variables(truth_raw)
    print_columns("Prediction data", pred_raw)

    truth_raw = convert_date_columns(truth_raw, truth_date_dayfirst)
    pred_raw = convert_date_columns(pred_raw, prediction_date_dayfirst)

    truth_prepared = prepare_truth(truth_raw)
    pred_prepared = prepare_prediction(pred_raw)

    saved_paths = []
    print("\nModel evaluation metrics:")
    for variable in variables_to_compare:
        merged, internal_keys, key_pairs = merge_single_variable(
            truth_prepared,
            pred_prepared,
            variable,
        )
        date_key = find_date_key(internal_keys, key_pairs)
        data, truth_col, pred_col = clean_variable_data(merged, variable)
        y_true = data[truth_col].to_numpy(dtype=float)
        y_pred = data[pred_col].to_numpy(dtype=float)
        metrics = compute_metrics(y_true, y_pred)

        print(
            f"  {variable}: "
            f"RMSE={metrics['RMSE']:.6g}, "
            f"MAE={metrics['MAE']:.6g}, "
            f"R2={metrics['R2']:.6g}, "
            f"Bias={metrics['Bias']:.6g}, "
            f"n={len(data)}"
        )

        saved_paths.append(plot_sequence(data, variable, truth_col, pred_col, date_key, metrics))
        saved_paths.append(plot_scatter(data, variable, truth_col, pred_col, metrics))

    print("\nSaved figures:")
    for path in saved_paths:
        print(f"  - {path}")


if __name__ == "__main__":
    run_comparison()
