"""Download the official 2021 Dezhou Statistical Yearbook (2020 data)."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "raw" / "shandong_public" / "statistics" / "dezhou_yearbook_2021.pdf"
SOURCE_PAGE = "https://dztj.dezhou.gov.cn/n54289016/n54289061/n54289125/c68576092/content.html"
PDF_URL = "http://www.dezhou.gov.cn/pdf/tjnj2021.pdf"


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if not OUT.exists() or OUT.stat().st_size == 0:
        request = urllib.request.Request(PDF_URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=120) as response, OUT.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    manifest = {
        "dataset": "2021 Dezhou Statistical Yearbook",
        "statistical_year": 2020,
        "publisher": "Dezhou Municipal Bureau of Statistics",
        "source_page": SOURCE_PAGE,
        "pdf_url": PDF_URL,
        "downloaded_or_verified": date.today().isoformat(),
        "path": str(OUT.relative_to(ROOT)).replace("\\", "/"),
        "bytes": OUT.stat().st_size,
        "sha256": digest,
        "target_table": "6-7 各县市区农业主要产品生产情况",
    }
    (OUT.parent / "dezhou_yearbook_2021_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
