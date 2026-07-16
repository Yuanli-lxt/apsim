"""Download official Dezhou yearbooks containing Qihe 2018-2020 crop data."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "data" / "raw" / "shandong_public" / "statistics"
YEARBOOKS = {
    2018: {
        "yearbook": 2019,
        "url": "http://www.dezhou.gov.cn/pdf/2019tjnj.pdf",
        "source_page": "https://www.tjnj.net/navipage-n3020030203000143.html",
        "table": "6-8 各县市区农业主要产品生产情况",
    },
    2019: {
        "yearbook": 2020,
        "url": "http://www.dezhou.gov.cn/pdf/tjnj2020.pdf",
        "source_page": "https://www.tjnj.net/navipage-n3020013223000139.html",
        "table": "6-7 各县市区农业主要产品生产情况",
    },
    2020: {
        "yearbook": 2021,
        "url": "http://www.dezhou.gov.cn/pdf/tjnj2021.pdf",
        "source_page": "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c68576092/content.html",
        "table": "6-7 各县市区农作物生产情况",
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    for statistical_year, item in YEARBOOKS.items():
        path = OUT_DIR / f"dezhou_yearbook_{item['yearbook']}.pdf"
        if not path.exists() or path.stat().st_size == 0:
            request = urllib.request.Request(item["url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=180) as response, path.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
        records.append(
            {
                "statistical_year": statistical_year,
                "yearbook": item["yearbook"],
                "publisher": "Dezhou Municipal Bureau of Statistics",
                "source_page": item["source_page"],
                "pdf_url": item["url"],
                "target_table": item["table"],
                "path": str(path.relative_to(ROOT)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "dataset": "Qihe 2018-2020 official wheat and maize statistics source yearbooks",
        "downloaded_or_verified": date.today().isoformat(),
        "records": records,
    }
    manifest_path = OUT_DIR / "dezhou_yearbooks_2019_2021_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
