#!/usr/bin/env python3
"""
Fetch a fresh Starlink TLE snapshot from CelesTrak and write it under data/.

Usage:
  python scripts/fetch_tles.py
  python scripts/fetch_tles.py --max 50 --out data/starlink_live.tle

Requires network access. CelesTrak may rate-limit; do not hammer the endpoint.
"""

from __future__ import annotations

import argparse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CELESTRAK_STARLINK = (
    "https://celestrak.org/NORAD/elements/gp.php?GROUP=starlink&FORMAT=tle"
)


def fetch(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "satcom-protocol/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Starlink TLEs from CelesTrak")
    ap.add_argument("--url", default=CELESTRAK_STARLINK)
    ap.add_argument("--max", type=int, default=None, help="Max satellites to keep")
    ap.add_argument(
        "--out",
        default="data/starlink_live.tle",
        help="Output path relative to repo root",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent
    out = root / args.out
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"Fetching {args.url} ...")
    text = fetch(args.url)
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]

    blocks = []
    i = 0
    while i < len(lines):
        if not lines[i].startswith("1 ") and not lines[i].startswith("2 "):
            name = lines[i]
            if i + 2 < len(lines) and lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
                blocks.append((name, lines[i + 1], lines[i + 2]))
                i += 3
                continue
        i += 1

    if args.max is not None:
        blocks = blocks[: args.max]

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    header = f"# Starlink TLE snapshot from CelesTrak\n# fetched_utc: {stamp}\n# count: {len(blocks)}\n"
    body = "\n".join(f"{n}\n{l1}\n{l2}" for n, l1, l2 in blocks) + "\n"
    out.write_text(header + body, encoding="utf-8")
    print(f"Wrote {len(blocks)} satellites to {out}")


if __name__ == "__main__":
    main()
