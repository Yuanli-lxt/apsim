"""
APSIM Classic cultivar Sobol workflow common utilities.

注意：
1. 这里处理的是 APSIM Classic 7.10 的 XML / 文本结构，不是 APSIM Next Gen .apsimx JSON。
2. modified_from_truth.apsim 中通常只保存作物模块与 Manager 调用；
   真实 cultivar 参数常在 APSIM 安装目录 Model/Wheat.xml、Model/Maize.xml 中。
"""

from __future__ import annotations

import csv
import json
import logging
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from lxml import etree


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_APSIM = Path(os.environ.get("SOBOL_BASE_APSIM", PROJECT_ROOT / "models" / "apsim_classic" / "modified_from_truth.apsim"))
SOBOL_DIR = PROJECT_ROOT / "outputs" / "sobol"
SCRIPTS_DIR = PROJECT_ROOT / "scripts" / "sobol"

# 所有新输出默认进入整理后的归档目录，避免再次散落在 sobol 根目录。
# 如需输出到新的目录，可在 PowerShell 中临时设置：
# $env:SOBOL_OUTPUT_DIR = "F:\APSIM710-r4221\process_bio\outputs\sobol\organized_outputs_new"
OUTPUT_ROOT = Path(
    os.environ.get(
        "SOBOL_OUTPUT_DIR",
        SOBOL_DIR / "organized_outputs_20260515_152346",
    )
)
FINAL_RESULTS_DIR = OUTPUT_ROOT / "final_results"
INTERMEDIATE_DIR = OUTPUT_ROOT / "intermediate_and_raw_files"
HELPER_DIR = OUTPUT_ROOT / "helper_files"

APS_RUN_DIR = INTERMEDIATE_DIR / "apsim_runs"
LOG_DIR = INTERMEDIATE_DIR / "logs"
FIG_DIR = FINAL_RESULTS_DIR / "figures"
BACKUP_DIR = INTERMEDIATE_DIR / "backups"
MODEL_DIR = Path(r"F:\APSIM710-r4221\Model")

CROP_XML_FILES = {
    "wheat": MODEL_DIR / "Wheat.xml",
    "maize": MODEL_DIR / "Maize.xml",
}

INVENTORY_CSV = INTERMEDIATE_DIR / "cultivar_parameter_inventory.csv"
RANGES_CSV = FINAL_RESULTS_DIR / "sobol_parameter_ranges_template.csv"
SAMPLES_LONG_CSV = INTERMEDIATE_DIR / "sobol_samples.csv"
SAMPLES_WIDE_CSV = FINAL_RESULTS_DIR / "sobol_samples_wide.csv"
PROBLEM_JSON = INTERMEDIATE_DIR / "sobol_problem_definition.json"
SIM_INDEX_CSV = FINAL_RESULTS_DIR / "simulation_index.csv"
PARAM_TRACE_CSV = INTERMEDIATE_DIR / "parameter_trace_long.csv"
OUTPUTS_CSV = FINAL_RESULTS_DIR / "sobol_model_outputs.csv"
INDICES_SUMMARY_CSV = FINAL_RESULTS_DIR / "sobol_indices_summary.csv"
AVAILABLE_COLUMNS_CSV = INTERMEDIATE_DIR / "available_output_columns.csv"
MISSING_VALUES_REPORT_CSV = INTERMEDIATE_DIR / "sobol_missing_values_report.csv"


def ensure_dirs() -> None:
    for path in [
        SOBOL_DIR,
        SCRIPTS_DIR,
        OUTPUT_ROOT,
        FINAL_RESULTS_DIR,
        INTERMEDIATE_DIR,
        HELPER_DIR,
        APS_RUN_DIR,
        LOG_DIR,
        FIG_DIR,
        BACKUP_DIR,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def setup_logging(script_name: str) -> logging.Logger:
    ensure_dirs()
    logger = logging.getLogger(script_name)
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    log_file = LOG_DIR / f"{script_name}.log"
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def require_file(path: Path, label: str = "文件") -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label}不存在: {path}")


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_file(src: Path, backup_subdir: str = "baseline") -> Path:
    require_file(src, "待备份文件")
    target_dir = BACKUP_DIR / backup_subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    dst = target_dir / f"{src.stem}_{timestamp()}{src.suffix}"
    shutil.copy2(src, dst)
    return dst


def parse_xml(path: Path) -> etree._ElementTree:
    parser = etree.XMLParser(remove_blank_text=False, recover=True)
    return etree.parse(str(path), parser)


def write_xml(tree: etree._ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(path), encoding="utf-8", xml_declaration=False, pretty_print=True)


def clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def as_float(value: object) -> Optional[float]:
    try:
        txt = clean_text(value)
        if txt == "":
            return None
        return float(txt)
    except Exception:
        return None


def split_numeric_vector(text: str) -> Optional[List[float]]:
    txt = clean_text(text)
    if txt == "":
        return None
    parts = re.split(r"[\s,;]+", txt)
    vals: List[float] = []
    for part in parts:
        if part == "":
            continue
        val = as_float(part)
        if val is None:
            return None
        vals.append(val)
    return vals if len(vals) > 1 else None


def xml_path(element: etree._Element) -> str:
    names = []
    cur = element
    while cur is not None:
        if not isinstance(cur.tag, str):
            cur = cur.getparent()
            continue
        tag = etree.QName(cur).localname
        name = cur.get("name")
        cultivar = cur.get("cultivar")
        label = tag
        if name:
            label += f"[@name='{name}']"
        if cultivar:
            label += f"[@cultivar='{cultivar}']"
        names.append(label)
        cur = cur.getparent()
    return "/" + "/".join(reversed(names))


def infer_crop_from_text(text: str) -> str:
    t = clean_text(text).lower()
    if "wheat" in t:
        return "wheat"
    if "maize" in t or "corn" in t:
        return "maize"
    return "unknown"


def safe_key_part(text: object) -> str:
    txt = clean_text(text)
    txt = re.sub(r"[^A-Za-z0-9_.-]+", "_", txt)
    txt = txt.strip("_")
    return txt or "unknown"


def make_parameter_key(
    crop: str,
    cultivar: str,
    parameter_name: str,
    source_section: str,
    value_index: object = "",
) -> str:
    idx = clean_text(value_index)
    suffix = f"__i{idx}" if idx != "" else ""
    return "__".join(
        [
            safe_key_part(crop),
            safe_key_part(cultivar),
            safe_key_part(parameter_name),
            safe_key_part(source_section),
        ]
    ) + suffix


def decode_parameter_name(name: str) -> str:
    return clean_text(name).replace("[", "_").replace("]", "")


def bool_series(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin(["true", "1", "yes", "y"])


def load_included_ranges(path: Path = RANGES_CSV) -> pd.DataFrame:
    require_file(path, "参数范围表")
    df = pd.read_csv(path)
    if "include_in_sobol" not in df.columns:
        raise ValueError("参数范围表缺少 include_in_sobol 字段")
    df = df[bool_series(df["include_in_sobol"])].copy()
    required = ["parameter_key", "lower_bound", "upper_bound", "crop", "cultivar", "parameter_name"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"参数范围表缺少字段: {missing}")
    if df.empty:
        raise ValueError("没有 include_in_sobol=TRUE 的参数。请先人工检查并修改范围模板。")
    df["lower_bound"] = pd.to_numeric(df["lower_bound"], errors="coerce")
    df["upper_bound"] = pd.to_numeric(df["upper_bound"], errors="coerce")
    bad = df[df[["lower_bound", "upper_bound"]].isna().any(axis=1)]
    if not bad.empty:
        raise ValueError(f"存在上下界为空或非数值的参数: {bad['parameter_key'].tolist()[:10]}")
    return df


def write_problem_json(ranges: pd.DataFrame, n_base: int, calc_second_order: bool) -> Dict:
    names = ranges["parameter_key"].tolist()
    bounds = ranges[["lower_bound", "upper_bound"]].astype(float).values.tolist()
    problem = {
        "num_vars": len(names),
        "names": names,
        "bounds": bounds,
        "metadata": {
            "n_base": int(n_base),
            "calc_second_order": bool(calc_second_order),
            "sample_formula": "N*(2D+2)" if calc_second_order else "N*(D+2)",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "note": "最终参数范围必须由研究者检查后再正式运行。",
        },
        "parameter_table": ranges.to_dict(orient="records"),
    }
    with open(PROBLEM_JSON, "w", encoding="utf-8") as f:
        json.dump(problem, f, ensure_ascii=False, indent=2)
    return problem


def read_problem_json(path: Path = PROBLEM_JSON) -> Dict:
    require_file(path, "Sobol problem JSON")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def update_xml_parameter(
    crop_xml: Path,
    crop: str,
    cultivar: str,
    base_parameter_name: str,
    sampled_value: float,
    value_index: object = "",
) -> Tuple[bool, str]:
    """在 crop XML 中修改某个 cultivar 子参数。

    value_index 为空表示标量；非空表示修改空格分隔数值向量中的某一位。
    """
    try:
        tree = parse_xml(crop_xml)
        root = tree.getroot()
        cultivar_nodes = root.xpath(f".//*[local-name()=$name]", name=cultivar)
        cultivar_nodes = [n for n in cultivar_nodes if clean_text(n.get("cultivar")).lower() == "yes" or n.tag == cultivar]
        if not cultivar_nodes:
            return False, f"未找到 cultivar 节点: {crop}/{cultivar}"
        node = cultivar_nodes[0]
        params = node.xpath(f"./*[local-name()=$pname]", pname=base_parameter_name)
        if not params:
            return False, f"未找到参数节点: {crop}/{cultivar}/{base_parameter_name}"
        param = params[0]
        idx_txt = clean_text(value_index)
        if idx_txt == "":
            param.text = format_float(sampled_value)
        else:
            idx = int(float(idx_txt))
            vals = split_numeric_vector(param.text or "")
            if vals is None:
                return False, f"参数不是可解析数值向量: {base_parameter_name}={param.text}"
            if idx < 0 or idx >= len(vals):
                return False, f"向量索引越界: {base_parameter_name}[{idx}]"
            vals[idx] = float(sampled_value)
            param.text = " ".join(format_float(v) for v in vals)
        write_xml(tree, crop_xml)
        return True, "ok"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def format_float(value: float) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return f"{float(value):.10g}"


def unique_existing(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    out = []
    for path in paths:
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        if resolved.exists() and resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def read_apsim_out(path: Path) -> pd.DataFrame:
    """读取 APSIM Classic .out/.txt 的常见固定宽度/空白分隔格式。

    APSIM Classic 输出通常前两行是版本和标题，随后一行列名、一行单位，再是数据。
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\bDate\b|dd/mm/yyyy|simulation_days", line, flags=re.I):
            header_idx = i
            break
    if header_idx is None:
        # 尝试 CSV/TSV
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame()
    header = re.split(r"\s+", lines[header_idx].strip())
    data_lines = []
    for line in lines[header_idx + 1 :]:
        if not line.strip():
            continue
        if line.strip().startswith("("):
            continue
        data_lines.append(line)
    rows = []
    for line in data_lines:
        parts = re.split(r"\s+", line.strip())
        if len(parts) < len(header):
            continue
        if len(parts) > len(header):
            parts = parts[: len(header) - 1] + [" ".join(parts[len(header) - 1 :])]
        rows.append(parts)
    df = pd.DataFrame(rows, columns=header)
    for col in df.columns:
        if col.lower() == "date":
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors="coerce")
        else:
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() > 0:
                df[col] = converted
    return df


def find_first_column(columns: Sequence[str], candidates: Sequence[str]) -> Optional[str]:
    lower_map = {c.lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lower_map:
            return lower_map[cand.lower()]
    for cand in candidates:
        cand_l = cand.lower()
        for col in columns:
            if cand_l in col.lower():
                return col
    return None


def max_numeric(df: pd.DataFrame, candidates: Sequence[str]) -> Optional[float]:
    col = find_first_column(df.columns, candidates)
    if col is None:
        return None
    vals = pd.to_numeric(df[col], errors="coerce")
    if vals.dropna().empty:
        return None
    return float(vals.max())


def first_stage_date(df: pd.DataFrame, crop: str, keywords: Sequence[str]) -> Optional[str]:
    date_col = find_first_column(df.columns, ["Date"])
    stage_col = find_first_column(
        df.columns,
        [f"{crop}Stage", f"{crop}.StageName", f"paddock.{crop}.StageName", "StageName"],
    )
    if date_col is None or stage_col is None:
        return None
    pat = re.compile("|".join(re.escape(k) for k in keywords), flags=re.I)
    hit = df[df[stage_col].astype(str).str.contains(pat, na=False)]
    if hit.empty:
        return None
    val = hit.iloc[0][date_col]
    if pd.isna(val):
        return None
    return pd.to_datetime(val).strftime("%Y-%m-%d")


def json_dumps_compact(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def write_csv_rows(path: Path, rows: List[Dict], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
