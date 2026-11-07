#!/opt/anaconda3/bin/python3
"""Which rules each box of a run was built with (Hig, 2026-11-06: "keep track of which rules were applied to which blocks, and
we'll go back and rerun some of them as we find issues").

    rules_applied.py TAG [BOX ...]            -> a table: box, start, and for each rule in rules_ledger.json Y / n / (stage not run)

A rule in the ledger names the stage whose code it changed and the moment it went live (the atomic replace of the live file);
a box has it when that stage of the box STARTED after the moment (the chain reads code at each stage start; the stage stamps are
the box's chain.log: "units" = the start, "joint fuse + layer", then the chain's own "build 1 (diag)", "water", "build 2", "shore",
"fill", "build 3", "high line").  A rule that changed a DATA file read by a stage (tide_sea.geojson -> flat water -> build 1) is
dated the same way with that stage.  Boxes with an 'n' are the rerun candidates for that rule; the order of reruns is the plan's."""
import json, re, sys, pathlib
from datetime import datetime
HERE = pathlib.Path(__file__).resolve().parent; W = pathlib.Path("/Volumes/Nunatak/lidar_build/composite")
STAGES = {"units": "units", "joint": "joint fuse + layer", "build1": "build 1 (diag)", "water": "water", "build2": "build 2", "shore": "shore",
          "fill": "fill", "build3": "build 3", "highline": "high line"}
tag = sys.argv[1]; boxes = sys.argv[2:] or sorted(p.name for p in W.glob("kj_r*c*") if (p / f"out_{tag}").is_dir())
L = json.load(open(HERE / "rules_ledger.json"))["rules"]


def stage_times(box):
    """{stage: datetime} for the box's run under `tag`, from its archived chain.log (the live one when not archived)."""
    for p in (W / box / f"out_{tag}" / "logs" / "chain.log", W / box / "chain.log"):
        if p.exists():
            break
    else:
        return {}
    lines = p.read_text().splitlines(); t = {}; on = False
    for ln in lines:
        m = re.match(r"\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)", ln)
        if not m:
            continue
        when, what = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2)
        if what.startswith(f"{box} {tag}: units"):
            t = {"units": when}; on = True; continue
        if not on:
            continue
        if what.startswith(f"{box} {tag}: "):
            rest = what[len(f"{box} {tag}: "):]
            if rest.startswith("joint"): t["joint"] = when
            elif rest.startswith("review") or rest.startswith("gate"): on = False
            continue
        for k, v in STAGES.items():
            if what == v or what.startswith(v + " ") or what.startswith(v + "("):
                t.setdefault(k, when)
    return t


rows = []
for b in boxes:
    t = stage_times(b)
    if not t:
        rows.append((b, "-", ["?"] * len(L))); continue
    flags = []
    for r in L:
        live = datetime.strptime(r["live"], "%Y-%m-%d %H:%M:%S"); st = t.get(r["stage"])
        flags.append("Y" if (st is not None and st >= live) else ("n" if st is not None else "."))
    rows.append((b, t["units"].strftime("%m-%d %H:%M"), flags))
w = max(len(r["id"]) for r in L)
print(f"{'box':9s} {'start':12s} " + " ".join(f"{r['id']:>{w}s}" for r in L))
for b, s, f in rows:
    print(f"{b:9s} {s:12s} " + " ".join(f"{x:>{w}s}" for x in f))
print("\nrules: " + "; ".join(f"{r['id']} = {r['what']} ({r['stage']}, live {r['live'][5:16]})" for r in L))
need = {r["id"]: [b for b, s, f in rows if f[i] == "n"] for i, r in enumerate(L)}
print("\nboxes without each rule (rerun candidates):")
for k, v in need.items():
    print(f"  {k}: {' '.join(v) if v else 'none'}")
