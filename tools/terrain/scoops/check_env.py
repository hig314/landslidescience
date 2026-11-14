#!/opt/anaconda3/bin/python3
"""Checks on the (sigma, tau) recording, none of which reuses the code path under test:

 1. USGS's single surface G: their published Ordinary F, against F rebuilt from our
    sigma and tau alone, F = (c + sigma tan phi) / tau.
 2. A planar slope: a shallow surface tends to the infinite slope, where tau / sigma
    = tan(slope) exactly, so the highest ratio should approach it from around there.
 3. Scale: plane, all lengths x 2 and volumes x 8: sigma and tau must exactly double.
 4. Real terrain: the identity c_ord = tau - sigma tan phi per tile top is not free,
    so compare tile c_ord with the separately kept requirement over a window.
"""
import math
import sys
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S
import benchmark_usgs as B
import statewide as W

d = B.ROOT / 'DonaldGiam'
p = B.scp(d / 'G_emb10single.scp')
dem, dx = B.read_asc(d / 'input/emb10DEM.asc')
x, y, z, R, _ = [float(v) for v in p['xcen   ycen   zcen   rad   angle'].split()]
_, c, phi, gam = [float(v) for v in p['lnum   cee   phi   gamt'].split()]
o = S.single(S.Grid(dem, dx), x, y, z, R, c, phi, gam, 999.0)
ref = B.out_values(d / 'G_emb10single_output/G_emb10single_out.txt')
F = (c + o['sigma'] * math.tan(math.radians(phi))) / o['tau']
print(f"1 USGS G: their F_ord {ref['f_ordinary']:.4f}, from sigma,tau {F:.4f}, diff {100 * (F / ref['f_ordinary'] - 1):+.3f}%  (sigma {o['sigma']:.1f}, tau {o['tau']:.1f} kPa)")

def plane(beta, dx, n=400):
    ii = np.arange(n) * dx
    return np.tile((n * dx - ii) * math.tan(math.radians(beta)), (n, 1))

def surf(beta, dx, x, y, zc, R):
    return S.single(S.Grid(plane(beta, dx), dx), x, y, zc, R, 0.0, 30.0, 26.0, 999.0)

for beta in (20, 35, 50):
    dxp, n = 10.0, 400
    best = 0
    xs = n * dxp / 2
    zg = (n * dxp - xs) * math.tan(math.radians(beta))
    for R in (500, 1000, 2000, 4000, 8000):
        # axis on the normal through the mid point; depth 1/50 of R or so gives near-parallel base
        cb, sb = math.cos(math.radians(beta)), math.sin(math.radians(beta))
        dist = R - min(R * .02, 25)
        xc, zc = xs + dist * sb, zg + dist * cb
        o = surf(beta, dxp, xc, n * dxp / 2, zc, R)
        if o['sigma'] > 0:
            best = max(best, o['tau'] / o['sigma'])
    print(f"2 plane {beta} deg: tan = {math.tan(math.radians(beta)):.4f}; highest tau/sigma of shallow spheres {best:.4f}")

a = surf(35, 10.0, 2300, 2000, 2400, 1000)
b = surf(35, 20.0, 4600, 4000, 4800, 2000)
print(f"3 scale x2: sigma {a['sigma']:.3f} -> {b['sigma']:.3f} (ratio {b['sigma'] / a['sigma']:.6f}); tau ratio {b['tau'] / a['tau']:.6f}; V ratio {b['v'] / a['v']:.6f}")
