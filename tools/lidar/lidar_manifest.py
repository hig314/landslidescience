"""The lidar manifest, read in one place.

`datasets.json` is tracked and public. `datasets.local.json` (same shape,
gitignored) holds surveys that must not leave this machine: the repository
is public, and a manifest row alone publishes a survey's name and source.
Every tool that reads the manifest goes through `load()`, so a local-only
survey builds, catalogues and audits like any other here and simply does not
exist anywhere else.

A local entry must carry "dev_only": true, so that even on this machine a
catalogue built without --include-dev leaves it out.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "datasets.json"
LOCAL = HERE / "datasets.local.json"


def load():
    """The manifest dict ({"datasets": [...], ...}) with local entries appended."""
    data = json.loads(MANIFEST.read_text())
    if LOCAL.exists():
        ids = {d["id"] for d in data["datasets"]}
        for d in json.loads(LOCAL.read_text())["datasets"]:
            if d["id"] in ids:
                raise SystemExit(f"{LOCAL.name}: id {d['id']!r} is already in {MANIFEST.name}")
            if not d.get("dev_only"):
                raise SystemExit(f"{LOCAL.name}: {d['id']!r} must have \"dev_only\": true")
            data["datasets"].append(d)
    return data
