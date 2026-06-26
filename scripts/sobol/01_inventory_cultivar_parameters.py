"""
01 读取 APSIM Classic .apsim，并扫描 cultivar / variety / genotype 相关参数。

输出:
F:\APSIM710-r4221\process_bio\sobol\cultivar_parameter_inventory.csv
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set

import pandas as pd

from sobol_common import (
    BASE_APSIM,
    CROP_XML_FILES,
    INVENTORY_CSV,
    MODEL_DIR,
    backup_file,
    clean_text,
    ensure_dirs,
    infer_crop_from_text,
    make_parameter_key,
    parse_xml,
    setup_logging,
    split_numeric_vector,
    xml_path,
)


def add_row(rows: List[Dict], **kwargs) -> None:
    base = {
        "crop": "unknown",
        "cultivar": "unknown",
        "parameter_name": "",
        "baseline_value": "",
        "unit_if_available": "",
        "xml_path_or_text_location": "",
        "source_section": "",
        "notes": "",
        "source_file": "",
        "target_kind": "",
        "base_parameter_name": "",
        "value_index": "",
        "parameter_key": "",
        "is_used_cultivar": "FALSE",
    }
    base.update(kwargs)
    if not base["parameter_key"]:
        base["parameter_key"] = make_parameter_key(
            base["crop"], base["cultivar"], base["parameter_name"], base["source_section"], base["value_index"]
        )
    rows.append(base)


def scan_manager_calls(tree, rows: List[Dict]) -> Set[tuple]:
    used: Set[tuple] = set()
    for manager in tree.xpath(".//*[local-name()='manager2']"):
        manager_name = clean_text(manager.get("name")) or "unknown_manager"
        ui = manager.find("ui")
        if ui is None:
            continue
        crop_node = ui.find("crop")
        crop = infer_crop_from_text(crop_node.text if crop_node is not None else manager_name)
        for child in ui:
            tag = child.tag
            desc = clean_text(child.get("description"))
            value = clean_text(child.text)
            if tag.lower().startswith("cultivar") or "cultivar" in desc.lower():
                cultivar = value if value and value.lower() != "na" else "unknown"
                if cultivar != "unknown":
                    used.add((crop, cultivar))
                add_row(
                    rows,
                    crop=crop,
                    cultivar=cultivar,
                    parameter_name=tag,
                    baseline_value=value,
                    unit_if_available=clean_text(child.get("units") or child.get("unit")),
                    xml_path_or_text_location=f"{xml_path(child)} line={child.sourceline}",
                    source_section=f"manager2:{manager_name}",
                    notes="Manager sowing module 中调用的 cultivar 名称；不是 cultivar 生理参数。",
                    source_file=str(BASE_APSIM),
                    target_kind="manager_cultivar_call",
                    base_parameter_name=tag,
                    is_used_cultivar="TRUE" if cultivar != "unknown" else "FALSE",
                )
    return used


def scan_soil_crop_sections(tree, rows: List[Dict]) -> None:
    for soil_crop in tree.xpath(".//*[local-name()='SoilCrop']"):
        crop = clean_text(soil_crop.get("name")) or "unknown"
        for param in soil_crop:
            vals = [clean_text(x.text) for x in param if clean_text(x.text) != ""]
            value = " ".join(vals) if vals else clean_text(param.text)
            add_row(
                rows,
                crop=crop,
                cultivar="unknown",
                parameter_name=param.tag,
                baseline_value=value,
                unit_if_available=clean_text(param.get("units") or param.get("unit")),
                xml_path_or_text_location=f"{xml_path(param)} line={param.sourceline}",
                source_section="Soil/SoilCrop",
                notes="作物土壤水参数，不是 cultivar 参数；默认不进入品种 Sobol。",
                source_file=str(BASE_APSIM),
                target_kind="soil_crop_parameter",
                base_parameter_name=param.tag,
                is_used_cultivar="FALSE",
            )


def scan_embedded_cultivar_like(tree, rows: List[Dict]) -> None:
    for node in tree.xpath(".//*[@cultivar='yes' or contains(translate(local-name(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'cultivar') or contains(translate(local-name(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'genotype') or contains(translate(local-name(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'variety')]"):
        cultivar = clean_text(node.get("name")) or node.tag
        crop = infer_crop_from_text(xml_path(node))
        for param in node:
            value = clean_text(param.text)
            add_row(
                rows,
                crop=crop,
                cultivar=cultivar,
                parameter_name=param.tag,
                baseline_value=value,
                unit_if_available=clean_text(param.get("units") or param.get("unit")),
                xml_path_or_text_location=f"{xml_path(param)} line={param.sourceline}",
                source_section="embedded_cultivar_like_in_apsim",
                notes="在 .apsim 内发现的 cultivar/variety/genotype-like 节点。",
                source_file=str(BASE_APSIM),
                target_kind="embedded_cultivar_parameter",
                base_parameter_name=param.tag,
            )


def scan_external_crop_xml(rows: List[Dict], used: Set[tuple], logger) -> None:
    used_by_cultivar = {(crop.lower(), cultivar) for crop, cultivar in used}
    for crop, xml_file in CROP_XML_FILES.items():
        if not xml_file.exists():
            logger.warning("未找到外部作物 XML: %s", xml_file)
            continue
        tree = parse_xml(xml_file)
        cultivar_nodes = tree.xpath(".//*[@cultivar='yes']")
        for cultivar_node in cultivar_nodes:
            cultivar = cultivar_node.tag
            is_used = (crop, cultivar) in used_by_cultivar
            for param in cultivar_node:
                raw_value = clean_text(param.text)
                unit = clean_text(param.get("units") or param.get("unit"))
                base_name = param.tag
                vector = split_numeric_vector(raw_value)
                if vector is not None:
                    for idx, val in enumerate(vector):
                        add_row(
                            rows,
                            crop=crop,
                            cultivar=cultivar,
                            parameter_name=f"{base_name}[{idx}]",
                            baseline_value=val,
                            unit_if_available=unit,
                            xml_path_or_text_location=f"{xml_path(param)} line={param.sourceline}",
                            source_section="external_crop_xml_cultivar",
                            notes="APSIM Classic 外部 crop XML 中的 cultivar 参数；向量参数已拆分为单独元素。",
                            source_file=str(xml_file),
                            target_kind="external_cultivar_parameter",
                            base_parameter_name=base_name,
                            value_index=idx,
                            is_used_cultivar="TRUE" if is_used else "FALSE",
                        )
                else:
                    add_row(
                        rows,
                        crop=crop,
                        cultivar=cultivar,
                        parameter_name=base_name,
                        baseline_value=raw_value,
                        unit_if_available=unit,
                        xml_path_or_text_location=f"{xml_path(param)} line={param.sourceline}",
                        source_section="external_crop_xml_cultivar",
                        notes="APSIM Classic 外部 crop XML 中的 cultivar 参数。",
                        source_file=str(xml_file),
                        target_kind="external_cultivar_parameter",
                        base_parameter_name=base_name,
                        is_used_cultivar="TRUE" if is_used else "FALSE",
                    )


def main() -> None:
    ensure_dirs()
    logger = setup_logging("01_inventory_cultivar_parameters")
    if not BASE_APSIM.exists():
        raise FileNotFoundError(f"APSIM 文件不存在: {BASE_APSIM}")
    backup = backup_file(BASE_APSIM, "baseline_apsim")
    logger.info("已备份 baseline .apsim: %s", backup)
    logger.info("APSIM Classic Model 目录: %s", MODEL_DIR)

    rows: List[Dict] = []
    tree = parse_xml(BASE_APSIM)
    used = scan_manager_calls(tree, rows)
    scan_soil_crop_sections(tree, rows)
    scan_embedded_cultivar_like(tree, rows)
    scan_external_crop_xml(rows, used, logger)

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["parameter_key", "source_file", "xml_path_or_text_location"], keep="first")
    df.to_csv(INVENTORY_CSV, index=False, encoding="utf-8-sig")
    logger.info("已输出参数清单: %s", INVENTORY_CSV)
    logger.info("Manager 调用的 cultivar: %s", sorted(used))
    logger.info("清单行数: %s", len(df))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        logger = setup_logging("01_inventory_cultivar_parameters")
        logger.exception("脚本失败: %s", exc)
        raise
