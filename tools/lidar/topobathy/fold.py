"""The fold.  NaN-aware: where the running output has no data yet and a
layer has weight, the layer simply takes the cell; where a layer is
invalid its effective mask is already 0, so a stroke of 1 over a hole
changes nothing."""
from __future__ import annotations
import numpy as np


def fold_step(out, z, m):
    """out <- out*(1-m) + z*m, in place, treating NaN in `out` as absent."""
    has_out = np.isfinite(out)
    # a cell the layer has no value for contributes nothing, whatever its weight (2026-10-16, Bluff Point:
    # a feathered weight over a NaN cell blended the running -3.9 m toward 0 m and left a -0.5 m speck;
    # the docstring's assumption -- "where a layer is invalid its effective mask is already 0" -- did not
    # hold for every mask that reaches here)
    on = (m > 0) & np.isfinite(z)
    zz = np.where(np.isfinite(z), z, 0).astype(np.float32)
    both = has_out & on
    out[both] = out[both] * (1.0 - m[both]) + zz[both] * m[both]
    only_new = ~has_out & on
    out[only_new] = zz[only_new]
    return out


def fold(layers):
    """layers: iterable of (z, m) in fold order; the base's m is ignored
    (its own validity is its mask)."""
    it = iter(layers)
    z0, _ = next(it)
    out = z0.astype(np.float32, copy=True)
    for z, m in it:
        fold_step(out, z, m)
    return out
