#!/usr/bin/env python3
"""Regenerate the browser globe's world data.

Run after changing the universe, the supply links or the event catalogue:

    PYTHONPATH=platform python platform/build_world.py

Writes `public/world/world.json` and re-inlines it into
`public/world/index.html`, which is the page the Cloudflare Worker serves.
The JSON is inlined rather than fetched because the artifact sandbox blocks
same-origin data fetches, and a single self-contained file deploys either way.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "platform"))

from modules.world_export import export_static_world  # noqa: E402

TARGET_DIR = ROOT / "public" / "world"
JSON_PATH = TARGET_DIR / "world.json"
HTML_PATH = TARGET_DIR / "index.html"

# The inlined payload sits between these markers in the page.
PATTERN = re.compile(r"(const WORLD = )(.*?)(;\n)", re.S)


def main() -> int:
    payload = export_static_world()
    blob = json.dumps(payload, separators=(",", ":"))

    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(blob)

    if not HTML_PATH.exists():
        print(f"! {HTML_PATH} assente: scritto solo {JSON_PATH.name}")
        return 1

    html = HTML_PATH.read_text()
    new_html, count = PATTERN.subn(lambda m: m.group(1) + blob + m.group(3), html, count=1)
    if count != 1:
        print("! impossibile trovare 'const WORLD = ...;' in index.html")
        return 1
    HTML_PATH.write_text(new_html)

    print(f"✓ {JSON_PATH.relative_to(ROOT)}  {len(blob) / 1024:.1f} KB")
    print(f"✓ {HTML_PATH.relative_to(ROOT)}  {len(new_html) / 1024:.1f} KB")
    print(f"  {len(payload['nodes'])} aziende · {len(payload['edges'])} archi · "
          f"{len(payload['countries'])} paesi · "
          f"{len(payload['eventCatalogue'])} template eventi")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
