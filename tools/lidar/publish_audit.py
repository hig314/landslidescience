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
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PM_DIR = Path(os.environ.get("LIDAR_PM_OUT", ROOT / "data" / "lidar" / "pmtiles"))
COG_DIR = Path(os.environ.get("LIDAR_COG_OUT", "/Volumes/Nunatak/lidar_build/cog"))
R2 = "https://lidar.landslidescience.org"


def head(url, timeout=8):
    """HTTP status for a URL, without downloading it.

    urllib rather than a curl subprocess: this also runs inside the app
    container, which has no curl -- and a missing binary there returned 0 for
    every probe, which read as "nothing is on R2" and flagged every published
    survey as broken. A dependency that fails by reporting absence is worse
    than one that fails loudly.
    """
    import urllib.error
    import urllib.request
    req = urllib.request.Request(url, method="HEAD")
    req.add_header("User-Agent", "landslidescience-audit/1")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def fetch_ids(url, timeout=20):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            fc = json.load(r)
        return {f.get("properties", {}).get("id") for f in fc.get("features", [])}
    except Exception:
        return None


def read_ids(path):
    """-> (ids, build-metadata) for a catalogue on disk; (None, {}) if unreadable."""
    try:
        fc = json.loads(Path(path).read_text())
        return ({f.get("properties", {}).get("id") for f in fc.get("features", [])},
                fc.get("build") or {})
    except (OSError, ValueError):
        return None, {}


def audit(prod="https://landslidescience.org", no_r2=False, catalog_dir=None):
    """-> (rows, problems, meta). Importable so the admin page renders the
    same answer the command line gives, rather than a second implementation
    of it that can drift."""
    class _A:
        pass
    a = _A()
    a.prod, a.no_r2, a.catalog_dir = prod, no_r2, catalog_dir
    return _run(a)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod", default="https://landslidescience.org")
    ap.add_argument("--no-r2", action="store_true", help="skip the R2 probes (faster)")
    a = ap.parse_args()
    a.catalog_dir = None
    rows, problems, meta = _run(a)
    _print(rows, problems, meta, a.prod)
    return 1 if problems else 0


def _run(a):
    manifest = json.loads((HERE / "datasets.json").read_text())["datasets"]
    if getattr(a, "catalog_dir", None):
        # Running ON the server that serves these: read the files rather than
        # asking the server to make an HTTP request to itself, which is both
        # fragile and a worse answer -- the file IS what it would serve.
        public_ids, build = read_ids(Path(a.catalog_dir) / "catalog.geojson")
        gated_status = None
    else:
        public_ids, build = fetch_ids(f"{a.prod}/lidar/catalog.geojson"), {}
        # The gated catalogue is auth-only, so an anonymous probe SHOULD be
        # refused. That refusal is itself a result worth reporting.
        gated_status = head(f"{a.prod}/lidar/catalog-gated.geojson")

    rows, problems = [], []
    probes = {}
    if not a.no_r2:
        want = []
        for ds in manifest:
            did = ds["id"]
            want += [f"{R2}/pmtiles/{did}.pmtiles", f"{R2}/pmtiles/{did}_slope.pmtiles",
                     f"{R2}/pmtiles/{did}_ortho.pmtiles", f"{R2}/cog/{did}.tif"]
        with ThreadPoolExecutor(24) as ex:
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
        verdict, bad = "ok", None
        if state == "retired":
            verdict = "retired" if not in_cat else "RETIRED BUT STILL LISTED"
        elif state == "dev-only":
            if not in_cat:
                verdict = "dev only"
            elif build.get("include_dev"):
                verdict = "dev only (this is a dev catalogue)"
            else:
                verdict = "DEV-ONLY BUT LISTED PUBLICLY"
        elif state == "gated":
            # Bytes on the bucket ARE public, listed or not: R2 has no auth in
            # front of it and the object name is derived from the id, so the
            # only thing hiding one is that nobody guessed it. That is not a
            # gate. It has to be asked separately from the catalogue check
            # because r2_sync only ever ADDS -- gating a survey that was
            # already pushed does not pull it back, and nothing else notices.
            leaks = [] if a.no_r2 else [
                name for name, ch in zip(("PYRAMID", "SLOPE", "ORTHO", "COG"), r2)
                if ch != "-"]
            why = []
            if leaks:
                why.append("ON PUBLIC R2 (" + ", ".join(leaks) + ")")
            if in_cat:
                why.append("IN THE PUBLIC CATALOGUE")
            if why:
                verdict, bad = "GATED BUT " + " AND ".join(why), True
            elif local[0] != "P":
                verdict = "gated, pyramid not built"
            else:
                verdict = "gated, served from the droplet"
        else:  # public
            if in_cat is None:
                verdict = "catalogue unreadable — cannot tell"
            elif not in_cat:
                # "built but unpublished" and "not built at all" are different
                # jobs -- one wants an upload, the other wants a build -- and
                # the first reads as an alarm. Only say it when the pyramid is
                # actually there.
                verdict = ("built, NOT YET PUBLISHED" if local[0] == "P"
                           else "not built yet")
            elif r2[0] != "P" and not a.no_r2:
                verdict = "LISTED BUT NOT ON R2 — reader sees nothing"
            elif local[2] == "o" and r2[2] != "o" and not a.no_r2:
                verdict = "listed; ortho built but not uploaded"
            else:
                verdict = "ok"
        if bad is None:
            bad = verdict.isupper() or "NOT" in verdict
        if bad:
            problems.append((did, verdict))
        rows.append({"id": did, "state": state, "built": local, "r2": r2,
                     "in_cat": in_cat, "verdict": verdict,
                     # Who made the survey, carried straight from the
                     # manifest: this page is where a missing credit is
                     # meant to be noticed and filled in.
                     "source": (ds.get("source") or "")
                               .replace(" (to confirm)", "") or None,
                     "source_url": ds.get("source_url") or None,
                     # An attribution inferred from file naming or a partial
                     # match, rather than read off a metadata record. It is
                     # still shown -- a lead beats a blank -- but marked, so
                     # nobody cites it as though it were established.
                     "source_tentative": "(to confirm)" in (ds.get("source") or ""),
                     "bad": bad})

    if gated_status is not None and gated_status not in (401, 403):
        problems.append(("catalog-gated.geojson",
                         f"anonymous got {gated_status}, should be 403"))

    return rows, problems, {"build": build, "public_count": None if public_ids is None else len(public_ids),
                            "gated_status": gated_status}


def _print(rows, problems, meta, prod):
    w = max(len(r["id"]) for r in rows) + 1
    print(f"\n  {'survey':{w}} {'state':9} {'built':6} {'R2':5} {'cat':4} {'src':4} verdict")
    print(f"  {'-' * w} {'-' * 9} {'-' * 6} {'-' * 5} {'-' * 4} {'-' * 4} {'-' * 40}")
    for r in rows:
        flag = "?" if r["in_cat"] is None else ("yes" if r["in_cat"] else "no")
        src = "yes" if r["source"] else "--"
        print(f"  {r['id']:{w}} {r['state']:9} {r['built']:6} {r['r2']:5} "
              f"{flag:4} {src:4} {r['verdict']}")

    print("\n  built = Pyramid / slope / ortho / COG present locally; R2 = same, on the bucket")
    pc = meta["public_count"]
    print(f"  public catalogue on {prod}: "
          f"{'unreachable' if pc is None else str(pc) + ' surveys'}")
    gs = meta["gated_status"]
    print(f"  gated catalogue, asked anonymously: {gs} "
          f"{'(correctly refused)' if gs in (401, 403) else '<-- SHOULD BE 403'}")
    uncredited = [r["id"] for r in rows if not r["source"] and r["state"] != "retired"]
    if uncredited:
        print(f"\n  {len(uncredited)} survey(s) with no source recorded: "
              f"{', '.join(uncredited)}")
    if problems:
        print(f"\n  {len(problems)} thing(s) a reader would see as broken:")
        for did, v in problems:
            print(f"    {did}: {v}")
        return
    print("\n  nothing a reader would see as broken.")


if __name__ == "__main__":
    sys.exit(main())
