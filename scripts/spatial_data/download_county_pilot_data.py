"""Download and register public inputs for the year-specific Qihe pilot.

The default smoke-test year is 2020 because CACD, CN_Wheat10 and the
ChinaCP-Wheat10m wheat-maize rotation product are all available for that
year.  The previous 2024 inputs remain reproducible under
``county_pilot_2024`` but are not the default because CACD stops at 2023.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import rasterio
import requests
from rasterio.windows import from_bounds


ROOT = Path(__file__).resolve().parents[2]
BASE_RAW = ROOT / "data" / "raw" / "shandong_public"
QIHE_BOUNDS = (116.35, 36.35, 117.02, 37.08)


def source_catalog(year: int) -> tuple[Path, list[dict], list[dict]]:
    raw = BASE_RAW / f"county_pilot_{year}"
    common = [{
        "dataset": "DataV_Qihe_county_boundary",
        "url": "https://geo.datav.aliyun.com/areas_v3/bound/371425.json",
        "path": raw / "boundaries" / "371425_qihe_datav.geojson",
        "role": "county boundary",
        "year": "current endpoint",
        "license_note": "Public DataV endpoint; not an authoritative survey boundary",
    }]
    if year == 2020:
        remote = common + [
            {
                "dataset": "CACD_v1_2020",
                "url": "https://zenodo.org/records/16927779/files/CACD-v1-2020.tif?download=1",
                "path": raw / "cropland" / "CACD-v1-2020_Qihe_subset.tif",
                "role": "same-year annual cropland/non-cropland mask (class 1)",
                "year": "2020",
                "license_note": "CC BY 4.0; DOI 10.5281/zenodo.16927779",
                "mode": "cog_subset",
            },
            {
                "dataset": "CN_Wheat10_2020",
                "url": "https://ndownloader.figshare.com/files/53951060",
                "path": raw / "crop_masks" / "CN-Wheat10_2020.rar",
                "role": "annual 10 m wheat distribution archive",
                "year": "2020",
                "license_note": "CC BY 4.0; DOI 10.6084/m9.figshare.28852220.v2",
            },
            {
                "dataset": "ChinaCP_Wheat10m_wheat_maize_2020",
                "url": "https://ndownloader.figshare.com/files/53168510",
                "path": raw / "crop_masks" / "wheat_maize_china_2020.zip",
                "role": "same-year 10 m wheat-maize rotation mask",
                "year": "2020",
                "license_note": "CC BY 4.0; DOI 10.6084/m9.figshare.28646687.v3",
            },
        ]
        local = [{
            "dataset": "Shandong_statistical_yearbook_2021_city_crop_table",
            "path": BASE_RAW / "statistics" / "yearbook_city_crops" / "shandong_yearbook_2021_13-09.xls",
            "role": "2020 prefecture-level crop area/production/yield validation context",
            "year": "2020",
            "origin": "Shandong Statistical Yearbook 2021; city-level, not a county label",
        }, {
            "dataset": "Qihe_2020_wheat_table_6_7_screenshot",
            "path": raw / "小麦.png",
            "role": "Qihe County wheat area, production and yield validation",
            "year": "2020",
            "origin": "2020 Dezhou Statistical Yearbook, table 6-7, p.116; official index https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html",
        }, {
            "dataset": "Qihe_2020_maize_table_6_7_screenshot",
            "path": raw / "玉米.png",
            "role": "Qihe County maize area, production and yield validation",
            "year": "2020",
            "origin": "2020 Dezhou Statistical Yearbook, table 6-7, p.116; official index https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html",
        }, {
            "dataset": "Shandong_2020_maize_classification_v1",
            "path": raw / "classified-Shandong-maize-2020-WGS84-v1.tif",
            "role": "2020 maize binary classification; class 1=maize",
            "year": "2020",
            "origin": "National Ecosystem Science Data Center: 2001-2024 China maize distribution dataset",
            "source_url": "https://www.nesdc.org.cn/sdo/detail?id=651403fd7e281774b9b5da68",
            "doi": "10.57760/sciencedb.08490",
            "license_note": "CC BY-NC 4.0; cite Peng et al. (2023), Scientific Data 10:658",
        }]
    elif year == 2024:
        remote = common + [
            {
                "dataset": "CACD_v1_2023",
                "url": "https://zenodo.org/records/16927779/files/CACD-v1-2023.tif?download=1",
                "path": raw / "cropland" / "CACD-v1-2023_Qihe_subset.tif",
                "role": "t-1 cropland fallback for the 2024 archive",
                "year": "2023",
                "license_note": "CC BY 4.0; one-year gap must remain explicit",
                "mode": "cog_subset",
            },
        ]
        local = []
    else:
        raise ValueError(f"unsupported pilot year: {year}")
    return raw, remote, local


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"records": []}


def download(source: dict, manifest: Path, force: bool = False) -> dict:
    path = source["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    relpath = path.relative_to(ROOT).as_posix()
    previous = next((r for r in load_manifest(manifest)["records"] if r.get("path") == relpath), None)
    if path.exists() and not force and previous and previous.get("sha256") == sha256(path):
        print(f"SKIP verified: {relpath}")
        return previous
    if path.exists() and not force and previous is None:
        # Recover cleanly when a prior run completed this file but was stopped
        # before the final manifest write.
        print(f"REGISTER existing unmanifested file: {relpath}")
        return record_for(source, path, "registered_utc")
    if source.get("mode") == "cog_subset":
        return download_cog_subset(source)

    partial = path.with_suffix(path.suffix + ".part")
    headers: dict[str, str] = {}
    mode = "wb"
    if partial.exists() and not force:
        headers["Range"] = f"bytes={partial.stat().st_size}-"
        mode = "ab"
    response = requests.get(source["url"], stream=True, timeout=(30, 300), headers=headers)
    if mode == "ab" and response.status_code != 206:
        mode = "wb"
    response.raise_for_status()
    with partial.open(mode) as stream:
        for block in response.iter_content(1024 * 1024):
            if block:
                stream.write(block)
    partial.replace(path)
    return record_for(source, path, "downloaded_utc")


def download_cog_subset(source: dict) -> dict:
    path = source["path"]
    tmp = path.with_suffix(path.suffix + ".tmp")
    with rasterio.open(source["url"]) as src:
        window = from_bounds(*QIHE_BOUNDS, transform=src.transform).round_offsets().round_lengths()
        data = src.read(1, window=window)
        profile = src.profile.copy()
        profile.update(width=data.shape[1], height=data.shape[0], transform=src.window_transform(window),
                       compress="DEFLATE", tiled=True, blockxsize=512, blockysize=512)
        with rasterio.open(tmp, "w", **profile) as dst:
            dst.write(data, 1)
    tmp.replace(path)
    record = record_for(source, path, "downloaded_utc")
    record["subset_bounds_epsg4326"] = list(QIHE_BOUNDS)
    return record


def record_for(source: dict, path: Path, timestamp_key: str) -> dict:
    record = {k: v for k, v in source.items() if k != "path"}
    record.update({
        "path": path.relative_to(ROOT).as_posix(),
        timestamp_key: datetime.now(timezone.utc).isoformat(),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    })
    print(f"REGISTERED: {record['path']} ({record['bytes']} bytes)")
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, choices=[2020, 2024], default=2020)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    raw, remote_sources, local_sources = source_catalog(args.year)
    manifest = raw / "download_manifest.json"
    by_path = {r["path"]: r for r in load_manifest(manifest).get("records", [])}
    for source in remote_sources:
        record = download(source, manifest, args.force)
        by_path[record["path"]] = record
    for source in local_sources:
        if source["path"].exists():
            relpath = source["path"].relative_to(ROOT).as_posix()
            previous = by_path.get(relpath)
            if previous and previous.get("sha256") == sha256(source["path"]):
                record = {**previous, **{k: v for k, v in source.items() if k != "path"}}
                print(f"SKIP verified local: {relpath}")
            else:
                record = record_for(source, source["path"], "registered_utc")
            by_path[record["path"]] = record
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"pilot": f"Qihe County {args.year}", "records": list(by_path.values())},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Manifest: {manifest.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
