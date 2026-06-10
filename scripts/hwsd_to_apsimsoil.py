#!/usr/bin/env python
"""
Convert HWSD v2.0 soil data into APSIM Soil/apsimsoil-ready profile files.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from shapely.geometry import Point

try:
    import geopandas as gpd  # noqa: F401  # optional; kept to satisfy GIS dependency requests
except Exception:  # pragma: no cover
    gpd = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]

try:
    import pyodbc  # type: ignore
except Exception:  # pragma: no cover
    pyodbc = None


LAYER_DEFS = [
    {"layer": "D1", "top_cm": 0, "bottom_cm": 20},
    {"layer": "D2", "top_cm": 20, "bottom_cm": 40},
    {"layer": "D3", "top_cm": 40, "bottom_cm": 60},
    {"layer": "D4", "top_cm": 60, "bottom_cm": 80},
    {"layer": "D5", "top_cm": 80, "bottom_cm": 100},
    {"layer": "D6", "top_cm": 100, "bottom_cm": 150},
    {"layer": "D7", "top_cm": 150, "bottom_cm": 200},
]

DEFAULT_KL = [0.06, 0.05, 0.04, 0.03, 0.025, 0.015, 0.01]
DEFAULT_XF = [1.0] * 7
DEFAULT_SOIL_CN = [12.0] * 7
DEFAULT_FOM = [350.0, 270.0, 165.0, 100.0, 60.0, 37.0, 22.0]
DEFAULT_FOM_CN = [40.0] * 7
DEFAULT_FBIOM = [0.04, 0.02, 0.02, 0.02, 0.01, 0.01, 0.01]
DEFAULT_FINERT = [0.40, 0.60, 0.80, 1.00, 1.00, 1.00, 1.00]
DEFAULT_NO3N = [5.0, 3.0, 2.0, 1.5, 1.0, 0.8, 0.5]
DEFAULT_NH4N = [1.0, 0.8, 0.6, 0.5, 0.4, 0.3, 0.2]

FIELD_MAP: Dict[str, List[str]] = {
    "mapping_unit_id": ["MU_GLOBAL", "HWSD2_SMU_ID", "SMU_ID", "HWSD2", "MU_ID", "ID"],
    "sequence": ["SEQUENCE", "SEQ"],
    "layer": ["LAYER", "DEPTH_LAYER", "SOIL_LAYER"],
    "topdep": ["TOPDEP", "TOP_DEPTH", "TOP_CM"],
    "botdep": ["BOTDEP", "BOT_DEPTH", "BOTTOM_CM"],
    "sand": ["SAND", "T_SAND", "SAND_0_20"],
    "silt": ["SILT", "T_SILT", "SILT_0_20"],
    "clay": ["CLAY", "T_CLAY", "CLAY_0_20"],
    "bulk_density": ["BULK", "REF_BULK", "REF_BULK_DENSITY", "BULK_DENSITY", "BD"],
    "organic_carbon": ["ORG_CARBON", "OC", "SOC", "ORGANIC_CARBON"],
    "ph": ["PH_WATER", "PH_H2O", "PH", "T_PH_H2O"],
    "cn_ratio": ["CN_RATIO", "C_N", "CNRATIO"],
    "awc": ["AWC", "PAWC", "AVAILABLE_WATER_CAPACITY"],
    "ll15": ["LL15", "PWP", "THETA1500"],
    "dul": ["DUL", "FC", "THETA33"],
    "sat": ["SAT", "THETAS", "THETA_SAT"],
    "ks": ["KSAT", "KS", "K_SAT", "Ksat", "SAT_HYD_COND"],
}


def _normalize_col_name(col: str) -> str:
    return "".join(ch for ch in str(col).upper() if ch.isalnum())


def _column_lookup(columns: List[str]) -> Dict[str, str]:
    return {_normalize_col_name(c): c for c in columns}


def _find_column(columns: List[str], candidates: List[str]) -> Optional[str]:
    lookup = _column_lookup(columns)
    for cand in candidates:
        key = _normalize_col_name(cand)
        if key in lookup:
            return lookup[key]
    return None


def _safe_float(v: Any) -> float:
    try:
        x = float(v)
        if np.isnan(x):
            return np.nan
        return x
    except Exception:
        return np.nan


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return str(obj)


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if pd.isna(value):
        return True
    return False


def read_hwsd_raster(raster_path: Path) -> Dict[str, Any]:
    if not raster_path.exists():
        raise FileNotFoundError(
            f"找不到 HWSD raster 文件: {raster_path}\n"
            "请检查 --hwsd-raster 路径，建议使用绝对路径。"
        )
    if not raster_path.is_file():
        raise FileNotFoundError(
            f"--hwsd-raster 不是有效文件: {raster_path}"
        )
    with rasterio.open(raster_path) as ds:
        return {
            "path": str(raster_path),
            "crs": ds.crs,
            "width": ds.width,
            "height": ds.height,
            "transform": ds.transform,
            "nodata": ds.nodata,
        }


def read_hwsd_attributes(hwsd_db_path: Path, db_table: Optional[str] = None) -> Dict[str, Any]:
    if not hwsd_db_path.exists():
        raise FileNotFoundError(
            f"找不到 HWSD attribute database 文件: {hwsd_db_path}\n"
            "请检查 --hwsd-db 路径，建议使用绝对路径。"
        )
    if not hwsd_db_path.is_file():
        raise FileNotFoundError(
            f"--hwsd-db 不是有效文件: {hwsd_db_path}"
        )
    suffix = hwsd_db_path.suffix.lower()
    bundle: Dict[str, Any] = {
        "source_path": str(hwsd_db_path),
        "kind": None,
        "main_df": None,
        "layers_df": None,
        "smu_df": None,
        "table_names": [],
    }

    if suffix == ".csv":
        df = pd.read_csv(hwsd_db_path)
        bundle["kind"] = "csv"
        bundle["main_df"] = df
        return bundle

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(hwsd_db_path)
        bundle["kind"] = "excel"
        bundle["main_df"] = df
        return bundle

    if suffix in {".sqlite", ".db"}:
        conn = sqlite3.connect(hwsd_db_path)
        try:
            tables = pd.read_sql_query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", conn
            )["name"].tolist()
            bundle["table_names"] = tables
            if not tables:
                raise ValueError(f"SQLite 数据库中没有可用表: {hwsd_db_path}")

            table_lookup = {_normalize_col_name(t): t for t in tables}
            layers_table = None
            smu_table = None
            for t in tables:
                t_key = _normalize_col_name(t)
                if "LAYER" in t_key and layers_table is None:
                    layers_table = t
                if "SMU" in t_key and smu_table is None:
                    smu_table = t

            if "HWSD2LAYERS" in table_lookup:
                layers_table = table_lookup["HWSD2LAYERS"]
            if "HWSD2SMU" in table_lookup:
                smu_table = table_lookup["HWSD2SMU"]

            if layers_table is not None:
                bundle["layers_df"] = pd.read_sql_query(f'SELECT * FROM "{layers_table}"', conn)
            if smu_table is not None:
                bundle["smu_df"] = pd.read_sql_query(f'SELECT * FROM "{smu_table}"', conn)

            if db_table is not None:
                if db_table not in tables:
                    raise ValueError(
                        f"--db-table={db_table} 不在 SQLite 表中。可选: {', '.join(tables)}"
                    )
                bundle["main_df"] = pd.read_sql_query(f'SELECT * FROM "{db_table}"', conn)
            elif bundle["layers_df"] is not None:
                bundle["main_df"] = bundle["layers_df"]
            else:
                bundle["main_df"] = pd.read_sql_query(f'SELECT * FROM "{tables[0]}"', conn)

            bundle["kind"] = "sqlite"
            return bundle
        finally:
            conn.close()

    if suffix == ".mdb":
        if pyodbc is None:
            raise ImportError(
                "检测到 .mdb 文件，但当前环境未安装 pyodbc。\n"
                "请先安装: pip install pyodbc\n"
                "若仍失败，请安装 Microsoft Access Database Engine。"
            )

        conn_str = (
            r"DRIVER={Microsoft Access Driver (*.mdb, *.accdb)};"
            f"DBQ={hwsd_db_path};"
        )

        try:
            conn = pyodbc.connect(conn_str, autocommit=True)
        except Exception as e:
            raise RuntimeError(
                "无法连接 .mdb。请确认已安装 'Microsoft Access Driver (*.mdb, *.accdb)'。\n"
                "可改为将 .mdb 导出为 CSV/SQLite 后再运行。"
            ) from e

        try:
            table_names = []
            for t in conn.cursor().tables(tableType="TABLE"):
                if t.table_name and not str(t.table_name).startswith("MSys"):
                    table_names.append(str(t.table_name))
            table_names = sorted(set(table_names))
            bundle["table_names"] = table_names
            bundle["kind"] = "mdb"
            if not table_names:
                raise ValueError(f"MDB 数据库中没有可用表: {hwsd_db_path}")

            if db_table is not None:
                if db_table not in table_names:
                    raise ValueError(
                        f"--db-table={db_table} 不在 MDB 表中。可选: {', '.join(table_names)}"
                    )
                bundle["main_df"] = pd.read_sql(f"SELECT * FROM [{db_table}]", conn)
                return bundle

            table_lookup = {_normalize_col_name(t): t for t in table_names}
            layers_table = None
            smu_table = None
            for t in table_names:
                t_key = _normalize_col_name(t)
                if "LAYER" in t_key and layers_table is None:
                    layers_table = t
                if "SMU" in t_key and smu_table is None:
                    smu_table = t

            if "HWSD2LAYERS" in table_lookup:
                layers_table = table_lookup["HWSD2LAYERS"]
            if "HWSD2SMU" in table_lookup:
                smu_table = table_lookup["HWSD2SMU"]

            if layers_table is not None:
                bundle["layers_df"] = pd.read_sql(f"SELECT * FROM [{layers_table}]", conn)
            if smu_table is not None:
                bundle["smu_df"] = pd.read_sql(f"SELECT * FROM [{smu_table}]", conn)

            if bundle["layers_df"] is not None:
                bundle["main_df"] = bundle["layers_df"]
            else:
                # Fallback: pick first table that contains likely mapping-unit key
                selected_table = None
                for t in table_names:
                    probe = pd.read_sql(f"SELECT TOP 1 * FROM [{t}]", conn)
                    cols = probe.columns.tolist()
                    if _find_column(cols, FIELD_MAP["mapping_unit_id"]) is not None:
                        selected_table = t
                        break
                if selected_table is None:
                    selected_table = table_names[0]
                bundle["main_df"] = pd.read_sql(f"SELECT * FROM [{selected_table}]", conn)
            return bundle
        finally:
            conn.close()

    raise ValueError(
        f"不支持的属性数据库格式: {hwsd_db_path.suffix}。请使用 CSV / Excel / SQLite / MDB。"
    )


def transform_lonlat_to_raster_crs(
    lon: float, lat: float, raster_crs: Any
) -> Tuple[float, float, str]:
    if raster_crs is None:
        raise ValueError("Raster CRS 为空，无法完成坐标转换。")

    raster_crs_str = str(raster_crs)
    if "4326" in raster_crs_str.upper():
        return lon, lat, raster_crs_str

    transformer = Transformer.from_crs("EPSG:4326", raster_crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    return x, y, raster_crs_str


def extract_mapping_unit_by_point(raster_path: Path, x: float, y: float) -> int:
    point = Point(x, y)
    with rasterio.open(raster_path) as ds:
        row, col = ds.index(point.x, point.y)
        if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
            raise ValueError("坐标点落在 raster 范围外，无法提取 HWSD mapping unit。")

        data = ds.read(1)
        value = data[row, col]
        nodata = ds.nodata
        if nodata is not None and value == nodata:
            raise ValueError("提取到 nodata 像元，无法获取有效 HWSD mapping unit。")
        if np.isnan(value):
            raise ValueError("提取到 NaN 像元，无法获取有效 HWSD mapping unit。")
        return int(round(float(value)))


def _load_field_map(user_field_map: Optional[Path]) -> Dict[str, List[str]]:
    fmap = {k: v[:] for k, v in FIELD_MAP.items()}
    if user_field_map is None:
        return fmap
    with open(user_field_map, "r", encoding="utf-8") as f:
        override = json.load(f)
    if not isinstance(override, dict):
        raise ValueError("字段映射文件必须是 JSON 对象（dict）。")
    for k, v in override.items():
        if isinstance(v, str):
            fmap[k] = [v]
        elif isinstance(v, list):
            fmap[k] = [str(x) for x in v]
        else:
            raise ValueError(f"字段映射键 {k} 的值必须是字符串或字符串列表。")
    return fmap


def _subset_by_mapping_unit(df: pd.DataFrame, unit_col: str, mapping_unit_id: int) -> pd.DataFrame:
    candidates = [mapping_unit_id, str(mapping_unit_id), float(mapping_unit_id)]
    out = pd.DataFrame()
    for c in candidates:
        tmp = df[df[unit_col] == c]
        if not tmp.empty:
            out = tmp
            break
    if out.empty:
        numeric_col = pd.to_numeric(df[unit_col], errors="coerce")
        out = df[numeric_col == float(mapping_unit_id)]
    return out


def _pick_sequence(df: pd.DataFrame, field_map: Dict[str, List[str]]) -> pd.DataFrame:
    seq_col = _find_column(df.columns.tolist(), field_map["sequence"])
    if seq_col is None:
        return df
    if df.empty:
        return df
    numeric_seq = pd.to_numeric(df[seq_col], errors="coerce")
    if (numeric_seq == 1).any():
        return df[numeric_seq == 1]
    return df[numeric_seq == numeric_seq.min()]


def _make_layer_candidate_names(base: str, layer_def: Dict[str, Any], layer_index_1: int) -> List[str]:
    t = layer_def["top_cm"]
    b = layer_def["bottom_cm"]
    names = [
        base,
        f"{base}_D{layer_index_1}",
        f"{base}D{layer_index_1}",
        f"{base}_{t}_{b}",
        f"{base}_{t}TO{b}",
        f"{base}_{t}-{b}",
        f"{base}_{t}{b}",
    ]
    return names


def _extract_value_from_row(
    row: pd.Series, candidate_names: List[str], layer_def: Dict[str, Any], layer_index_1: int
) -> Tuple[float, Optional[str]]:
    cols = row.index.tolist()
    for base in candidate_names:
        for cand in _make_layer_candidate_names(base, layer_def, layer_index_1):
            col = _find_column(cols, [cand])
            if col is not None:
                return _safe_float(row[col]), col
    return np.nan, None


def _extract_value_from_layer_df(
    layer_df: pd.DataFrame,
    field_candidates: List[str],
    layer_def: Dict[str, Any],
    layer_index_1: int,
) -> Tuple[float, Optional[str]]:
    if layer_df.empty:
        return np.nan, None

    # First try direct layer label matching (D1..D7)
    layer_col = _find_column(layer_df.columns.tolist(), FIELD_MAP["layer"])
    if layer_col is not None:
        layer_mask = layer_df[layer_col].astype(str).str.upper().str.strip() == f"D{layer_index_1}"
        subset = layer_df[layer_mask]
        if not subset.empty:
            row = subset.iloc[0]
            return _extract_value_from_row(row, field_candidates, layer_def, layer_index_1)

    # Then try top/bottom depth matching
    top_col = _find_column(layer_df.columns.tolist(), FIELD_MAP["topdep"])
    bot_col = _find_column(layer_df.columns.tolist(), FIELD_MAP["botdep"])
    if top_col is not None and bot_col is not None:
        top_num = pd.to_numeric(layer_df[top_col], errors="coerce")
        bot_num = pd.to_numeric(layer_df[bot_col], errors="coerce")
        subset = layer_df[
            (np.abs(top_num - layer_def["top_cm"]) <= 1e-6)
            & (np.abs(bot_num - layer_def["bottom_cm"]) <= 1e-6)
        ]
        if not subset.empty:
            row = subset.iloc[0]
            return _extract_value_from_row(row, field_candidates, layer_def, layer_index_1)

    # Fallback: layer order in file (if rows >= 7)
    if len(layer_df) >= layer_index_1:
        row = layer_df.iloc[layer_index_1 - 1]
        return _extract_value_from_row(row, field_candidates, layer_def, layer_index_1)

    return np.nan, None


def map_hwsd_fields(
    attribute_bundle: Dict[str, Any],
    mapping_unit_id: int,
    field_map: Dict[str, List[str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    main_df = attribute_bundle.get("main_df")
    layers_df = attribute_bundle.get("layers_df")
    diagnostics: Dict[str, Any] = {
        "mapping_unit_id": mapping_unit_id,
        "used_tables": [],
        "field_hits": {},
        "missing_fields": [],
    }

    if main_df is None or not isinstance(main_df, pd.DataFrame) or main_df.empty:
        raise ValueError("属性数据库未读取到有效数据（main_df 为空）。")

    map_col_main = _find_column(main_df.columns.tolist(), field_map["mapping_unit_id"])
    if map_col_main is None:
        raise ValueError(
            "无法识别 mapping unit 字段。请在 FIELD_MAP['mapping_unit_id'] 中添加正确字段名。"
        )

    subset_main = _subset_by_mapping_unit(main_df, map_col_main, mapping_unit_id)
    if subset_main.empty:
        raise ValueError(
            f"属性库未找到 mapping unit ID={mapping_unit_id}。"
            "请检查 raster 像元值与属性表主键是否一致。"
        )
    subset_main = _pick_sequence(subset_main, field_map)

    layer_source_df = subset_main
    if layers_df is not None and isinstance(layers_df, pd.DataFrame) and not layers_df.empty:
        map_col_layers = _find_column(layers_df.columns.tolist(), field_map["mapping_unit_id"])
        if map_col_layers is not None:
            subset_layers = _subset_by_mapping_unit(layers_df, map_col_layers, mapping_unit_id)
            subset_layers = _pick_sequence(subset_layers, field_map)
            if not subset_layers.empty:
                layer_source_df = subset_layers
                diagnostics["used_tables"].append("layers_df")
        else:
            diagnostics["used_tables"].append("main_df_no_layer_key")
    else:
        diagnostics["used_tables"].append("main_df")

    if layer_source_df.empty:
        raise ValueError(f"mapping unit ID={mapping_unit_id} 在属性库中无可用土层记录。")

    rows: List[Dict[str, Any]] = []
    for i, layer_def in enumerate(LAYER_DEFS, start=1):
        row_data: Dict[str, Any] = {
            "layer": layer_def["layer"],
            "top_cm": layer_def["top_cm"],
            "bottom_cm": layer_def["bottom_cm"],
            "raw": {},
            "source_flag": {},
            "raw_column_used": {},
        }

        for logical_field in [
            "sand",
            "silt",
            "clay",
            "bulk_density",
            "organic_carbon",
            "ph",
            "cn_ratio",
            "awc",
            "ll15",
            "dul",
            "sat",
            "ks",
        ]:
            val, col = _extract_value_from_layer_df(
                layer_source_df, field_map[logical_field], layer_def, i
            )
            if _is_missing(val):
                row_data["raw"][logical_field] = np.nan
                row_data["source_flag"][logical_field] = "missing"
                diagnostics["missing_fields"].append((logical_field, layer_def["layer"]))
            else:
                row_data["raw"][logical_field] = val
                row_data["source_flag"][logical_field] = "measured_from_HWSD"
                row_data["raw_column_used"][logical_field] = col
                diagnostics["field_hits"].setdefault(logical_field, set()).add(col)

        rows.append(row_data)

    diagnostics["field_hits"] = {
        k: sorted(str(x) for x in list(v)) for k, v in diagnostics["field_hits"].items()
    }
    diagnostics["missing_fields"] = sorted(set(diagnostics["missing_fields"]))
    return rows, diagnostics


def convert_units(layer_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted: List[Dict[str, Any]] = []
    for row in layer_rows:
        out = {
            "layer": row["layer"],
            "top_cm": row["top_cm"],
            "bottom_cm": row["bottom_cm"],
            "thickness_mm": (row["bottom_cm"] - row["top_cm"]) * 10.0,
            "value": {},
            "source_flag": row["source_flag"].copy(),
            "quality_flag": [],
            "raw_column_used": row["raw_column_used"],
        }

        sand = row["raw"]["sand"]
        silt = row["raw"]["silt"]
        clay = row["raw"]["clay"]
        bd = row["raw"]["bulk_density"]
        oc = row["raw"]["organic_carbon"]
        ph = row["raw"]["ph"]
        cn_ratio = row["raw"]["cn_ratio"]
        awc = row["raw"]["awc"]
        ll15 = row["raw"]["ll15"]
        dul = row["raw"]["dul"]
        sat = row["raw"]["sat"]
        ks = row["raw"]["ks"]

        # Normalize texture units to %
        for key, v in [("sand", sand), ("silt", silt), ("clay", clay)]:
            if not _is_missing(v) and 0.0 <= v <= 1.2:
                row["raw"][key] = v * 100.0
        sand = row["raw"]["sand"]
        silt = row["raw"]["silt"]
        clay = row["raw"]["clay"]

        # Bulk density (g/cm3), try fixing common deci-scaled values
        if not _is_missing(bd) and bd > 3.0 and bd <= 20.0:
            bd = bd / 10.0
            out["source_flag"]["bulk_density"] = "calculated_by_PTF"
            out["quality_flag"].append("bulk_density_scaled_div10")

        # Organic carbon to %
        if not _is_missing(oc) and oc > 20.0:
            oc = oc / 10.0
            out["source_flag"]["organic_carbon"] = "calculated_by_PTF"
            out["quality_flag"].append("organic_carbon_gkg_to_percent")

        # Replace impossible negative with NaN
        for vname, v in [("sand", sand), ("silt", silt), ("clay", clay), ("bd", bd), ("oc", oc), ("ph", ph)]:
            if not _is_missing(v) and v < 0:
                if vname == "sand":
                    sand = np.nan
                elif vname == "silt":
                    silt = np.nan
                elif vname == "clay":
                    clay = np.nan
                elif vname == "bd":
                    bd = np.nan
                elif vname == "oc":
                    oc = np.nan
                elif vname == "ph":
                    ph = np.nan

        if not _is_missing(awc):
            if awc > 1.5:
                awc = awc / 100.0
            if awc < 0:
                awc = np.nan

        for key, v in [("ll15", ll15), ("dul", dul), ("sat", sat)]:
            if not _is_missing(v):
                if v > 1.5:
                    row["raw"][key] = v / 100.0
                elif v < 0:
                    row["raw"][key] = np.nan
        ll15 = row["raw"]["ll15"]
        dul = row["raw"]["dul"]
        sat = row["raw"]["sat"]

        if not _is_missing(ks) and ks < 0:
            ks = np.nan

        out["value"].update(
            {
                "sand_pct": sand,
                "silt_pct": silt,
                "clay_pct": clay,
                "bd_g_cm3": bd,
                "oc_pct": oc,
                "ph": ph,
                "cn_ratio": cn_ratio,
                "awc_vwc": awc,
                "ll15_vwc": ll15,
                "dul_vwc": dul,
                "sat_vwc": sat,
                "ks_mm_day": ks,
            }
        )

        converted.append(out)
    return converted


def _saxton_rawls_2006(sand_pct: float, clay_pct: float, om_pct: float) -> Dict[str, float]:
    s = sand_pct / 100.0
    c = clay_pct / 100.0
    om = om_pct

    theta1500t = (
        -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    )
    theta1500 = theta1500t + (0.14 * theta1500t - 0.02)

    theta33t = (
        -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    )
    theta33 = theta33t + (1.283 * theta33t**2 - 0.374 * theta33t - 0.015)

    theta_s33t = (
        0.278 * s + 0.034 * c + 0.022 * om - 0.018 * s * om - 0.027 * c * om - 0.584 * s * c + 0.078
    )
    theta_s33 = theta_s33t + (0.636 * theta_s33t - 0.107)
    theta_sat = theta33 + theta_s33 - 0.097 * s + 0.043

    psi_et = (
        -21.67 * s
        - 27.93 * c
        - 81.97 * theta_s33
        + 71.12 * s * theta_s33
        + 8.29 * c * theta_s33
        + 14.05 * s * c
        + 27.16
    )
    psi_e = psi_et + (0.02 * psi_et**2 - 0.113 * psi_et - 0.70)

    # Campbell "B" and lambda
    if theta33 > 0 and theta1500 > 0 and theta33 != theta1500:
        b_param = (np.log(1500.0) - np.log(33.0)) / (np.log(theta33) - np.log(theta1500))
        lambda_param = 1.0 / b_param if b_param != 0 else np.nan
    else:
        b_param = np.nan
        lambda_param = np.nan

    if np.isnan(lambda_param):
        ks_mm_day = np.nan
    else:
        delta = theta_sat - theta33
        if delta <= 0:
            ks_mm_day = np.nan
        else:
            # Equation commonly expressed in mm/h; convert to mm/day
            ks_mm_h = 1930.0 * (delta ** (3.0 - lambda_param))
            ks_mm_day = ks_mm_h * 24.0

    return {
        "theta1500": float(theta1500),
        "theta33": float(theta33),
        "theta_sat": float(theta_sat),
        "ks_mm_day": float(ks_mm_day) if not np.isnan(ks_mm_day) else np.nan,
        "psi_e": float(psi_e),
        "b_param": float(b_param) if not np.isnan(b_param) else np.nan,
    }


def estimate_water_params(
    converted_rows: List[Dict[str, Any]],
    airdry_ratio: float = 0.5,
    default_ks_mm_day: float = 10.0,
) -> List[Dict[str, Any]]:
    out_rows: List[Dict[str, Any]] = []
    for row in converted_rows:
        rr = json.loads(json.dumps(row, default=_json_default))
        vals = rr["value"]
        src = rr["source_flag"]
        qf = set(rr["quality_flag"])

        sand = vals["sand_pct"]
        silt = vals["silt_pct"]
        clay = vals["clay_pct"]
        bd = vals["bd_g_cm3"]
        oc = vals["oc_pct"]

        om = oc * 1.724 if not _is_missing(oc) else np.nan
        ll15 = vals["ll15_vwc"]
        dul = vals["dul_vwc"]
        sat = vals["sat_vwc"]
        ks = vals["ks_mm_day"]

        ptf = None
        if all(not _is_missing(x) for x in [sand, clay, oc]):
            ptf = _saxton_rawls_2006(sand, clay, om)

        if _is_missing(ll15) and ptf is not None:
            ll15 = ptf["theta1500"]
            src["ll15"] = "calculated_by_PTF"
        if _is_missing(dul):
            if ptf is not None:
                dul = ptf["theta33"]
                src["dul"] = "calculated_by_PTF"
            elif not _is_missing(vals.get("awc_vwc")) and not _is_missing(ll15):
                dul = ll15 + vals["awc_vwc"]
                src["dul"] = "calculated_by_PTF"
                qf.add("dul_from_awc_plus_ll15")
        if _is_missing(sat):
            if not _is_missing(bd):
                sat = 1.0 - bd / 2.65
                src["sat"] = "calculated_by_PTF"
            elif ptf is not None:
                sat = ptf["theta_sat"]
                src["sat"] = "calculated_by_PTF"

        if _is_missing(ks):
            if ptf is not None and not _is_missing(ptf["ks_mm_day"]):
                ks = ptf["ks_mm_day"]
                src["ks"] = "calculated_by_PTF"
            else:
                ks = default_ks_mm_day
                src["ks"] = "default_value"
                qf.add("estimated_low_confidence")

        # Physical constraints
        if _is_missing(ll15):
            ll15 = 0.05
            src["ll15"] = "default_value"
            qf.add("default_ll15")
        if _is_missing(dul):
            dul = ll15 + 0.08
            src["dul"] = "default_value"
            qf.add("default_dul")
        if _is_missing(sat):
            sat = max(dul + 0.05, 0.40)
            src["sat"] = "default_value"
            qf.add("default_sat")

        sat = float(np.clip(sat, 0.10, 0.89))
        dul = float(np.clip(dul, 0.05, sat - 0.005))
        ll15 = float(np.clip(ll15, 0.01, dul - 0.005))

        if not (0.0 < ll15 < dul < sat < 0.9):
            qf.add("water_hierarchy_adjusted")
            if ll15 <= 0:
                ll15 = 0.01
            if dul <= ll15:
                dul = ll15 + 0.01
            if sat <= dul:
                sat = dul + 0.01
            sat = min(sat, 0.89)

        airdry = max(0.001, min(ll15, airdry_ratio * ll15))
        if _is_missing(vals.get("ll15_vwc")):
            src["airdry"] = "calculated_by_PTF"
        else:
            src["airdry"] = "default_value"

        vals["ll15_vwc"] = ll15
        vals["dul_vwc"] = dul
        vals["sat_vwc"] = sat
        vals["airdry_vwc"] = airdry
        vals["ks_mm_day"] = float(max(0.001, ks))
        rr["quality_flag"] = sorted(qf)
        rr["value"] = vals
        rr["source_flag"] = src
        out_rows.append(rr)

    return out_rows


def build_apsim_soil_profile(
    estimated_rows: List[Dict[str, Any]],
    crop_name: str,
    crop_kl: Optional[List[float]] = None,
    crop_xf: Optional[List[float]] = None,
) -> Dict[str, Any]:
    crop_kl = crop_kl if crop_kl is not None else DEFAULT_KL
    crop_xf = crop_xf if crop_xf is not None else DEFAULT_XF
    if len(crop_kl) != 7 or len(crop_xf) != 7:
        raise ValueError("crop.KL 和 crop.XF 必须提供 7 个值（对应 7 层）。")

    layers_out: List[Dict[str, Any]] = []
    for i, row in enumerate(estimated_rows):
        vals = row["value"]
        src = row["source_flag"]
        qf = set(row["quality_flag"])

        carbon = vals["oc_pct"] if not _is_missing(vals["oc_pct"]) else np.nan
        carbon_source = src.get("organic_carbon", "missing")
        if _is_missing(carbon):
            carbon = 0.5
            carbon_source = "default_value"
            qf.add("carbon_defaulted")

        ph = vals["ph"] if not _is_missing(vals["ph"]) else np.nan
        ph_source = src.get("ph", "missing")
        if _is_missing(ph):
            ph = 7.0
            ph_source = "default_value"
            qf.add("ph_defaulted")

        soil_cn = vals["cn_ratio"] if not _is_missing(vals["cn_ratio"]) else DEFAULT_SOIL_CN[i]
        soil_cn_source = src.get("cn_ratio", "default_value") if not _is_missing(vals["cn_ratio"]) else "default_value"

        layer_out = {
            "Layer": row["layer"],
            "Depth": f"{row['top_cm']}-{row['bottom_cm']}",
            "Thickness": row["thickness_mm"],
            "BD": vals["bd_g_cm3"],
            "AirDry": vals["airdry_vwc"],
            "LL15": vals["ll15_vwc"],
            "DUL": vals["dul_vwc"],
            "SAT": vals["sat_vwc"],
            "KS": vals["ks_mm_day"],
            "Carbon": carbon,
            "SoilCNRatio": soil_cn,
            "FOM": DEFAULT_FOM[i],
            "FOM.CN": DEFAULT_FOM_CN[i],
            "FBiom": DEFAULT_FBIOM[i],
            "FInert": DEFAULT_FINERT[i],
            "NO3N": DEFAULT_NO3N[i],
            "NH4N": DEFAULT_NH4N[i],
            "PH": ph,
            "ParticleSizeClay": vals["clay_pct"],
            "ParticleSizeSilt": vals["silt_pct"],
            "ParticleSizeSand": vals["sand_pct"],
            "crop.LL": vals["ll15_vwc"],
            "crop.KL": crop_kl[i],
            "crop.XF": crop_xf[i],
            "quality_flag": sorted(qf),
            "source_flags": {
                "Thickness": "calculated_by_PTF",
                "BD": src.get("bulk_density", "missing"),
                "AirDry": src.get("airdry", "default_value"),
                "LL15": src.get("ll15", "missing"),
                "DUL": src.get("dul", "missing"),
                "SAT": src.get("sat", "missing"),
                "KS": src.get("ks", "missing"),
                "Carbon": carbon_source,
                "SoilCNRatio": soil_cn_source,
                "FOM": "default_value",
                "FOM.CN": "default_value",
                "FBiom": "default_value",
                "FInert": "default_value",
                "NO3N": "default_value",
                "NH4N": "default_value",
                "PH": ph_source,
                "ParticleSizeClay": src.get("clay", "missing"),
                "ParticleSizeSilt": src.get("silt", "missing"),
                "ParticleSizeSand": src.get("sand", "missing"),
                "crop.LL": "calculated_by_PTF" if src.get("ll15", "missing") != "measured_from_HWSD" else "measured_from_HWSD",
                "crop.KL": "default_value" if crop_kl == DEFAULT_KL else "user_supplied",
                "crop.XF": "default_value" if crop_xf == DEFAULT_XF else "user_supplied",
            },
        }

        if layer_out["source_flags"]["crop.KL"] == "default_value":
            layer_out["quality_flag"].append("default_needs_calibration")
        if layer_out["source_flags"]["crop.XF"] == "default_value":
            layer_out["quality_flag"].append("default_needs_calibration")

        layers_out.append(layer_out)

    return {
        "crop_name": crop_name,
        "layers": layers_out,
    }


def validate_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    checks: Dict[str, Any] = {
        "texture_sum_check": [],
        "bd_range_check": [],
        "water_order_check": [],
        "carbon_check": [],
        "ph_check": [],
        "default_or_estimated_fields": [],
        "warnings": [],
    }

    for layer in profile["layers"]:
        lid = layer["Layer"]
        sand = _safe_float(layer["ParticleSizeSand"])
        silt = _safe_float(layer["ParticleSizeSilt"])
        clay = _safe_float(layer["ParticleSizeClay"])
        bd = _safe_float(layer["BD"])
        ll15 = _safe_float(layer["LL15"])
        dul = _safe_float(layer["DUL"])
        sat = _safe_float(layer["SAT"])
        carbon = _safe_float(layer["Carbon"])
        ph = _safe_float(layer["PH"])

        texture_ok = False
        if not any(_is_missing(v) for v in [sand, silt, clay]):
            tsum = sand + silt + clay
            texture_ok = abs(tsum - 100.0) <= 2.0
            if not texture_ok:
                layer["quality_flag"].append("texture_sum_not_100pm2")
                checks["warnings"].append(f"{lid}: sand+silt+clay={tsum:.2f} (偏差>2)")
            checks["texture_sum_check"].append({"layer": lid, "sum_pct": tsum, "ok": texture_ok})
        else:
            checks["texture_sum_check"].append({"layer": lid, "sum_pct": np.nan, "ok": False})

        bd_ok = not _is_missing(bd) and 0.8 <= bd <= 2.0
        if not bd_ok:
            layer["quality_flag"].append("bd_out_of_range")
            checks["warnings"].append(f"{lid}: BD={bd} 不在 0.8-2.0 g/cm3")
        checks["bd_range_check"].append({"layer": lid, "bd": bd, "ok": bd_ok})

        water_ok = (not any(_is_missing(v) for v in [ll15, dul, sat])) and (ll15 < dul < sat)
        if not water_ok:
            layer["quality_flag"].append("water_order_invalid")
            checks["warnings"].append(f"{lid}: LL15/DUL/SAT 顺序异常")
        checks["water_order_check"].append({"layer": lid, "ll15": ll15, "dul": dul, "sat": sat, "ok": water_ok})

        carbon_ok = not _is_missing(carbon) and 0 <= carbon <= 20
        if not carbon_ok:
            layer["quality_flag"].append("carbon_missing_or_abnormal")
        checks["carbon_check"].append({"layer": lid, "carbon_pct": carbon, "ok": carbon_ok})

        ph_ok = not _is_missing(ph) and 3.0 <= ph <= 10.0
        if not ph_ok:
            layer["quality_flag"].append("ph_missing_or_abnormal")
        checks["ph_check"].append({"layer": lid, "ph": ph, "ok": ph_ok})

        non_measured = {}
        for k, v in layer["source_flags"].items():
            if v in {"calculated_by_PTF", "default_value", "user_supplied", "missing"}:
                non_measured[k] = v
        if non_measured:
            checks["default_or_estimated_fields"].append({"layer": lid, "fields": non_measured})

        layer["quality_flag"] = sorted(set(layer["quality_flag"]))

    return checks


def write_outputs(
    outdir: Path,
    profile: Dict[str, Any],
    diagnostics: Dict[str, Any],
    checks: Dict[str, Any],
    run_metadata: Dict[str, Any],
) -> Dict[str, Path]:
    csv_path = outdir / "soil_profile.csv"
    json_path = outdir / "soil_profile.json"
    apsimx_path = outdir / "optional_apsimx_soil_node.json"
    report_path = outdir / "processing_report.txt"

    flat_rows: List[Dict[str, Any]] = []
    for layer in profile["layers"]:
        row = {k: v for k, v in layer.items() if k not in {"source_flags"}}
        for f, flg in layer["source_flags"].items():
            row[f"source_{f}"] = flg
        row["quality_flag"] = ";".join(layer["quality_flag"])
        flat_rows.append(row)

    pd.DataFrame(flat_rows).to_csv(csv_path, index=False, encoding="utf-8")

    soil_json = {
        "metadata": run_metadata,
        "diagnostics": diagnostics,
        "quality_checks": checks,
        "profile": profile,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(soil_json, f, ensure_ascii=False, indent=2, default=_json_default)

    apsimx_node = {
        "Name": "Soil",
        "CropName": profile["crop_name"],
        "Physical": {
            "Thickness": [l["Thickness"] for l in profile["layers"]],
            "BD": [l["BD"] for l in profile["layers"]],
            "AirDry": [l["AirDry"] for l in profile["layers"]],
            "LL15": [l["LL15"] for l in profile["layers"]],
            "DUL": [l["DUL"] for l in profile["layers"]],
            "SAT": [l["SAT"] for l in profile["layers"]],
            "KS": [l["KS"] for l in profile["layers"]],
            "ParticleSizeClay": [l["ParticleSizeClay"] for l in profile["layers"]],
            "ParticleSizeSilt": [l["ParticleSizeSilt"] for l in profile["layers"]],
            "ParticleSizeSand": [l["ParticleSizeSand"] for l in profile["layers"]],
            "Crop": {
                profile["crop_name"]: {
                    "LL": [l["crop.LL"] for l in profile["layers"]],
                    "KL": [l["crop.KL"] for l in profile["layers"]],
                    "XF": [l["crop.XF"] for l in profile["layers"]],
                }
            },
        },
        "Organic": {
            "Carbon": [l["Carbon"] for l in profile["layers"]],
            "SoilCNRatio": [l["SoilCNRatio"] for l in profile["layers"]],
            "FOM": [l["FOM"] for l in profile["layers"]],
            "FOMCN": [l["FOM.CN"] for l in profile["layers"]],
            "FBiom": [l["FBiom"] for l in profile["layers"]],
            "FInert": [l["FInert"] for l in profile["layers"]],
        },
        "Chemical": {
            "PH": [l["PH"] for l in profile["layers"]],
            "NO3N": [l["NO3N"] for l in profile["layers"]],
            "NH4N": [l["NH4N"] for l in profile["layers"]],
        },
    }
    with open(apsimx_path, "w", encoding="utf-8") as f:
        json.dump(apsimx_node, f, ensure_ascii=False, indent=2, default=_json_default)

    lines: List[str] = []
    lines.append("HWSD -> APSIM Soil Processing Report")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Input raster: {run_metadata['hwsd_raster']}")
    lines.append(f"Input attributes: {run_metadata['hwsd_db']}")
    lines.append(f"Input lon/lat (EPSG:4326): lon={run_metadata['lon']} lat={run_metadata['lat']}")
    lines.append(f"Raster CRS: {run_metadata['raster_crs']}")
    lines.append(f"Sample point in raster CRS: x={run_metadata['sample_x']}, y={run_metadata['sample_y']}")
    lines.append(f"Extracted mapping unit ID: {run_metadata['mapping_unit_id']}")
    lines.append(f"Crop name: {run_metadata['crop_name']}")
    lines.append("")
    lines.append("Field mapping diagnostics:")
    lines.append(f"- Used tables: {diagnostics.get('used_tables', [])}")
    lines.append(f"- Field hits: {json.dumps(diagnostics.get('field_hits', {}), ensure_ascii=False)}")
    lines.append(f"- Missing field-layer pairs: {diagnostics.get('missing_fields', [])}")
    lines.append("")
    lines.append("Quality checks summary:")
    lines.append(f"- texture_sum_check: {json.dumps(checks['texture_sum_check'], ensure_ascii=False, default=_json_default)}")
    lines.append(f"- bd_range_check: {json.dumps(checks['bd_range_check'], ensure_ascii=False, default=_json_default)}")
    lines.append(f"- water_order_check: {json.dumps(checks['water_order_check'], ensure_ascii=False, default=_json_default)}")
    lines.append(f"- carbon_check: {json.dumps(checks['carbon_check'], ensure_ascii=False, default=_json_default)}")
    lines.append(f"- ph_check: {json.dumps(checks['ph_check'], ensure_ascii=False, default=_json_default)}")
    lines.append("")
    lines.append("Defaults / estimates used:")
    for item in checks["default_or_estimated_fields"]:
        lines.append(f"- {item['layer']}: {json.dumps(item['fields'], ensure_ascii=False)}")
    lines.append("")
    lines.append("Warnings:")
    if checks["warnings"]:
        for w in checks["warnings"]:
            lines.append(f"- {w}")
    else:
        lines.append("- None")
    lines.append("")
    lines.append("Output files:")
    lines.append(f"- {csv_path}")
    lines.append(f"- {json_path}")
    lines.append(f"- {apsimx_path}")
    lines.append(f"- {report_path}")
    lines.append("")
    lines.append("If field mapping fails, update FIELD_MAP in script or use --field-map-file JSON.")
    lines.append("All default/estimated values are explicitly flagged in CSV/JSON/report.")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return {
        "soil_profile_csv": csv_path,
        "soil_profile_json": json_path,
        "optional_apsimx_soil_node_json": apsimx_path,
        "processing_report_txt": report_path,
    }


def _ensure_outdir(outdir: Path) -> None:
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        raise PermissionError(
            f"无法创建或写入输出目录: {outdir}\n"
            "请修改权限后重试，或改用本地目录（例如 ./hdsw）。"
        ) from e
    except OSError as e:
        raise OSError(
            f"无法创建输出目录: {outdir}\n"
            "请检查路径合法性与权限，必要时改用 ./hdsw。"
        ) from e

    test_file = outdir / ".write_test.tmp"
    try:
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ok")
    except PermissionError as e:
        raise PermissionError(
            f"当前系统对 {outdir} 没有写入权限。\n"
            "请修改权限后重试，或改用本地目录（例如 ./hdsw）。"
        ) from e
    finally:
        if test_file.exists():
            test_file.unlink(missing_ok=True)


def _parse_float_list(text: Optional[str], expected_len: int, arg_name: str) -> Optional[List[float]]:
    if text is None:
        return None
    parts = [x.strip() for x in text.split(",") if x.strip() != ""]
    if len(parts) != expected_len:
        raise ValueError(f"{arg_name} 必须提供 {expected_len} 个逗号分隔数值。")
    values = [float(x) for x in parts]
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert HWSD v2.0 raster + attributes into APSIM Soil profile files."
    )
    parser.add_argument("--hwsd-raster", required=True, type=Path, help="HWSD raster file path (e.g. GeoTIFF)")
    parser.add_argument("--hwsd-db", required=True, type=Path, help="HWSD attribute DB path (CSV/Excel/SQLite/MDB)")
    parser.add_argument("--db-table", default=None, help="Optional table name for SQLite/MDB")
    parser.add_argument("--lon", required=True, type=float, help="Longitude in EPSG:4326")
    parser.add_argument("--lat", required=True, type=float, help="Latitude in EPSG:4326")
    parser.add_argument("--crop", default="Wheat", help="Crop name (default: Wheat)")
    parser.add_argument(
        "--outdir",
        default=PROJECT_ROOT / "hdsw",
        type=Path,
        help="Output directory (default: project hdsw directory)",
    )
    parser.add_argument("--field-map-file", default=None, type=Path, help="JSON file to override FIELD_MAP")
    parser.add_argument("--airdry-ratio", default=0.5, type=float, help="AirDry ratio to LL15 (default 0.5)")
    parser.add_argument(
        "--default-ks-mm-day",
        default=10.0,
        type=float,
        help="Default KS when unavailable (mm/day, flagged low confidence)",
    )
    parser.add_argument(
        "--crop-kl-values",
        default=None,
        help="7 comma-separated values for crop.KL (if omitted, default depth-decreasing values are used)",
    )
    parser.add_argument(
        "--crop-xf-values",
        default=None,
        help="7 comma-separated values for crop.XF (if omitted, all 1.0)",
    )
    args = parser.parse_args()

    # Keep x=lon, y=lat explicitly to avoid inversion.
    lon = float(args.lon)
    lat = float(args.lat)

    if args.airdry_ratio <= 0 or args.airdry_ratio > 1:
        raise ValueError("--airdry-ratio 必须在 (0, 1] 范围内。")

    _ensure_outdir(args.outdir)

    field_map = _load_field_map(args.field_map_file)
    crop_kl = _parse_float_list(args.crop_kl_values, 7, "--crop-kl-values")
    crop_xf = _parse_float_list(args.crop_xf_values, 7, "--crop-xf-values")

    raster_info = read_hwsd_raster(args.hwsd_raster)
    sample_x, sample_y, raster_crs_str = transform_lonlat_to_raster_crs(lon, lat, raster_info["crs"])
    mapping_unit_id = extract_mapping_unit_by_point(args.hwsd_raster, sample_x, sample_y)
    attribute_bundle = read_hwsd_attributes(args.hwsd_db, db_table=args.db_table)

    mapped_rows, diagnostics = map_hwsd_fields(attribute_bundle, mapping_unit_id, field_map)
    converted_rows = convert_units(mapped_rows)
    estimated_rows = estimate_water_params(
        converted_rows,
        airdry_ratio=float(args.airdry_ratio),
        default_ks_mm_day=float(args.default_ks_mm_day),
    )
    profile = build_apsim_soil_profile(
        estimated_rows,
        crop_name=args.crop,
        crop_kl=crop_kl,
        crop_xf=crop_xf,
    )
    checks = validate_profile(profile)

    run_metadata = {
        "hwsd_raster": str(args.hwsd_raster),
        "hwsd_db": str(args.hwsd_db),
        "lon": lon,
        "lat": lat,
        "sample_x": sample_x,
        "sample_y": sample_y,
        "raster_crs": raster_crs_str,
        "mapping_unit_id": mapping_unit_id,
        "crop_name": args.crop,
        "layer_scheme_cm": [(x["top_cm"], x["bottom_cm"]) for x in LAYER_DEFS],
        "outdir": str(args.outdir),
    }

    output_files = write_outputs(args.outdir, profile, diagnostics, checks, run_metadata)

    print("生成完成。输出文件：")
    for k, v in output_files.items():
        print(f"- {k}: {v}")


if __name__ == "__main__":
    main()
