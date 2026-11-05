#!/opt/anaconda3/bin/python3
"""Calibrate the NOS soundings' sigma and pre-1964 vertical offset from the data themselves (2026-11-03).
    nos_calibrate.py IN_1977_84.csv IN_1915_31.csv OUT_1977_84.csv OUT_1915_31.csv

The exports' sigmas were assigned from the bed slope and capped at 10 m; measured, they are far too large, and the
spline (misfit = sigma) stood metres off the casts (Hig's Taylor review).  Measured 2026-11-03:
  1977-84 vs the 2008-09 multibeam, 45,129 casts: median -0.34 m, NMAD by assigned-sigma class 0.32 / 0.52 / 0.92 / 1.60 m
  1915-31 vs the nearest 1977-84 cast within 60 m, 6,725 casts: median +1.40 m, NMAD by class 1.6 / 2.5 / 4.2 / 3.0 m
So 1977-84: sigma x 0.35, floor 0.4 m.  1915-31: sigma x 0.5, clipped to 1.5..4 m.
The 1915-31 casts also stand above the later ones.  Each survey is shifted by its own median against the 1977-84 casts
(n >= MIN_N pairs within R_M), else by the group's median.  The offset is measured, not assumed; a pre-1964 survey standing
high is what the 1964 subsidence would produce, but the attribution is a hypothesis.  z_raw keeps the exported value."""
import csv, sys
import numpy as np
from scipy.spatial import cKDTree
R_M = 60.0; MIN_N = 100
in77, in31, out77, out31 = sys.argv[1:5]
def load(p):
    rows = list(csv.DictReader(open(p))); return rows, np.array([[float(r["x"]), float(r["y"]), float(r["z"]), float(r["sigma"])] for r in rows]), np.array([r["survey"] for r in rows])
def save(p, rows, z, sg):
    cols = list(rows[0].keys()) + ["z_raw"]
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, cols); w.writeheader()
        for r, zz, s in zip(rows, z, sg):
            w.writerow(dict(r, z_raw=r["z"], z=f"{zz:.3f}", sigma=f"{s:.2f}"))
R7, A7, S7 = load(in77); R3, A3, S3 = load(in31)
save(out77, R7, A7[:, 2], np.maximum(0.4, 0.35 * A7[:, 3]))
d, i = cKDTree(A7[:, :2]).query(A3[:, :2]); near = d < R_M; res = A3[:, 2] - A7[i, 2]
group = float(np.median(res[near])); z3 = A3[:, 2].copy(); print(f"1915-31 vs 1977-84: {int(near.sum())} pairs, group median {group:+.2f} m")
for s in np.unique(S3):
    m = S3 == s; k = m & near
    dz = float(np.median(res[k])) if k.sum() >= MIN_N else group
    z3[m] -= dz; print(f"  {s}: n {int(m.sum())} pairs {int(k.sum())} shift {-dz:+.2f} m{'' if k.sum() >= MIN_N else ' (the group median)'}")
save(out31, R3, z3, np.clip(0.5 * A3[:, 3], 1.5, 4.0))
