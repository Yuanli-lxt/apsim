# -*- coding: utf-8 -*-
"""
直接解析 APSIM Classic .apsim 文件中的 output/report 模块和输出变量。

本脚本只读 baseline .apsim，不会修改原文件。由于 APSIM Classic 不同作物
模块的变量名可能不同，脚本会生成建议新增变量清单，但不会自动把不确定
变量写入 .apsim。
"""

from __future__ import annotations

import argparse
import csv
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

try:
    from lxml import etree
except ImportError as exc:  # pragma: no cover
    raise SystemExit("缺少 lxml，请先运行：pip install lxml") from exc


BASELINE_APSIM = Path(r"F:\APSIM710-r4221\process_bio\modified_from_truth.apsim")
WORK_DIR = Path(r"F:\APSIM710-r4221\process_bio\sobol")
LOG_DIR = WORK_DIR / "logs"

INVENTORY_CSV = WORK_DIR / "apsim_output_variable_inventory.csv"
INVENTORY_MD = WORK_DIR / "apsim_output_variable_inventory.md"
SEARCH_CSV = WORK_DIR / "apsim_target_variable_search_results.csv"
RECOMMENDED_CSV = WORK_DIR / "recommended_apsim_output_variables_to_add.csv"
TEST_COPY = WORK_DIR / "modified_from_truth_extended_outputs_test.apsim"

NODE_KEYWORDS = ("output", "report", "variable", "event", "file", "frequency")

TARGET_KEYWORDS = {
    "yield_components": [
        "grain_number",
        "grain number",
        "grain_no",
        "grain_num",
        "grainnum",
        "grain_weight",
        "grain weight",
        "grain_wt",
        "grain wt",
        "grain_size",
        "grain size",
        "max_grain_size",
        "kernel_number",
        "kernel number",
        "kernel_weight",
        "kernel weight",
        "grains_per_m2",
        "grain_no_per_m2",
        "yield",
        "grain_yield",
    ],
    "water_balance_and_wue": [
        "water_use_efficiency",
        "WUE",
        "wue",
        "evapotranspiration",
        "ET",
        "et",
        "transpiration",
        "soil evaporation",
        "evaporation",
        "es",
        "eo",
        "runoff",
        "drain",
        "drainage",
        "irrigation",
        "rainfall",
        "rain",
        "sw_dep",
        "SoilWater",
        "sw",
        "water",
    ],
}


RECOMMENDED_ROWS = [
    # crop, purpose, candidate_variable_name, priority, reason, must_test, notes
    ("wheat", "wheat yield components", "wheat.grain_no", "high", "候选小麦籽粒数/单位面积籽粒数变量，用于 grain_number 扩展分析", "TRUE", "APSIM Classic 变量名需在 GUI 变量浏览器中确认"),
    ("wheat", "wheat yield components", "wheat.grain_number", "high", "候选小麦籽粒数变量", "TRUE", "如果变量不存在，APSIM 运行会在日志中报错"),
    ("wheat", "wheat yield components", "wheat.grain_wt", "high", "候选小麦粒重变量，用于 grain_weight 扩展分析", "TRUE", "需确认单位，可能为单粒重或群体平均粒重"),
    ("wheat", "wheat yield components", "wheat.grain_size", "medium", "候选小麦粒大小变量", "TRUE", "与 cultivar 参数 max_grain_size 不同，需确认输出含义"),
    ("maize", "maize yield components", "maize.grain_no", "high", "候选玉米籽粒数/单位面积籽粒数变量", "TRUE", "APSIM Classic 变量名需在 GUI 中确认"),
    ("maize", "maize yield components", "maize.grain_number", "high", "候选玉米籽粒数变量", "TRUE", "如果变量不存在，先删除后重测"),
    ("maize", "maize yield components", "maize.grain_wt", "high", "候选玉米粒重变量", "TRUE", "需确认单位"),
    ("maize", "maize yield components", "maize.grain_size", "medium", "候选玉米粒大小变量", "TRUE", "需确认是否为实际输出变量"),
    ("both", "water balance", "evapotranspiration", "high", "WUE 计算最直接的分母变量", "TRUE", "若不存在，尝试作物蒸腾 + 土壤蒸发"),
    ("both", "water balance", "transpiration", "high", "可用于蒸腾效率或与 soil evaporation 合成 ET", "TRUE", "可能需要 crop 前缀，如 wheat.transpiration"),
    ("both", "water balance", "soil_evaporation", "high", "与 transpiration 共同估算 ET", "TRUE", "APSIM Classic 中也可能叫 es、SoilEvap、evaporation"),
    ("both", "water balance", "drainage", "medium", "水分平衡检查变量", "TRUE", "用于核查 ET 近似计算"),
    ("both", "water balance", "irrigation", "medium", "水分输入变量", "TRUE", "若试验无灌溉可为空，但建议输出以便追溯"),
    ("both", "water balance", "runoff", "medium", "已有 runoff，可继续保留用于水分平衡检查", "FALSE", "当前 .apsim 中已有 runoff as SurfaceRunoff"),
    ("both", "water balance", "Rain", "medium", "已有降雨输出，用于水分平衡检查", "FALSE", "当前 .apsim 中已有 Rain as Rainfall"),
    ("both", "WUE calculation", "grain_yield / evapotranspiration", "high", "计算 water_use_efficiency_yield", "TRUE", "grain_yield 和 ET 的时间尺度必须一致"),
    ("both", "WUE calculation", "biomass / evapotranspiration", "medium", "计算 water_use_efficiency_biomass", "TRUE", "biomass 和 ET 的时间尺度必须一致"),
    ("both", "existing core outputs", "yield", "high", "当前 Harvest 输出模块已有核心产量变量", "FALSE", "继续保留"),
    ("both", "existing core outputs", "biomass", "high", "当前 Harvest 输出模块已有核心生物量变量", "FALSE", "继续保留"),
    ("both", "existing core outputs", "wheat.lai / maize.lai", "high", "当前 Phases 输出模块已有 LAI 变量", "FALSE", "继续保留"),
]


@dataclass
class XmlDoc:
    tree: etree._ElementTree
    encoding_used: str
    recovered: bool


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / "11_search_apsim_output_variables.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, mode="w", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def read_xml(path: Path) -> XmlDoc:
    if not path.exists():
        raise FileNotFoundError(f"APSIM 文件不存在：{path}")

    data = path.read_bytes()
    errors: list[str] = []
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = data.decode(enc)
            parser = etree.XMLParser(remove_blank_text=False, recover=False, huge_tree=True)
            tree = etree.ElementTree(etree.fromstring(text.encode("utf-8"), parser=parser))
            logging.info("XML 严格解析成功，编码：%s", enc)
            return XmlDoc(tree=tree, encoding_used=enc, recovered=False)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{enc}: {exc}")

    logging.warning("严格 XML 解析失败，尝试 lxml recover=True。错误：%s", " | ".join(errors))
    parser = etree.XMLParser(remove_blank_text=False, recover=True, huge_tree=True)
    tree = etree.ElementTree(etree.fromstring(data, parser=parser))
    return XmlDoc(tree=tree, encoding_used="bytes/recover", recovered=True)


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def clean_text(text: str | None) -> str:
    return " ".join((text or "").split())


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9_]+", text.lower())


def node_matches_interest(element: etree._Element) -> bool:
    name = local_name(element).lower()
    attr_text = " ".join([str(k).lower() + " " + str(v).lower() for k, v in element.attrib.items()])
    text = f"{name} {attr_text}"
    return any(key in text for key in NODE_KEYWORDS)


def xml_path(tree: etree._ElementTree, element: etree._Element) -> str:
    try:
        return tree.getpath(element)
    except Exception:  # noqa: BLE001
        return ""


def child_text(element: etree._Element, child_name: str) -> str:
    wanted = child_name.lower()
    for child in element:
        if local_name(child).lower() == wanted:
            return clean_text(child.text)
    return ""


def find_output_ancestor(element: etree._Element) -> etree._Element | None:
    for anc in [element] + list(element.iterancestors()):
        lname = local_name(anc).lower()
        if "output" in lname or "report" in lname:
            return anc
    # 有些 APSIM Classic 模块可能不叫 output/report，但包含 filename/variables/events。
    for anc in list(element.iterancestors()):
        child_names = {local_name(child).lower() for child in anc}
        if {"filename", "variables"} & child_names and ("events" in child_names or "frequency" in child_names):
            return anc
    return None


def output_module_name(module: etree._Element | None) -> str:
    if module is None:
        return "unknown"
    return module.get("name") or child_text(module, "title") or local_name(module) or "unknown"


def output_file_name(module: etree._Element | None) -> str:
    if module is None:
        return ""
    filename = child_text(module, "filename") or module.get("filename") or module.get("file") or ""
    return filename


def event_or_frequency(module: etree._Element | None) -> str:
    if module is None:
        return ""
    values: list[str] = []
    for node in module.iter():
        lname = local_name(node).lower()
        if lname in {"event", "frequency"} or "frequency" in lname:
            txt = clean_text(node.text)
            if txt:
                values.append(txt)
    # 保留顺序去重
    seen: set[str] = set()
    unique = []
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return "; ".join(unique)


def variable_alias(raw: str) -> str:
    match = re.search(r"\s+as\s+(.+)$", raw, flags=re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return raw


def classify_variable(raw: str) -> str:
    lower = raw.lower()
    word_tokens = set(tokens(raw))
    notes: list[str] = []
    if "yield" in lower or lower.strip() == "yield":
        notes.append("yield")
    if "biomass" in lower:
        notes.append("biomass")
    if ".lai" in lower or lower.endswith("lai") or " lai" in lower:
        notes.append("LAI")
    if "stage" in lower or "flower" in lower or "matur" in lower or "phenolog" in lower:
        notes.append("phenology")
    if "sw_dep" in lower or "soilwater" in lower or "water" in lower or re.search(r"\bsw\s*\(", lower):
        notes.append("soil water")
    if (
        "rain" in word_tokens
        or "rainfall" in word_tokens
        or any(key in lower for key in ["runoff", "drain", "pond", "infiltration", "evapo", "transpiration", "irrig"])
    ):
        notes.append("water balance")
    if any(key in lower for key in ["grain_no", "grain number", "grain_weight", "grain weight", "grain_wt", "kernel"]):
        notes.append("yield components candidate")
    return "; ".join(notes)


def collect_inventory(doc: XmlDoc) -> tuple[list[dict[str, str]], list[etree._Element]]:
    tree = doc.tree
    rows: list[dict[str, str]] = []
    interesting_nodes: list[etree._Element] = []

    for element in tree.iter():
        if node_matches_interest(element):
            interesting_nodes.append(element)

        if local_name(element).lower() == "variable":
            raw = clean_text(element.text)
            module = find_output_ancestor(element)
            rows.append(
                {
                    "output_module_name": output_module_name(module),
                    "output_file_name": output_file_name(module),
                    "event_or_frequency": event_or_frequency(module),
                    "variable_name": variable_alias(raw),
                    "variable_text_raw": raw,
                    "xml_path": xml_path(tree, element),
                    "parent_node": local_name(element.getparent()) if element.getparent() is not None else "",
                    "notes": classify_variable(raw),
                }
            )
    return rows, interesting_nodes


def keyword_match_type(keyword: str, value: str) -> str | None:
    key = keyword.strip()
    val = value.strip()
    if not key or not val:
        return None
    if val == key:
        return "exact"
    if val.lower() == key.lower():
        return "case_insensitive"

    key_lower = key.lower()
    val_lower = val.lower()
    key_norm = norm(key)
    val_norm = norm(value)

    token_only_keywords = {"et", "es", "sw", "eo", "rain", "transpiration"}
    # et/es/sw/rain 这类关键词只允许作为独立 token 或规范化完全匹配，避免匹配 currentState/grain 等。
    if len(key_norm) <= 2 or key_lower in token_only_keywords:
        if key_lower in tokens(val):
            return "contains"
        if val_norm == key_norm:
            return "fuzzy"
        return None

    if key_lower in val_lower:
        return "contains"
    if key_norm and key_norm in val_norm:
        return "fuzzy"
    return None


def search_targets(inventory_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for group, keywords in TARGET_KEYWORDS.items():
        for keyword in keywords:
            for row in inventory_rows:
                raw = row["variable_text_raw"]
                alias = row["variable_name"]
                match_type = keyword_match_type(keyword, raw) or keyword_match_type(keyword, alias)
                if match_type:
                    results.append(
                        {
                            "target_group": group,
                            "keyword": keyword,
                            "matched_variable": raw,
                            "output_module_name": row["output_module_name"],
                            "output_file_name": row["output_file_name"],
                            "xml_path": row["xml_path"],
                            "match_type": match_type,
                            "notes": row["notes"],
                        }
                    )
    return results


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logging.info("已写出：%s", path)


def write_recommended_csv(path: Path) -> None:
    fieldnames = ["crop", "purpose", "candidate_variable_name", "priority", "reason", "must_test", "notes"]
    rows = [dict(zip(fieldnames, row)) for row in RECOMMENDED_ROWS]
    write_csv(path, rows, fieldnames)


def contains_any(rows: Iterable[dict[str, str]], keywords: Iterable[str]) -> bool:
    compiled = [norm(k) for k in keywords]
    for row in rows:
        text = norm(row.get("variable_text_raw", "") + " " + row.get("variable_name", ""))
        if any(k and k in text for k in compiled):
            return True
    return False


def summarize_modules(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], list[dict[str, str]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (row["output_module_name"], row["output_file_name"], row["event_or_frequency"])
        grouped.setdefault(key, []).append(row)
    return grouped


def write_markdown_report(
    path: Path,
    doc: XmlDoc,
    inventory_rows: list[dict[str, str]],
    search_rows: list[dict[str, str]],
    interesting_nodes: list[etree._Element],
) -> None:
    grouped = summarize_modules(inventory_rows)
    explicit_grouped = {
        key: value
        for key, value in grouped.items()
        if key[0].lower() != "unknown" or key[1] or key[2]
    }
    non_output_rows = [
        row
        for row in inventory_rows
        if row["output_module_name"].lower() == "unknown" and not row["output_file_name"] and not row["event_or_frequency"]
    ]
    has_grain_number = contains_any(inventory_rows, ["grain_number", "grain_no", "grainnum", "grain number", "kernel_number", "kernel number", "grains_per_m2"])
    has_grain_weight = contains_any(inventory_rows, ["grain_weight", "grain_wt", "grain weight", "grain wt", "kernel_weight", "kernel weight", "grain_size", "grain size"])
    has_yield = contains_any(inventory_rows, ["yield", "grain_yield"])
    has_biomass = contains_any(inventory_rows, ["biomass"])
    has_lai = contains_any(inventory_rows, ["lai"])
    has_et = contains_any(inventory_rows, ["evapotranspiration", "transpiration", "soil evaporation"])
    has_wue = contains_any(inventory_rows, ["water_use_efficiency", "wue"])
    has_water_balance = contains_any(inventory_rows, ["rain", "runoff", "drain", "sw_dep", "soilwater", "water", "irrigation"])

    lines: list[str] = []
    lines.append("# APSIM output/report 变量清单诊断")
    lines.append("")
    lines.append(f"- APSIM 文件：`{BASELINE_APSIM}`")
    lines.append(f"- XML 编码/解析方式：`{doc.encoding_used}`")
    lines.append(f"- 是否使用 recover=True：`{doc.recovered}`")
    lines.append(f"- 模糊匹配到的 output/report/variable/event/file/frequency 相关节点数：`{len(interesting_nodes)}`")
    lines.append(f"- 识别到明确 output/report 模块数：`{len(explicit_grouped)}`")
    if non_output_rows:
        lines.append(f"- 另有 `{len(non_output_rows)}` 条 `<variable>` 位于非 output/report 位置，已保留在 CSV 中但不作为输出模块统计。")
    lines.append("")

    lines.append("## 1. output/report 模块")
    for idx, ((module_name, file_name, events), module_rows) in enumerate(explicit_grouped.items(), start=1):
        lines.append("")
        lines.append(f"### {idx}. {module_name}")
        lines.append(f"- 输出文件：`{file_name or 'unknown'}`")
        lines.append(f"- 事件/频率：`{events or 'unknown'}`")
        lines.append(f"- 变量数：`{len(module_rows)}`")
        for row in module_rows:
            note = f"；{row['notes']}" if row["notes"] else ""
            lines.append(f"  - `{row['variable_text_raw']}`{note}")

    if non_output_rows:
        lines.append("")
        lines.append("### 非 output/report 位置的 variable 节点")
        for row in non_output_rows:
            lines.append(f"- `{row['variable_text_raw']}`，XML 路径：`{row['xml_path']}`")

    lines.append("")
    lines.append("## 2. 关键变量类别判断")
    lines.append(f"- yield / grain_yield：`{'存在' if has_yield else '未发现'}`")
    lines.append(f"- biomass：`{'存在' if has_biomass else '未发现'}`")
    lines.append(f"- LAI：`{'存在' if has_lai else '未发现'}`")
    lines.append(f"- soil water / water balance 基础变量：`{'存在' if has_water_balance else '未发现'}`")
    lines.append(f"- grain_number 或相近变量：`{'存在' if has_grain_number else '未发现'}`")
    lines.append(f"- grain_weight 或相近变量：`{'存在' if has_grain_weight else '未发现'}`")
    lines.append(f"- WUE 直接变量：`{'存在' if has_wue else '未发现'}`")
    lines.append(f"- ET / transpiration / soil evaporation：`{'存在' if has_et else '未发现'}`")
    lines.append("")

    lines.append("## 3. 目标关键词匹配结果摘要")
    if search_rows:
        for row in search_rows:
            lines.append(
                f"- `{row['keyword']}` -> `{row['matched_variable']}` "
                f"({row['match_type']}, 模块 `{row['output_module_name']}`, 文件 `{row['output_file_name']}`)"
            )
    else:
        lines.append("- 未匹配到目标关键词。")

    lines.append("")
    lines.append("## 4. 是否需要修改 output/report")
    if has_grain_number:
        lines.append("- grain_number 或相近变量已在 .apsim 输出模块中出现，可进一步确认输出文件和列名。")
    else:
        lines.append("- 未发现 grain_number / grain_no / kernel_number 等变量；若要分析籽粒数，需要新增 APSIM output/report 变量并小样本测试。")
    if has_grain_weight:
        lines.append("- grain_weight 或相近变量已在 .apsim 输出模块中出现，可进一步确认变量含义和单位。")
    else:
        lines.append("- 未发现 grain_weight / grain_wt / kernel_weight / grain_size 等变量；若要分析粒重，需要新增 APSIM output/report 变量并小样本测试。")
    if has_wue:
        lines.append("- 已发现 WUE 直接变量，可检查其单位和计算定义。")
    elif has_et and has_yield:
        lines.append("- 未发现 WUE 直接变量，但存在 yield 与 ET/蒸腾相关变量，理论上可计算 WUE。")
    else:
        lines.append("- 未发现 WUE 直接变量，也未发现可靠的 ET / transpiration / soil evaporation 输出；当前不能可靠计算 WUE。")
    if has_water_balance and not has_et:
        lines.append("- 当前存在 rain/runoff/soil water 等水分平衡基础变量，但缺 ET 或 transpiration，不能仅凭这些列稳健计算 WUE。")

    lines.append("")
    lines.append("## 5. 建议")
    lines.append("- 不建议自动把不确定变量直接写入 baseline .apsim。")
    lines.append("- 建议先在 APSIM Classic GUI 的变量浏览器中确认作物模块可用变量名。")
    lines.append("- 先少量添加 grain number、grain weight、ET/transpiration/soil evaporation 相关候选变量，然后运行 `--limit 5` 测试。")
    lines.append("- 测试通过后，再决定是否复用当前 13 个参数和 N=128 样本做完整扩展分析。")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logging.info("已写出：%s", path)


def make_safe_copy(source: Path, dest: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    shutil.copy2(source, dest)
    logging.info("已生成只读安全测试副本（未新增变量）：%s", dest)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="搜索 APSIM Classic .apsim output/report 输出变量。")
    parser.add_argument("--apsim", default=str(BASELINE_APSIM), help="APSIM Classic .apsim 文件路径")
    parser.add_argument("--work-dir", default=str(WORK_DIR), help="输出工作目录")
    parser.add_argument(
        "--make-test-copy",
        action="store_true",
        help="生成 modified_from_truth_extended_outputs_test.apsim 安全副本，但不自动新增不确定变量",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    global BASELINE_APSIM, WORK_DIR, LOG_DIR, INVENTORY_CSV, INVENTORY_MD, SEARCH_CSV, RECOMMENDED_CSV, TEST_COPY

    BASELINE_APSIM = Path(args.apsim)
    WORK_DIR = Path(args.work_dir)
    LOG_DIR = WORK_DIR / "logs"
    INVENTORY_CSV = WORK_DIR / "apsim_output_variable_inventory.csv"
    INVENTORY_MD = WORK_DIR / "apsim_output_variable_inventory.md"
    SEARCH_CSV = WORK_DIR / "apsim_target_variable_search_results.csv"
    RECOMMENDED_CSV = WORK_DIR / "recommended_apsim_output_variables_to_add.csv"
    TEST_COPY = WORK_DIR / "modified_from_truth_extended_outputs_test.apsim"

    setup_logging()
    logging.info("开始解析 APSIM 文件：%s", BASELINE_APSIM)

    doc = read_xml(BASELINE_APSIM)
    inventory_rows, interesting_nodes = collect_inventory(doc)
    search_rows = search_targets(inventory_rows)

    write_csv(
        INVENTORY_CSV,
        inventory_rows,
        [
            "output_module_name",
            "output_file_name",
            "event_or_frequency",
            "variable_name",
            "variable_text_raw",
            "xml_path",
            "parent_node",
            "notes",
        ],
    )
    write_csv(
        SEARCH_CSV,
        search_rows,
        [
            "target_group",
            "keyword",
            "matched_variable",
            "output_module_name",
            "output_file_name",
            "xml_path",
            "match_type",
            "notes",
        ],
    )
    write_recommended_csv(RECOMMENDED_CSV)
    write_markdown_report(INVENTORY_MD, doc, inventory_rows, search_rows, interesting_nodes)

    if args.make_test_copy:
        make_safe_copy(BASELINE_APSIM, TEST_COPY)

    grouped = summarize_modules(inventory_rows)
    explicit_count = sum(1 for key in grouped if key[0].lower() != "unknown" or key[1] or key[2])
    logging.info("完成。明确 output/report 模块数：%s；变量数：%s；目标匹配数：%s", explicit_count, len(inventory_rows), len(search_rows))


if __name__ == "__main__":
    main()
