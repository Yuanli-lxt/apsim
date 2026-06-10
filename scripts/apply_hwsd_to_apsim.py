#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import xml.etree.ElementTree as ET


def parse_soil_profile_csv(path: Path):
    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            depth = r["Depth"].strip()
            top_cm, bottom_cm = depth.split("-")
            rows.append(
                {
                    "top_mm": float(top_cm) * 10.0,
                    "bottom_mm": float(bottom_cm) * 10.0,
                    "BD": float(r["BD"]),
                    "AirDry": float(r["AirDry"]),
                    "LL15": float(r["LL15"]),
                    "DUL": float(r["DUL"]),
                    "SAT": float(r["SAT"]),
                    "Carbon": float(r["Carbon"]),
                    "FBiom": float(r["FBiom"]),
                    "FInert": float(r["FInert"]),
                    "PH": float(r["PH"]),
                    "NO3N": float(r["NO3N"]),
                    "NH4N": float(r["NH4N"]),
                    "crop.LL": float(r["crop.LL"]),
                    "crop.KL": float(r["crop.KL"]),
                    "crop.XF": float(r["crop.XF"]),
                }
            )
    return rows


def get_double_values(parent: ET.Element, tag: str):
    node = parent.find(tag)
    if node is None:
        return None
    out = []
    for d in node.findall("double"):
        text = (d.text or "").strip()
        if text == "":
            continue
        out.append(float(text))
    return out


def set_double_values(parent: ET.Element, tag: str, values):
    node = parent.find(tag)
    if node is None:
        node = ET.SubElement(parent, tag)
    for child in list(node):
        node.remove(child)
    for v in values:
        d = ET.SubElement(node, "double")
        d.text = f"{v:.6f}".rstrip("0").rstrip(".")


def thickness_to_intervals(thickness_mm):
    intervals = []
    z = 0.0
    for t in thickness_mm:
        t = float(t)
        intervals.append((z, z + t))
        z += t
    return intervals


def overlap_len(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def aggregate_to_target(source_rows, field, target_thickness_mm):
    target_intervals = thickness_to_intervals(target_thickness_mm)
    source_intervals = [(r["top_mm"], r["bottom_mm"]) for r in source_rows]
    source_values = [r[field] for r in source_rows]

    out = []
    for t0, t1 in target_intervals:
        num = 0.0
        den = 0.0
        for (s0, s1), v in zip(source_intervals, source_values):
            ol = overlap_len(t0, t1, s0, s1)
            if ol > 0:
                num += v * ol
                den += ol
        if den > 0:
            out.append(num / den)
        else:
            # fallback to nearest source layer by midpoint
            mid = (t0 + t1) / 2.0
            idx = min(
                range(len(source_intervals)),
                key=lambda i: abs(mid - (source_intervals[i][0] + source_intervals[i][1]) / 2.0),
            )
            out.append(source_values[idx])
    return out


def make_compare_block(name, before_vals, after_vals):
    lines = [f"[{name}]"]
    n = max(len(before_vals), len(after_vals))
    lines.append("idx\tbefore\tafter")
    for i in range(n):
        b = before_vals[i] if i < len(before_vals) else None
        a = after_vals[i] if i < len(after_vals) else None
        lines.append(f"{i+1}\t{b}\t{a}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apsim-in", required=True, type=Path)
    parser.add_argument("--soil-csv", required=True, type=Path)
    parser.add_argument("--outdir", required=True, type=Path)
    parser.add_argument("--apsim-out-name", default="modified_from_truth_hwsd.apsim")
    parser.add_argument("--crop-name", default="wheat")
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    source_rows = parse_soil_profile_csv(args.soil_csv)

    tree = ET.parse(args.apsim_in)
    root = tree.getroot()

    soil = root.find(".//Soil")
    if soil is None:
        raise ValueError("未找到 <Soil> 节点。")

    compare_parts = []

    water = soil.find("Water")
    if water is None:
        raise ValueError("未找到 <Water> 节点。")
    water_thk = get_double_values(water, "Thickness")
    if not water_thk:
        raise ValueError("<Water><Thickness> 为空。")

    for field in ["BD", "AirDry", "LL15", "DUL", "SAT"]:
        before = get_double_values(water, field) or []
        src_field = field if field != "LL15" else "LL15"
        after = aggregate_to_target(source_rows, src_field, water_thk)
        set_double_values(water, field, after)
        compare_parts.append(make_compare_block(f"Water/{field}", before, after))

    crop_name = args.crop_name.lower()
    for sc in water.findall("SoilCrop"):
        sc_name = (sc.get("name") or "").lower()
        sc_thk = get_double_values(sc, "Thickness")
        if not sc_thk:
            continue

        before_ll = get_double_values(sc, "LL") or []
        after_ll = aggregate_to_target(source_rows, "crop.LL", sc_thk)
        set_double_values(sc, "LL", after_ll)
        compare_parts.append(make_compare_block(f"Water/SoilCrop[{sc_name}]/LL", before_ll, after_ll))

        if sc_name == crop_name:
            before_kl = get_double_values(sc, "KL") or []
            after_kl = aggregate_to_target(source_rows, "crop.KL", sc_thk)
            set_double_values(sc, "KL", after_kl)
            compare_parts.append(make_compare_block(f"Water/SoilCrop[{sc_name}]/KL", before_kl, after_kl))

            before_xf = get_double_values(sc, "XF") or []
            after_xf = aggregate_to_target(source_rows, "crop.XF", sc_thk)
            set_double_values(sc, "XF", after_xf)
            compare_parts.append(make_compare_block(f"Water/SoilCrop[{sc_name}]/XF", before_xf, after_xf))

    som = soil.find("SoilOrganicMatter")
    if som is not None:
        som_thk = get_double_values(som, "Thickness")
        if som_thk:
            for tag, src in [("OC", "Carbon"), ("FBiom", "FBiom"), ("FInert", "FInert")]:
                before = get_double_values(som, tag) or []
                after = aggregate_to_target(source_rows, src, som_thk)
                set_double_values(som, tag, after)
                compare_parts.append(make_compare_block(f"SoilOrganicMatter/{tag}", before, after))

    analysis = soil.find("Analysis")
    if analysis is not None:
        a_thk = get_double_values(analysis, "Thickness")
        if a_thk:
            before = get_double_values(analysis, "PH") or []
            after = aggregate_to_target(source_rows, "PH", a_thk)
            set_double_values(analysis, "PH", after)
            compare_parts.append(make_compare_block("Analysis/PH", before, after))

    sample = soil.find("Sample[@name='InitialNitrogen']")
    if sample is not None:
        s_thk = get_double_values(sample, "Thickness")
        if s_thk:
            before_no3 = get_double_values(sample, "NO3") or []
            after_no3 = aggregate_to_target(source_rows, "NO3N", s_thk)
            set_double_values(sample, "NO3", after_no3)
            compare_parts.append(make_compare_block("Sample[InitialNitrogen]/NO3", before_no3, after_no3))

            before_nh4 = get_double_values(sample, "NH4") or []
            after_nh4 = aggregate_to_target(source_rows, "NH4N", s_thk)
            set_double_values(sample, "NH4", after_nh4)
            compare_parts.append(make_compare_block("Sample[InitialNitrogen]/NH4", before_nh4, after_nh4))

    ET.indent(tree, space="  ")
    apsim_out = args.outdir / args.apsim_out_name
    tree.write(apsim_out, encoding="utf-8", xml_declaration=False)

    compare_path = args.outdir / "soil_parameter_comparison.txt"
    with open(compare_path, "w", encoding="utf-8") as f:
        f.write("Modified soil parameters comparison (before vs after)\n")
        f.write("=" * 64 + "\n\n")
        f.write(f"Input APSIM: {args.apsim_in}\n")
        f.write(f"Input soil CSV: {args.soil_csv}\n")
        f.write(f"Output APSIM: {apsim_out}\n\n")
        f.write("\n".join(compare_parts))

    print("完成。")
    print(f"输出 APSIM: {apsim_out}")
    print(f"对比报告: {compare_path}")


if __name__ == "__main__":
    main()
