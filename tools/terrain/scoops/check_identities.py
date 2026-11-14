#!/opt/anaconda3/bin/python3
"""Checks that do not lean on USGS's runs.

1. Sphere on a plane, phi = 0: an exact answer by other means. The driving moment
   is gamma V l sin(beta) (cap volume and centroid in closed form) and the resisting
   arm integral is done in spherical coordinates on the cap, with no columns.
2. Round trip: the cohesion solved for F = 1 at a cell, fed to the forward
   calculation, must give F = 1 on that cell's critical surface.
3. Scale: phi = 0 required cohesion is exactly proportional to gamma; and with
   friction, scaling heights and lengths by k at fixed c / (gamma k) changes nothing.
"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scoops as S


def plane_exact(beta_deg, R, d, gamma):
    """F for c = 1 kPa, phi = 0: sphere radius R, centre d off the plane."""
    b = math.radians(beta_deg)
    h = R - d
    V = math.pi * h * h * (3 * R - h) / 3
    arm_n = 3 * (2 * R - h) ** 2 / (4 * (3 * R - h))        # centroid from the centre, along the normal
    D = gamma * V * arm_n * math.sin(b)
    # R_ij on the cap: distance of a surface point to the horizontal axis through
    # the centre, normal to the fall line. Unit normal n of plane; axis a horizontal.
    n = np.array([math.sin(b), 0, math.cos(b)])              # plane normal, fall line along +x
    a = np.array([0, 1.0, 0])
    e1 = np.array([math.cos(b), 0, -math.sin(b)])
    tmax = math.acos(d / R)
    nt, np_ = 3000, 720
    t = (np.arange(nt) + .5) / nt * tmax
    p = (np.arange(np_) + .5) / np_ * 2 * math.pi
    tt, pp = np.meshgrid(t, p, indexing='ij')
    pt = R * (np.sin(tt)[..., None] * (np.cos(pp)[..., None] * e1 + np.sin(pp)[..., None] * a) - np.cos(tt)[..., None] * n)
    rij = np.hypot(pt[..., 0], pt[..., 2])
    dA = R * R * np.sin(tt) * (tmax / nt) * (2 * math.pi / np_)
    return np.sum(rij * dA) / D, V


def plane_ours(beta_deg, R, d, gamma, dx):
    b = math.radians(beta_deg)
    n = int(6 * R / dx)
    x = (np.arange(n) + .5) * dx
    z = -np.tan(b) * x
    dem = np.tile(z, (n, 1))
    xc, yc = 3 * R, 3 * R
    z_plane = -math.tan(b) * xc
    zc = z_plane + d / math.cos(b)
    return S.single(S.Grid(dem, dx), xc, yc, zc, R, 1.0, 0.0, gamma)


print('1. Sphere on a plane, phi = 0: F for c = 1 kPa, exact vs columns')
print(f"   {'beta':>5s}{'R':>7s}{'d':>7s}{'exact F':>11s}{'ours':>11s}{'diff %':>8s}{'V diff %':>9s}{'cols':>7s}")
# Only caps below the sphere's equator: acos(d/R) + beta < 90. Vertical columns
# cannot represent a surface that overhangs itself, in Scoops3D or here.
for beta, R, d in ((10, 300, 250), (30, 300, 200), (30, 300, 260), (45, 300, 280), (60, 300, 295)):
    F, V = plane_exact(beta, R, d, 26.0)
    o = plane_ours(beta, R, d, 26.0, 5.0)
    print(f"   {beta:5d}{R:7d}{d:7d}{F:11.5f}{o['f_ordinary']:11.5f}{100 * (o['f_ordinary'] / F - 1):8.3f}"
          f"{100 * (o['v'] / V - 1):9.3f}{o['ncols']:7d}")

print('\n2. Round trip on a rough synthetic surface, phi 0 / 20 / 35')
rng = np.random.default_rng(3)
n, dx = 160, 25.0
yy, xx = np.mgrid[0:n, 0:n] * dx
dem = 900 * np.sin(xx / 900) + 600 * np.cos(yy / 700) + 200 * np.sin((xx + yy) / 300) + 2000
g = S.Grid(dem, dx)
xn, yn = S.lattice(n, n, dx, 1, 1, n, n, 8)
edges = [1e6, 1e9]
phis = [0, 20, 35]
r = S.search(g, xn, yn, 1000, 6000, 150, 40, 1e6, 1e9, 2e5, gamma=26, phis=phis, edges=edges, workers=4)
print('   surfaces', r['stats']['surfaces'], ' filtered (m_alpha)', r['stats']['req_filtered'])
for p, phi in enumerate(phis):
    c = r['creq'][p, 0]
    pick = c > 0
    cmax = float(c.max())
    f = S.search(g, xn, yn, 1000, 6000, 150, 40, 1e6, 1e9, 2e5, gamma=26, c=cmax, phi_f=phi, workers=4)
    print(f"   phi {phi:2d}: highest required c {cmax:9.1f} kPa; forward run at that c: lowest F {np.nanmin(f['fmin']):.5f}")

print('\n3. Scale')
a = S.search(g, xn, yn, 1000, 6000, 150, 40, 1e6, 1e9, 2e5, gamma=26, phis=[0, 30], edges=edges, workers=4)['creq'][:, 0]
b = S.search(g, xn, yn, 1000, 6000, 150, 40, 1e6, 1e9, 2e5, gamma=13, phis=[0, 30], edges=edges, workers=4)['creq'][:, 0]
m = a[0] > 0
print(f"   gamma x 0.5, phi 0: required c ratio range {np.min(b[0][m] / a[0][m]):.6f} to {np.max(b[0][m] / a[0][m]):.6f}")
m = a[1] > 0
print(f"   gamma x 0.5, phi 30: required c ratio range {np.min(b[1][m] / a[1][m]):.6f} to {np.max(b[1][m] / a[1][m]):.6f}")
