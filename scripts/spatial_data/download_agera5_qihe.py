"""Download AgERA5 v2 daily drivers for the Qihe baseline experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import cdsapi


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "shandong_public" / "weather" / "agera5_v2"
DATASET = "sis-agrometeorological-indicators"
AREA = [37.2, 116.2, 36.2, 117.2]  # north, west, south, east
VARIABLES = {
    "tmax": {"variable": "2m_temperature", "statistic": ["24_hour_maximum"]},
    "tmin": {"variable": "2m_temperature", "statistic": ["24_hour_minimum"]},
    "rain": {"variable": "precipitation_flux"},
    "radn": {"variable": "solar_radiation_flux"},
}


def valid_zip(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and bool(archive.namelist())
    except zipfile.BadZipFile:
        return False


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_one(year: int, short_name: str, variable_request: dict, force: bool) -> dict:
    target = OUT / f"agera5_v2_qihe_{year}_{short_name}.zip"
    if valid_zip(target) and not force:
        print(f"reuse {target.name}", flush=True)
    else:
        request = {
            **variable_request,
            "year": [str(year)],
            "month": [f"{month:02d}" for month in range(1, 13)],
            "day": [f"{day:02d}" for day in range(1, 32)],
            "version": "2_0",
            "area": AREA,
        }
        print(f"download {target.name}", flush=True)
        cdsapi.Client().retrieve(DATASET, request, str(target))
        if not valid_zip(target):
            raise RuntimeError(f"Invalid CDS download: {target}")
    return {
        "year": year,
        "variable": short_name,
        "path": str(target.relative_to(ROOT)).replace("\\", "/"),
        "bytes": target.stat().st_size,
        "sha256": sha256(target),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", nargs="+", type=int, default=[2017, 2018, 2019, 2020])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent CDS requests")
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 8:
        raise ValueError("--workers must be between 1 and 8")
    OUT.mkdir(parents=True, exist_ok=True)
    downloads = []
    tasks = [(year, short_name, request) for year in sorted(set(args.years)) for short_name, request in VARIABLES.items()]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(download_one, year, short_name, request, args.force): (year, short_name)
            for year, short_name, request in tasks
        }
        for future in as_completed(futures):
            downloads.append(future.result())
    manifest_path = OUT / "download_manifest.json"
    existing_files = []
    if manifest_path.exists():
        try:
            existing_files = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
        except (json.JSONDecodeError, OSError):
            existing_files = []
    merged = {(int(item["year"]), item["variable"]): item for item in existing_files}
    merged.update({(int(item["year"]), item["variable"]): item for item in downloads})
    manifest = {
        "dataset": DATASET,
        "product": "AgERA5",
        "version": "2.0",
        "dataset_doi": "10.24381/cds.6c68c9bb",
        "area_nwse": AREA,
        "downloaded_or_verified": date.today().isoformat(),
        "variables": VARIABLES,
        "years": sorted({key[0] for key in merged}),
        "files": [merged[key] for key in sorted(merged)],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
