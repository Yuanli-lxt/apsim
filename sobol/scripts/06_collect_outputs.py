# -*- coding: utf-8 -*-
"""
06 Collect APSIM Classic output files.

This script searches APSIM Classic .out/.csv/.txt files for each Sobol sample,
extracts standard crop outputs, records source columns, and computes WUE when
evapotranspiration is available.

It is intentionally tolerant: missing variables are written as NA and reported
in extended_missing_variable_report.csv rather than causing the workflow to fail.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from sobol_common import (
    APS_RUN_DIR,
    AVAILABLE_COLUMNS_CSV,
    OUTPUTS_CSV,
    PARAM_TRACE_CSV,
    SIM_INDEX_CSV,
    clean_text,
    ensure_dirs,
    find_first_column,
    first_stage_date,
    read_apsim_out,
    setup_logging,
    unique_existing,
)


VARIABLE_MAP = {
    "wheat": {
        "grain_yield": ["paddock.wheat.yield", "WheatYield", "wheat.Yield", "yield"],
        "biomass": ["paddock.wheat.biomass", "WheatBio", "wheat.Biomass", "biomass"],
        "lai": ["wheatlai", "wheat.lai", "paddock.wheat.lai", "lai"],
        "grain_number": [
            "wheat.grain_no",
            "wheat.grain_number",
            "wheat.grain_number_total",
            "WheatGrainNo",
            "WheatGrainNumber",
            "grain_number",
            "grain number",
            "kernel number",
            "grain_no",
            "grainno",
        ],
        "grain_weight": [
            "wheat.grain_size",
            "WheatGrainSize",
            "wheat.grain_weight",
            "grain_weight",
            "grain weight",
            "grain wt",
            "grain_size",
            "grain size",
            "kernel weight",
            "wheat.grain_wt",
            "WheatGrainWt",
            "WheatGrainWeight",
        ],
        "water_use_efficiency": ["wheat.WUE", "WheatWUE", "water_use_efficiency", "WUE", "water use efficiency"],
        "evapotranspiration": [
            "wheat.evapotranspiration",
            "evapotranspiration",
            "Evapotranspiration",
            "crop_evapotranspiration",
        ],
        # eo 在 APSIM GUI 中显示为 Potential evapotranspiration，不等同于实际 ET。
        # 因此单独保存，不自动用于 WUE 计算。
        "potential_evapotranspiration": ["eo", "PotentialEvapotranspiration", "potential_evapotranspiration"],
        "transpiration": [
            "wheat.transpiration_tot",
            "WheatTranspirationTotal",
            "wheat.transpiration",
            "WheatTranspirationDaily",
            "transpiration_tot",
            "crop_transpiration",
        ],
        "transpiration_efficiency": ["wheat.transp_eff", "WheatTranspirationEfficiency", "transp_eff"],
        "soil_evaporation": [
            "wheat.soil_evaporation",
            "SoilEvaporation",
            "es",
            "soil_evaporation",
            "soil evaporation",
            "SoilEvaporation",
            "evaporation",
            "Evaporation",
            "soil_evap",
        ],
        "potential_soil_evaporation": ["eos", "PotentialSoilEvaporation", "potential_soil_evaporation"],
        "runoff": ["SurfaceRunoff", "runoff", "Runoff", "surface runoff"],
        "drainage": ["Drainage", "drainage", "deep_drainage", "DeepDrainage"],
        "rainfall": ["Rainfall", "rainfall", "rain"],
        "irrigation": ["Irrigation", "irrigation"],
    },
    "maize": {
        "grain_yield": ["paddock.maize.yield", "MaizeYield", "maize.Yield", "yield"],
        "biomass": ["paddock.maize.biomass", "MaizeBio", "maize.Biomass", "biomass"],
        "lai": ["maizelai", "maize.lai", "paddock.maize.lai", "lai"],
        "grain_number": [
            "maize.grain_no",
            "maize.grain_number",
            "maize.kernel_no",
            "maize.kernel_number",
            "MaizeGrainNo",
            "MaizeGrainNumber",
            "MaizeKernelNumber",
            "grain_number",
            "grain number",
            "kernel number",
            "grain_no",
            "grainno",
        ],
        "grain_weight": [
            "maize.grain_size",
            "MaizeGrainSize",
            "maize.grain_weight",
            "grain_weight",
            "grain weight",
            "grain wt",
            "grain_size",
            "grain size",
            "kernel weight",
            "maize.grain_wt",
            "maize.kernel_weight",
            "MaizeGrainWt",
            "MaizeGrainWeight",
            "MaizeKernelWeight",
        ],
        "water_use_efficiency": ["maize.WUE", "MaizeWUE", "water_use_efficiency", "WUE", "water use efficiency"],
        "evapotranspiration": [
            "maize.evapotranspiration",
            "evapotranspiration",
            "Evapotranspiration",
            "crop_evapotranspiration",
        ],
        # eo 在 APSIM GUI 中显示为 Potential evapotranspiration，不等同于实际 ET。
        # 因此单独保存，不自动用于 WUE 计算。
        "potential_evapotranspiration": ["eo", "PotentialEvapotranspiration", "potential_evapotranspiration"],
        "transpiration": [
            "maize.transpiration_tot",
            "MaizeTranspirationTotal",
            "maize.transpiration",
            "MaizeTranspirationDaily",
            "transpiration_tot",
            "crop_transpiration",
        ],
        "transpiration_efficiency": ["maize.transp_eff", "MaizeTranspirationEfficiency", "transp_eff"],
        "soil_evaporation": [
            "maize.soil_evaporation",
            "SoilEvaporation",
            "es",
            "soil_evaporation",
            "soil evaporation",
            "SoilEvaporation",
            "evaporation",
            "Evaporation",
            "soil_evap",
        ],
        "potential_soil_evaporation": ["eos", "PotentialSoilEvaporation", "potential_soil_evaporation"],
        "runoff": ["SurfaceRunoff", "runoff", "Runoff", "surface runoff"],
        "drainage": ["Drainage", "drainage", "deep_drainage", "DeepDrainage"],
        "rainfall": ["Rainfall", "rainfall", "rain"],
        "irrigation": ["Irrigation", "irrigation"],
    },
}

EXTRACT_VARIABLES = [
    "grain_yield",
    "biomass",
    "lai",
    "grain_number",
    "grain_weight",
    "water_use_efficiency",
    "evapotranspiration",
    "potential_evapotranspiration",
    "transpiration",
    "transpiration_efficiency",
    "soil_evaporation",
    "potential_soil_evaporation",
    "runoff",
    "drainage",
    "rainfall",
    "irrigation",
]

MISSING_CHECK_VARIABLES = [
    "grain_number",
    "grain_weight",
    "evapotranspiration",
    "transpiration",
    "soil_evaporation",
    "potential_soil_evaporation",
    "water_use_efficiency_yield",
    "water_use_efficiency_biomass",
]

EXCLUDE_SOURCE_SUBSTRINGS = {
    # eo / PotentialEvapotranspiration 是潜在蒸散，不等同于实际 ET。
    # 避免因为列名包含 Evapotranspiration 而被误认为可用于 WUE 的实际 evapotranspiration。
    "evapotranspiration": ["potential", "transpirationdaily", "transpirationtotal", "transpirationefficiency"],
    "transpiration": ["potential", "evapotranspiration", "efficiency"],
}

AGGREGATION_METHOD = {
    # cumulative / state variables
    "grain_yield": "max",
    "biomass": "max",
    "lai": "max",
    "grain_number": "max",
    "grain_weight": "max",
    "water_use_efficiency": "max",
    "evapotranspiration": "max",
    "potential_evapotranspiration": "max",
    "transpiration": "max",
    "transpiration_efficiency": "max",
    "drainage": "max",
    # daily flux variables: sum once per date where possible
    "soil_evaporation": "sum_by_date",
    "potential_soil_evaporation": "sum_by_date",
    "rainfall": "sum_by_date",
    "irrigation": "sum_by_date",
    "runoff": "sum_by_date",
}


def sample_output_files(row) -> list[Path]:
    files = []
    out_files = clean_text(row.get("output_files"))
    if out_files:
        files.extend(Path(p) for p in out_files.split(";") if p)
    sid = int(row["sample_id"])
    sample_dir = APS_RUN_DIR / "outputs" / f"sample_{sid:06d}"
    if sample_dir.exists():
        files.extend(sample_dir.glob("*.out"))
        files.extend(sample_dir.glob("*.csv"))
        files.extend(sample_dir.glob("*.txt"))
    apsim_file = Path(row["apsim_file"])
    if apsim_file.parent.exists():
        files.extend(apsim_file.parent.glob(f"*sample_{sid:06d}*.out"))
    return unique_existing(files)


def get_crop_cultivars() -> pd.DataFrame:
    if PARAM_TRACE_CSV.exists():
        trace = pd.read_csv(PARAM_TRACE_CSV)
        return trace[["sample_id", "crop", "cultivar"]].drop_duplicates()
    return pd.DataFrame(columns=["sample_id", "crop", "cultivar"])


def is_missing(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def max_ignore_none(a, b):
    if is_missing(a):
        return b
    if is_missing(b):
        return a
    return max(float(a), float(b))


def append_unique(existing: str, value: str) -> str:
    if not value:
        return existing or ""
    parts = [x for x in str(existing).split(";") if x]
    if value not in parts:
        parts.append(value)
    return ";".join(parts)


def find_first_column_filtered(columns, candidates, exclude_substrings=None) -> str | None:
    exclude_substrings = [s.lower() for s in (exclude_substrings or [])]

    def allowed(col: str) -> bool:
        col_l = str(col).lower()
        return not any(bad in col_l for bad in exclude_substrings)

    lower_map = {str(c).lower(): c for c in columns if allowed(str(c))}
    for cand in candidates:
        if str(cand).lower() in lower_map:
            return lower_map[str(cand).lower()]
    for cand in candidates:
        cand_l = str(cand).lower()
        # 对短词或水分关键变量不做宽松 contains，避免 et/transpiration 等误匹配。
        if len(cand_l) <= 3 or cand_l in {"et", "es", "eo", "transpiration", "evapotranspiration"}:
            continue
        for col in columns:
            if allowed(str(col)) and cand_l in str(col).lower():
                return col
    return None


def max_numeric_with_source(df: pd.DataFrame, candidates, exclude_substrings=None) -> tuple[float | None, str]:
    col = find_first_column_filtered(df.columns, candidates, exclude_substrings)
    if col is None:
        return None, ""
    vals = pd.to_numeric(df[col], errors="coerce")
    if vals.dropna().empty:
        return None, col
    return float(vals.max()), col


def aggregate_numeric_with_source(df: pd.DataFrame, candidates, method: str = "max", exclude_substrings=None) -> tuple[float | None, str]:
    col = find_first_column_filtered(df.columns, candidates, exclude_substrings)
    if col is None:
        return None, ""
    vals = pd.to_numeric(df[col], errors="coerce")
    if vals.dropna().empty:
        return None, col
    if method == "sum_by_date":
        date_col = find_first_column(df.columns, ["Date"])
        if date_col is not None:
            temp = pd.DataFrame(
                {
                    "date": pd.to_datetime(df[date_col], errors="coerce"),
                    "value": vals,
                }
            ).dropna(subset=["value"])
            if not temp.empty:
                # outputfile 同时包含 end_day / transition / harvesting 时，同一天可能重复输出。
                # 先取同一日期的最大值，再跨日期求和，避免重复累计。
                if temp["date"].notna().any():
                    return float(temp.groupby("date", dropna=False)["value"].max().sum()), col
        return float(vals.sum()), col
    return float(vals.max()), col


def update_value(out: dict, source_cols: dict, mapping_rows: list, df: pd.DataFrame, var: str, candidates, sid, crop, cultivar):
    val, col = aggregate_numeric_with_source(
        df,
        candidates,
        AGGREGATION_METHOD.get(var, "max"),
        EXCLUDE_SOURCE_SUBSTRINGS.get(var),
    )
    if val is None:
        return
    out[var] = max_ignore_none(out.get(var), val)
    source_cols[f"{var}_source_column"] = append_unique(source_cols.get(f"{var}_source_column", ""), col)
    mapping_rows.append(
        {
            "sample_id": sid,
            "crop": crop,
            "cultivar": cultivar,
            "standard_variable": var,
            "source_column": col,
            "source_output_file": df["_source_output_file"].iloc[0],
        }
    )


def main() -> None:
    ensure_dirs()
    logger = setup_logging("06_collect_outputs")
    if not SIM_INDEX_CSV.exists():
        raise FileNotFoundError(f"Missing simulation_index.csv: {SIM_INDEX_CSV}")
    sim_index = pd.read_csv(SIM_INDEX_CSV)
    crop_cultivars = get_crop_cultivars()
    rows = []
    column_rows = []
    mapping_rows = []

    for _, sim in sim_index.iterrows():
        sid = int(sim["sample_id"])
        files = sample_output_files(sim)
        if not files:
            logger.warning("sample_%06d: no APSIM output files found.", sid)
        dfs = []
        for f in files:
            try:
                df = read_apsim_out(f)
                if df.empty:
                    continue
                df["_source_output_file"] = str(f)
                dfs.append(df)
                for col in df.columns:
                    if col != "_source_output_file":
                        column_rows.append({"sample_id": sid, "source_output_file": str(f), "column": col})
            except Exception as exc:
                logger.warning("Failed reading output %s: %s", f, exc)

        sample_crops = crop_cultivars[crop_cultivars["sample_id"] == sid]
        if sample_crops.empty:
            sample_crops = pd.DataFrame(
                [{"sample_id": sid, "crop": crop, "cultivar": "unknown"} for crop in VARIABLE_MAP]
            )

        for _, cc in sample_crops.iterrows():
            crop = clean_text(cc["crop"]).lower()
            cultivar = clean_text(cc["cultivar"])
            maps = VARIABLE_MAP.get(crop, {})
            out = {
                "sample_id": sid,
                "crop": crop,
                "cultivar": cultivar,
                "year": "",
                "site_if_available": "",
                "grain_yield": None,
                "flowering_date": None,
                "maturity_date": None,
                "biomass": None,
                "lai": None,
                "grain_number": None,
                "grain_weight": None,
                "water_use_efficiency": None,
                "evapotranspiration": None,
                "potential_evapotranspiration": None,
                "transpiration": None,
                "transpiration_efficiency": None,
                "soil_evaporation": None,
                "potential_soil_evaporation": None,
                "runoff": None,
                "drainage": None,
                "rainfall": None,
                "irrigation": None,
                "water_use_efficiency_yield": None,
                "water_use_efficiency_biomass": None,
                "source_output_file": "",
            }
            source_cols = {f"{var}_source_column": "" for var in EXTRACT_VARIABLES}
            source_files = []
            for df in dfs:
                source_files.append(df["_source_output_file"].iloc[0])
                date_col = find_first_column(df.columns, ["Date"])
                if date_col and out["year"] == "":
                    dates = pd.to_datetime(df[date_col], errors="coerce")
                    if not dates.dropna().empty:
                        out["year"] = int(dates.max().year)

                for var in EXTRACT_VARIABLES:
                    update_value(out, source_cols, mapping_rows, df, var, maps.get(var, []), sid, crop, cultivar)

                if out["flowering_date"] is None:
                    out["flowering_date"] = first_stage_date(df, crop, ["flower", "anthesis"])
                if out["maturity_date"] is None:
                    out["maturity_date"] = first_stage_date(df, crop, ["matur", "ripe", "harvest_ripe"])

            if is_missing(out["evapotranspiration"]) and not is_missing(out["transpiration"]) and not is_missing(out["soil_evaporation"]):
                out["evapotranspiration"] = float(out["transpiration"]) + float(out["soil_evaporation"])
                source_cols["evapotranspiration_source_column"] = append_unique(
                    append_unique(source_cols["evapotranspiration_source_column"], source_cols["transpiration_source_column"]),
                    source_cols["soil_evaporation_source_column"],
                )

            if not is_missing(out["evapotranspiration"]) and float(out["evapotranspiration"]) != 0:
                et = float(out["evapotranspiration"])
                if not is_missing(out["grain_yield"]):
                    out["water_use_efficiency_yield"] = float(out["grain_yield"]) / et
                if not is_missing(out["biomass"]):
                    out["water_use_efficiency_biomass"] = float(out["biomass"]) / et

            out["source_output_file"] = ";".join(sorted(set(source_files)))
            out.update(source_cols)
            rows.append(out)

    out_df = pd.DataFrame(rows)
    out_df.to_csv(OUTPUTS_CSV, index=False, encoding="utf-8-sig")
    pd.DataFrame(column_rows).drop_duplicates().to_csv(AVAILABLE_COLUMNS_CSV, index=False, encoding="utf-8-sig")

    mapping_report = OUTPUTS_CSV.parent / "extended_variable_mapping_report.csv"
    missing_report = OUTPUTS_CSV.parent / "extended_missing_variable_report.csv"
    pd.DataFrame(mapping_rows).drop_duplicates().to_csv(mapping_report, index=False, encoding="utf-8-sig")

    missing_rows = []
    for (crop, cultivar), group in out_df.groupby(["crop", "cultivar"], dropna=False):
        for var in MISSING_CHECK_VARIABLES:
            present = int(group[var].notna().sum()) if var in group.columns else 0
            missing = int(len(group) - present)
            if missing:
                missing_rows.append(
                    {
                        "crop": crop,
                        "cultivar": cultivar,
                        "variable": var,
                        "present_count": present,
                        "missing_count": missing,
                        "message": "Variable not available in current APSIM output/report or could not be computed.",
                    }
                )
    pd.DataFrame(missing_rows).to_csv(missing_report, index=False, encoding="utf-8-sig")
    if missing_rows:
        logger.warning("Extended variables have missing values. See: %s", missing_report)
    logger.info("Model output summary written: %s", OUTPUTS_CSV)
    logger.info("Available output columns written: %s", AVAILABLE_COLUMNS_CSV)
    logger.info("Extended variable mapping report written: %s", mapping_report)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("06_collect_outputs")
        logger.exception("Script failed: %s", exc)
        raise
