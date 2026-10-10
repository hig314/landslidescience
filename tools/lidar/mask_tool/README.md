# Mask tool — local, offline, one window at a time

    ./run_examples.sh                      # both Portage examples, ports 8765 / 8766
    WIN=r0,r1,c0,c1 ./run_examples.sh      # another window (grid pixels)
    /opt/anaconda3/bin/python3 server.py ../composites/<id>/recipe.json --window r0,r1,c0,c1 --port 8767

**8765 · portage_legacy** — the five Portage crops with Hig's Photoshop
masks. The regression target; bit-exact with the 2026-01-04 merge.

**8766 · portage_auto** — same inputs, no hand masks: every mask comes
from the generators in the recipe (footprint / erode / despeckle /
feather; `water` with the lake level found automatically). This is the
one to paint on. Background "composite − reference" shows where it
differs from the painted merge; the wand can select on that difference
(e.g. click a white blob, tol 5 m, "show layer (1)" on bathy2026 to
adopt the painted choice there).

## What the stack shows
Composite on top, then layers in fold order, base at the bottom.
Per row: elevation (shared grey ramp, purple = nodata, orange = where the
effective mask lets the layer through), effective mask, and paint when
there is any. Click a thumbnail to open that view; click composite for
the clean result. The switches under the thumbnails are session-only
toggles: DEM = the whole layer, auto = the generated mask (off = plain
footprint), paint = hand paint.

## Tools
- **wand** on the difference (layer − below), the layer's elevation, the
  composite, the auto mask, or composite − reference; tolerance in the
  raster's units; shift adds, alt subtracts. **rect** drags a box.
  grow / shrink / fill holes / invert / ∩ footprint / clear.
- **fill** the selection into the current layer's paint: show (1), hide
  (0), restore auto (clears paint); feather = inward cosine ramp.
- **airbrush**: size, hardness, opacity (cap per stroke), flow (build per
  dab); show / hide / erase. `[` `]` change size.
- **undo** (session stack), **save paint** → `masks/<layer>_paint.tif`
  (value, alpha) in the recipe's work dir, on this window's domain box.

## Where paint goes and how it comes back
`$LIDAR_BUILD/composite/<id>/masks/<layer>_paint.tif` for weight paint,
`obs/<layer>/{class,sigma}_paint.tif` for class and quality corrections
(default root `/Volumes/Nunatak/lidar_build/composite/<id>/`). Every paint
file covers the **full composite grid** as a sparse, tiled GeoTIFF: the
first save creates it (a few hundred kB however big the grid), later saves
are windowed writes, so sessions on different windows accumulate into one
record and a save never discards what another window painted. A restarted
session reads all three kinds back for its window. Both the tool and
`composite.py build` (windowed or full) apply them through the same
functions (`axes.apply_class_paint` / `apply_sigma_paint`,
`masks.effective`), so what you see is what the build does.

The auto mask is regenerated every time; the paint is the record.
Photoshop / QGIS round trip: `composite.py export-mask … --layer X --out
x.tif` (the effective or auto mask as plain 8-bit), edit it, then
`composite.py import-mask … --layer X x.tif [--align pixel]` paints every
cell that differs from the auto mask and clears earlier paint where the
edited file agrees with it. A full `build` then applies it.
