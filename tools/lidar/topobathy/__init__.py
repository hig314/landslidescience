"""Topobathy compositing: an ordered layer stack folded through per-layer masks.

    out = layer[0]
    for i in 1..n:  out = out*(1 - m_i) + layer[i]*m_i

Everything else in the package is a way to make a layer or a mask.  See
tools/lidar/TOPOBATHY_PLAN.md.  Mockup of phase 1 (2026-09-20).
"""
from .grid import Grid
from .recipe import Recipe
from .fold import fold
