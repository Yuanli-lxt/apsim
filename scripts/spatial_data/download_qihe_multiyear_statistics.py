"""Download and checksum official Dezhou yearbooks for Qihe 2015-2023 data."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "shandong_public" / "statistics"
INDEX_URL = "https://dztj.dezhou.gov.cn/n3100530/n38260319/index.html"
YEARBOOKS = {
    2016: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c97852113/content.html", "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c97852113/part/100748230.pdf"),
    2017: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c29924076/content.html", "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c29924076/part/40739760.pdf"),
    2018: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c96128415/content.html", "http://www.dezhou.gov.cn/pdf/tjnj2018.pdf"),
    2019: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c53173660/content.html", "http://www.dezhou.gov.cn/pdf/2019tjnj.pdf"),
    2020: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c60991754/content.html", "http://www.dezhou.gov.cn/pdf/tjnj2020.pdf"),
    2021: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c68576092/content.html", "http://www.dezhou.gov.cn/pdf/tjnj2021.pdf"),
    2022: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c79459606/content.html", "http://www.dezhou.gov.cn/pdf/tjnj-2022.pdf"),
    2023: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c87039976/content.html", "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c87039976/part/87039981.pdf"),
    2024: ("https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c92860358/content.html", "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c92860358/part/92860363.pdf"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    for publication_year, (source_page, pdf_url) in YEARBOOKS.items():
        path = OUT / f"dezhou_yearbook_{publication_year}.pdf"
        status = "verified_existing"
        if not path.exists() or path.stat().st_size == 0:
            request = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=300) as response, path.open("wb") as target:
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
            status = "downloaded"
        records.append({
            "statistical_year": publication_year - 1,
            "publication_year": publication_year,
            "source_index": INDEX_URL,
            "source_page": source_page,
            "pdf_url": pdf_url,
            "path": path.relative_to(ROOT).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
            "status": status,
        })
    manifest = {
        "dataset": "Official Dezhou yearbooks supporting Qihe 2015-2023 wheat and maize statistics",
        "verified_at": datetime.now().astimezone().isoformat(),
        "records": records,
    }
    target = OUT / "dezhou_yearbooks_2016_2024_manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
