#!/usr/bin/env python3
"""Write the purple susceptibility ramp files from the tables the browser uses.

The susceptibility archives on R2 were baked yellow-orange-red (ColorBrewer
YlOrRd). Since 2026-11-25 the site shows them in the purple arm of the InSAR
ramp, recoloured in the browser (ls_overlays.js, "Susceptibility is purple"):
the baked GREEN value places a pixel on the old ramp, and the purple stops
colour that position. This script applies the same two tables to the OLD
class colours and writes the ramp files, so the keys (api/ramps/) show
exactly what the map draws, and a future re-bake produces the same colours.

STOPS and G must match SUSC_STOPS and SUSC_G in ls_overlays.js. Alpha, and
the DGGS "not assessed" class (pale blue), are kept.

    python3 tools/susc_purple_ramps.py          # rewrite the four files
"""
import math, pathlib

STOPS = [(0, (231, 212, 232)), (0.5, (194, 165, 207)), (1, (123, 50, 148))]
G = {'usgs': [(255, 0), (141, 0.5), (26, 1)],
     'full': [(255, 0), (204, 0.25), (141, 0.5), (59, 0.75), (0, 1)]}


def t_of(g, fam):
    tab = G[fam]
    for (g0, t0), (g1, t1) in zip(tab, tab[1:]):
        if g1 <= g <= g0:
            return t0 + (g - g0) / (g1 - g0) * (t1 - t0)
    return tab[0][1] if g > tab[0][0] else tab[-1][1]


def purple(g, fam):
    t = t_of(g, fam)
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        if t0 <= t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(int(math.floor(a + f * (b - a) + 0.5)) for a, b in zip(c0, c1))
    return STOPS[-1][1]


# file -> (family, [(value, old R, old G, old B, alpha)]): the colours the
# archives were baked with, value by value.
OLD = {
    'susc_color_n10.txt': ('usgs', [(1, 255, 255, 178, 90), (62, 255, 255, 178, 90),
                                    (63, 253, 141, 60, 190), (80, 253, 141, 60, 190), (81, 227, 26, 28, 235)]),
    'susc_color_lw.txt': ('usgs', [(1, 255, 255, 178, 90), (74, 255, 255, 178, 90),
                                   (75, 253, 141, 60, 190), (80, 253, 141, 60, 190), (81, 227, 26, 28, 235)]),
    'dggs_susc_color.txt': ('full', [(3, 253, 171, 75, 188), (5, 248, 108, 49, 208), (6, 243, 76, 38, 216),
                                     (7, 233, 51, 33, 222), (8, 219, 34, 35, 226), (9, 204, 17, 36, 231),
                                     (10, 189, 0, 38, 235)]),
    'nasa_susc_color.txt': ('full', [(1, 255, 255, 178, 70), (2, 254, 204, 92, 150), (3, 253, 141, 60, 190),
                                     (4, 240, 59, 32, 215), (5, 189, 0, 38, 235)]),
}
HEAD = {
    'susc_color_n10.txt': "# n10 discrete frequency-ratio classes (gdaldem color-relief).\n"
        "# Columns: VALUE R G B A. Breaks at n10's FR jump (see susceptibility analysis):\n"
        "#   0      = none        -> transparent\n"
        "#   1-62   = Low   (FR ~0.2, under-represented) -> pale lavender, faint\n"
        "#   63-80  = High  (FR ~13-15)                  -> mid purple\n"
        "#   81      = Very High (FR ~20)                -> dark purple\n",
    'susc_color_lw.txt': "# lw discrete frequency-ratio classes (gdaldem color-relief).\n"
        "# Columns: VALUE R G B A. Breaks at lw's FR jump (see susceptibility analysis):\n"
        "#   0      = none        -> transparent\n"
        "#   1-74   = Low   (FR ~0.2, under-represented) -> pale lavender, faint\n"
        "#   75-80  = High  (FR ~13)                     -> mid purple\n"
        "#   81      = Very High (FR ~24)                -> dark purple\n",
    'dggs_susc_color.txt': "# Colour ramp for the Alaska DGGS deep-seated landslide susceptibility\n"
        "# raster (PIR 2025-3), in gdaldem color-relief format: VALUE R G B A.\n"
        "# Discrete classes 3-10, one stop each; class 0 (slope < 3 deg, where this\n"
        "# failure style cannot occur) is transparent.\n",
    'nasa_susc_color.txt': "# Colour ramp for NASA's global landslide susceptibility map (Stanley &\n"
        "# Kirschbaum 2017, 2023 update), in gdaldem color-relief format:\n"
        "# VALUE R G B A. Classes 1-5 (very low ... very high); 0 is the sea (and\n"
        "# anything NASA did not assess): transparent.\n",
}
NOTE = ("#\n# PURPLE since 2026-11-25: the purple arm of the InSAR ramp, pale and faint\n"
        "# at the low end, dark and nearly opaque at the high end, one scale across\n"
        "# the four susceptibility layers. Chosen over yellow-orange-red because the\n"
        "# landslide dots are pink, yellow, green and blue. WRITTEN BY\n"
        "# tools/susc_purple_ramps.py: edit the tables there, not this file.\n"
        "# The archives on R2 are still the yellow-red bake; the browser recolours\n"
        "# them to these values (ls_overlays.js) until they are re-baked from here.\n")
TAIL = {'dggs_susc_color.txt': "# lake, river or glacier: the model does not apply. Pale and faint, so it\n"
                               "# reads as 'not assessed' rather than as a susceptibility value.\n"
                               "200  190 232 255  90\n"}

if __name__ == '__main__':
    here = pathlib.Path(__file__).resolve().parent
    for name, (fam, rows) in OLD.items():
        out = HEAD[name] + NOTE + "nv   0   0   0   0\n0    0   0   0   0\n"
        for v, r, g, b, a in rows:
            pr, pg, pb = purple(g, fam)
            out += f"{v:<4} {pr:3d} {pg:3d} {pb:3d} {a:3d}\n"
        out += TAIL.get(name, '')
        (here / name).write_text(out)
        print('wrote', name)
