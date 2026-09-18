#!/usr/bin/env python3
"""publish_audit.py -- where every survey actually is, end to end.

    tools/lidar/publish_audit.py [--prod https://landslidescience.org]

A survey reaches a reader only if several independent things line up: the
pyramid is built, the bytes are somewhere the browser can fetch, the
catalogue the browser reads names it, and -- for a restricted survey -- the
gate lets the right people through and nobody else. Each of those is
maintained by a different step, and a mismatch between any two is invisible
from inside the step that caused it. That is exactly how the Kenai rebuild
came to be complete, correct, and unviewable, and how a gated survey could be
listed while its tiles 403.

So this asks all of them at once and prints one row per survey.

Columns
  state     public / gated / dev-only / retired, from the manifest
  built     local pyramid (P), slope (s), orthomosaic (o), archive COG (C)
  R2        the same four, as the public bucket answers for them
  cat       is it in the PUBLIC catalogue prod serves?
  gated     is it in the GATED catalogue prod serves?
  verdict   what a reader would actually experience

Exit code is 1 if any survey is in a state a reader would see as broken.
"""
import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PM_DIR = Path(os.environ.get("LIDAR_PM_OUT", ROOT / "data" / "lidar" / "pmtiles"))
COG_DIR = Path(os.environ.get("LIDAR_COG_OUT", "/Volumes/Nunatak/lidar_build/cog"))
R2 = "https://lidar.landslidescience.org"


def head(url):
    """HTTP status for a URL, without downloading it."""
    try:
        out = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-I", "--max-time", "25", url],
            capture_output=True, text=True).stdout.strip()
        return int(out or 0)
    except Exception:
        return 0


def fetch_ids(url):
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "30", url],
                             capture_output=True, text=True).stdout
        fc = json.loads(out)
        return {f.get("properties", {}).get("id") for f in fc.get("features", [])}
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", default="https://landslidescience.org")
    ap.add_argument("--no-r2", action="store_true", help="skip the R2 probes (faster)")
    a = ap.parse_args()

    manifest = json.loads((HERE / "datasets.json").read_text())["datasets"]
    public_ids = fetch_ids(f"{a.prod}/lidar/catalog.geojson")
    # The gated catalogue is auth-only, so an anonymous probe SHOULD be refused.
    # That refusal is itself a result worth reporting.
    gated_status = head(f"{a.prod}/lidar/catalog-gated.geojson")

    rows, problems = [], []
    probes = {}
    if not a.no_r2:
        want = []
        for ds in manifest:
            did = ds["id"]
            want += [f"{R2}/pmtiles/{did}.pmtiles", f"{R2}/pmtiles/{did}_slope.pmtiles",
                     f"{R2}/pmtiles/{did}_ortho.pmtiles", f"{R2}/cog/{did}.tif"]
        with ThreadPoolExecutor(12) as ex:
            probes = dict(zip(want, ex.map(head, want)))

    for ds in manifest:
        did = ds["id"]
        if ds.get("retired"):
            state = "retired"
        elif ds.get("gated"):
            state = "gated"
        elif ds.get("dev_only"):
            state = "dev-only"
        else:
            state = "public"

        local = "".join([
            "P" if (PM_DIR / f"{did}.pmtiles").is_file() else "-",
            "s" if (PM_DIR / f"{did}_slope.pmtiles").is_file() else "-",
            "o" if (PM_DIR / f"{did}_ortho.pmtiles").is_file() else "-",
            "C" if (COG_DIR / f"{did}.tif").is_file() else "-",
        ])
        if a.no_r2:
            r2 = "????"
        else:
            r2 = "".join([
                "P" if probes.get(f"{R2}/pmtiles/{did}.pmtiles") == 200 else "-",
                "s" if probes.get(f"{R2}/pmtiles/{did}_slope.pmtiles") == 200 else "-",
                "o" if probes.get(f"{R2}/pmtiles/{did}_ortho.pmtiles") == 200 else "-",
                "C" if probes.get(f"{R2}/cog/{did}.tif") == 200 else "-",
            ])

        in_cat = None if public_ids is None else (did in public_ids)

        # What would a reader actually get?
        verdict = "ok"
        if state == "retired":
            verdict = "retired" if not in_cat else "RETIRED BUT STILL LISTED"
        elif state == "dev-only":
            verdict = "dev only" if not in_cat else "DEV-ONLY BUT LISTED PUBLICLY"
        elif state == "gated":
            if in_cat:
                verdict = "GATED BUT IN THE PUBLIC CATALOGUE"
            elif local[0] != "P":
                verdict = "gated, pyramid not built"
            else:
                verdict = "gated, served from the droplet"
        else:  # public
            if not in_cat:
                verdict = "built, NOT YET PUBLISHED"
            elif r2[0] != "P" and not a.no_r2:
                verdict = "LISTED BUT NOT ON R2 — reader sees nothing"
            elif local[2] == "o" and r2[2] != "o" and not a.no_r2:
                verdict = "listed; ortho built but not uploaded"
            else:
                verdict = "ok"
        if verdict.isupper() or "NOT" in verdict:
            problems.append((did, verdict))
        rows.append((did, state, local, r2, in_cat, verdict))

    w = max(len(r[0]) for r in rows) + 1
    print(f"\n  {'survey':{w}} {'state':9} {'built':6} {'R2':5} {'cat':4} verdict")
    print(f"  {'-' * w} {'-' * 9} {'-' * 6} {'-' * 5} {'-' * 4} {'-' * 40}")
    for did, state, local, r2, in_cat, verdict in rows:
        flag = "?" if in_cat is None else ("yes" if in_cat else "no")
        print(f"  {did:{w}} {state:9} {local:6} {r2:5} {flag:4} {verdict}")

    print("\n  built = Pyramid / slope / ortho / COG present locally; R2 = same, on the bucket")
    print(f"  public catalogue on {a.prod}: "
          f"{'unreachable' if public_ids is None else str(len(public_ids)) + ' surveys'}")
    print(f"  gated catalogue, asked anonymously: {gated_status} "
          f"{'(correctly refused)' if gated_status in (401, 403) else '<-- SHOULD BE 403'}")
    if gated_status not in (401, 403):
        problems.append(("catalog-gated.geojson", f"anonymous got {gated_status}"))
    if problems:
        print(f"\n  {len(problems)} thing(s) a reader would see as broken:")
        for did, v in problems:
            print(f"    {did}: {v}")
        return 1
    print("\n  nothing a reader would see as broken.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
