# Required strength from topography (Scoops3D rebuild): exploration state

Status as of 2026-11-05. **Exploratory, uncommitted, dev only.** Nothing here
has been pushed or deployed.

## Multi-size run (2026-11-19): read this first

Hig asked for one friction angle across every size class, keeping at each cell the
most demanding surface: its cohesion, volume and depth, with a smarter search than
the dense fine lattice. phi = 20 deg (matches the finished 200 m run; the envelope's
large-stress end reads about 24 deg).

- `multisize.py` + `run_multisize.sh`: classes 10^10 down to 10^6, each on its own
  grid, blocks listed in `scoops_state/multi_phi20/v<k>/done.txt`. `touch
  .../multi_phi20/STOP` stops cleanly; a kill loses only blocks in flight; the same
  script resumes. It refuses to start without Nunatak (a missing tile reads as sea).
- `scoops_core.c` gained `scoops_climb` (compass search in x, y, z, R from each
  node's best surface) and the coarse pass now records that surface.
- **Pilot, Chugach 60 km block, 10^9 m3, against the old fine lattice:** 135 s
  against 1,483 s; same maximum (1,477 kPa); volume of the winner within 0.03 dex;
  deepest column of the winner 529 against 521 m. But per-cell cohesion is a median
  11 % low, a quarter of cells more than 30 % low, and 6.5 % of cells the fine
  lattice colours are blank: the climb finds each optimum but paints fewer of the
  near-optimal surfaces that set the value at the edge of a failure.
- **Variants piloted 2026-11-20** (side copy `tools/terrain/scoops_dev/`, same block,
  against `v9_fine`; `MS_VARIANT=...`, runs in `multi_pilot/v9_<tag>`). Median
  cohesion ratio to the fine lattice / cells more than 30 % low / blank where the fine
  lattice has a value: climb 0.87 / 30 % / 10 %; `bands` the same (no gain);
  `dense` 0.93 / 22 % / 7 %; `dense,dil` (seed one node further out) 0.93 / 22 % /
  6 %; `bands,dense` and `dense3` no better than `dense`. Wall time 130 to 160 s, the
  same as the plain climb. What is left is at the rim of each failure patch (median
  0.64 there, 0.94 two cells in) and at low cohesion (cells under 50 kPa are mostly
  missed; above 400 kPa the median is 0.97). `dense,dil` is the pick. Not yet moved
  into `scoops/`.
- State on Nunatak: `multi_phi20/v10` has at most 17 of 138 blocks, made with the
  single-seed climb (the version that reads 11 % low). `multi_pilot/` has
  `v9_fine`, `v9_climb`, `v8_climb`; `v8_fine` did not finish.
- **Depth (Hig, 2026-11-20): per-pixel, the locally deepest surface wins.** Built in
  `scoops_dev` as: depth under the cell to the deepest surface that is the most
  demanding one at some cell. No second search: the core records each cell's winning
  sphere (`sx, sy, sz, sr` extras, written as bands in absolute EPSG:3338), and
  `multisize.paint_winners` repaints those spheres with the core's column geometry.
  Bands `depth_win` and `c_at_depth` (the cohesion of the surface that sets the
  depth). Pilot block: 7,908 winning surfaces, a few seconds; never shallower than
  the core's own `depth` at any cell. Median depth 204 m, against 16 m for the cell's
  own surface and 532 m for its deepest column. **Caution found:** at 91 % of cells
  the depth comes from a different surface than the cohesion, and that surface needs
  a median 0.26 of the cell's cohesion: weakly demanding deep surfaces set most of
  the depth map. **Hig, 2026-11-20: keep "deepest winning surface" as is for now** (no floor on
  `c_at_depth / creq`); keep the `c_at_depth` band so a floor can be tried later. Still per block and per class: winners must be re-judged after
  the classes are combined, and repainted across block seams.
- **Winner geometry on the pilot block (2026-11-20, `multi_pilot/v9_finerec` = the fine
  lattice with winners recorded; same cohesion map as `v9_fine`).** Hig asked for a
  faster search that seeds close to the likely geometry. Found:
  - Winners are thin caps of large radius: R median 2.95 r0 (5 to 95 %: 1.4 to 6.0),
    the sphere only 0.03 r0 (about 40 m) below ground under its centre, the centre
    0.23 R downslope of the cell it wins. 7,389 winners; 596 of them win half the cells.
  - **The class floor sets the size.** Half the cells' winners have volume within 5 %
    of 10^9, the class minimum. Holding position and radius and changing only the
    volume (12 biggest winners), cohesion relative to 10^9 is 0.52 at 1.4e9, 0 at 2e9,
    1.19 at 7e8, 1.22 at 5e8, 1.17 at 2.5e8 (medians; evaluated on the 200 m grid, too
    coarse below 10^9, and not re-optimised in position or radius).
  - At fixed volume, radius off by 10 % costs 9 % of the cohesion; off by 20 to 25 %,
    35 to 40 %. A 50 m change of centre height or radius alone changes volume by 25 to
    30 % and cohesion by 20 to 45 %: height must be solved from volume, not guessed.
  - A linear rule on slope, relief, position on the slope and curvature (all at scale
    r0) predicts ln R with R2 0.65, residual a factor 1.3. Boosted trees do no better.
  - Recorded c and V of the 12 biggest winners were reproduced by solving each sphere
    alone (`scoops.single`, F = 1): agreement to four figures.
  - Direction proposed to Hig, not yet agreed: search in (x, y, R, volume) with the
    centre height solved from volume, climbing in volume across what are now class
    boundaries (terrain grid chosen by volume), seeded by the radius rule.
- **Prototype of the size-free search (2026-11-20, Hig agreed):** `scoops_dev/vproto.py`
  + `scoops_vclimb` in the dev core. Seeds at 3e9, 1e9, 3e8 m3 on lattices of 0.44 r0,
  R = 3 r0, climbing in (x, y, ln R, ln V) between 1e8 and 1e10 on one 100 m grid.
  Output `multi_pilot/vproto_<site>_<tag>/`. First run, Chugach 20 km block (122_55 of
  the 1e8 class): 938 CPU s for both decades, against about 1,730 for the two
  fine-lattice class runs. Against the better of those per cell: median 0.99, 13 % of
  cells more than 30 % low, 4 % blank, 12 % more than 2 % higher; same maximum
  (1,727 kPa). Winner volumes spread 10^8.0 to 10^9.3 (median 10^8.6), no longer piled
  on a class floor. Cost per surface is 6.5 times the lattice's (the height solve, and
  large surfaces on a grid finer than they need): that is where the speed is to be had.
- **Prototype, second day's state (2026-11-20 evening).** (1) The height solve is now
  Newton from the previous point (`solve_z_from`): the same Chugach run fell from 938
  to 114 CPU s with the same map. (2) `scoops_dev/vlevels.py`: a pyramid of grids, 200 /
  100 / 50 / 25 m, each searching the volumes it resolves (floor at r0 / dx = 6: 6.9e8,
  8.6e7, 1.1e7, 1.3e6 m3) to 20 x its floor; a seed that ends at a level's floor is
  handed down to the next grid; fresh lattice seeds at every level (`--fresh`), only
  within 4 r0 of the core. Two levels on Chugach: 93 CPU s (with seven reference runs
  competing), median 1.00 against the better of the 1e8 and 1e9 fine lattices, 9 % of
  cells more than 30 % low, 2.5 % blank, 20 % more than 2 % higher. Output
  `multi_pilot/vlev_<site>_<tag>/`; compare with `vcompare.py SITE vlev TAG`.
  (3) On the single 100 m grid, Talkeetna's winners pile on the 1e8 floor (median
  10^8.04) and Chena is almost blank at 1e8 and above (0.4 % of cells, max 15 kPa): both
  need the finer levels. (4) Running when this was written: four-level runs on all
  three sites (tag `b`), and fine-lattice references for 1e7 (20 km) and 1e6 (6 km
  blocks) at all three, `multi_pilot/v7_finerec_<site>`, `v6_finerec_<site>`.
  **Paused 2026-11-20 21:40 (power cord trouble).** Done: v6 Chena, v6 Chugach,
  v7 Chena. v6 Talkeetna FAILED (its block write hit Nunatak as the Mac woke from a
  flat-battery hibernation, ~21:25); v7 Chugach and v7 Talkeetna were stopped by hand
  (~1 h and ~10 min into ~4 h single blocks; nothing partial kept). To resume, from
  `tools/terrain/scoops_frozen/`: `./run_refs.sh 6 6 talkeetna=-148.10790,61.99785`
  and `./run_refs.sh 7 20 talkeetna=-148.10790,61.99785` / `chugach=-148.81,61.38`
  (block km as in each `grid.json`: 6 km for 1e6, 20 km for 1e7). Each launcher
  writes `logs/ms_ref<k>_<site>.log`, so the failed v6 Talkeetna log is overwritten.
  Open: how many fresh seeds the fine levels need (`--fresh first|none`), pruning seeds
  where nothing needs cohesion (Chena spends as long as Chugach finding nothing), the
  depth repaint on the pyramid (left out of `vlevels.py`), blocks and seams.
  A trap met on the way: four workers each found the `.so` stale, compiled over one
  another and died, and the parent waited for ever; `scoops.search` now compiles first.
- **Four-level results, three sites (2026-11-20 night; runs `vlev_<site>_b`, Chena `_c`).**
  Share of cells won by each decade (1e9 / 1e8 / 1e7 / 1e6): Chugach 14 / 54 / 27 / 5 %,
  Talkeetna 0 / 9 / 52 / 39 %, Chena 0 / 0 / 22 / 78 %. Cells needing cohesion 99.9 /
  92 / 38 %; maxima 1,725 / 562 / 188 kPa. Chena has 20 % of its cells at the 1.3e6
  floor: it wants a grid finer than 25 m. About 10 million surfaces per 20 km block,
  three quarters of them on the 25 m grid. **Timings from that night are worthless**
  (six reference runs shared the machine); use surfaces as the cost until they are
  redone. Uncontended, the 100 m single-grid run cost 0.065 ms per surface.
  - **Hand-downs alone do not work** (`--fresh first`, tag `f1`): 3 % of the surfaces,
    but 17 % of Chugach, 75 % of Talkeetna and 97 % of Chena come out blank. Seeds
    from the coarsest grid mostly stop one level down (765 -> 57 -> 0 at Chugach):
    small failures are not where large ones shrink to. Fresh seeds at every level
    are needed; the saving has to come from placing fewer of them.
  - Seeds travel a median 0.8 r0 from where they start, twice their spacing (0.44
    r0), and a quarter to a third end on a surface another seed also found.
  - Wasted seeds on the 25 m grid: at Chugach 36 % climb to the level's size cap (they
    want to be larger, which the coarser grids already cover); at Chena 55 % end
    needing no cohesion; Talkeetna about 10 % each.
  - c / (gamma r0) reached, 99th percentile by level 200 / 100 / 50 / 25 m: Chugach
    0.031 / 0.052 / 0.069 / 0.086; Talkeetna 0.016 / 0.024 / 0.036 / 0.049; Chena
    0 / 0.005 / 0.015 / 0.018. Small surfaces reach more for their size.
  - Radius cap at 10 r0 (`VCLIMB_RMAX`, now the default): Chena map unchanged at the
    median, 0.5 % of cells more than 30 % low; whether it saves time is not known.
  - Running when written: seed spacing 0.66 and 0.88 r0 (tags `s66`, `s88`); the 1e7 and
    1e6 fine-lattice references (none finished after about 3 h).
- **First small-size check: Chena against the 1e6 fine lattice (2026-11-20).** On the
  ground both blocks cover (68 % of the 6 km reference block: the tilings do not nest,
  and a first comparison that ignored this reported 43 % blank, which was wrong;
  `vref_small.py` now compares the overlap only): median 0.98, 20 % of cells more
  than 30 % low, 7 % blank, 25 % more than 2 % higher, same maximum. The shortfall is
  the size floor: where the reference's winner is under 1.34e6 m3 (28 % of cells) the
  prototype is 46 % low / 19 % blank; above it, median 0.98 to 1.00, 9 to 10 % low, 3 %
  blank. Chena needs a 12.5 m level. Seeding the search with the reference's own
  spheres on the 25 m grid recovers the map, so evaluation on 25 m is sound.
  The size-free objective below zero (in `vclimb_eval`) made no measurable difference.
  Wider seed spacing against the same reference: 0.66 r0 23 % low / 9 % blank, 0.88 r0
  27 % / 10 %.
- **Docker Desktop crashed at 18:35** ("fs: injecting event blocked for 60s") with
  these runs plus the topobathy session's solve oversubscribing the ten cores. Run
  this work at `nice 19` and keep to about five processes while another session is busy.
- **Three test sites (Hig, 2026-11-20):** Chugach (-148.81, 61.38), southern Talkeetnas
  (-148.10790, 61.99785), Chena (-146.10225, 65.07391). Fine-lattice references with
  winners in `multi_pilot/v9_finerec` (Chugach), `v9_finerec_<site>` (60 km) and
  `v8_finerec_<site>` (20 km), made by `tools/terrain/scoops_frozen/run_refs.sh` (a
  frozen copy of the code, so dev edits cannot recompile under a queued run; delete it
  with `scoops_dev/`). Talkeetna 1e9: 23 % of cells need cohesion, max 1,160 kPa.
  **Chena 1e9: nothing needs cohesion at phi 20.**
- **Tag `n` reruns, all three sites (2026-11-20 evening).** Same maps as before the
  size-free objective. Against the 1e8 fine lattice: >30 % low 2.5 % / 1.7 % / 0 %
  (Chugach / Talkeetna / Chena), blank 0.1 % / 0.4 % / 0 %. 1e7 and the other 1e6
  references were still running (`logs/ms_ref[67]_*.log`).
- **Pruning fresh seeds by relief at the seed does NOT work.** Relief in a 2 r0 window
  at the start point, in r0 units: no threshold drops a useful share of seeds without
  dropping the same share of seeds that end needing cohesion (seeds walk from flat
  ground to the slope). Untested alternatives: rank seeds by their starting
  c/(gamma r0) and climb only the top share; abandon a climb still below zero after k
  moves. Needs the start value in `vout` (do not edit `scoops_dev/scoops_core.c` while
  a `vlevels.py` run is going: `_load()` recompiles at each level).
- **Running at the time of writing:** `vlev_chena_p12` (levels 200..12.5, 2 workers,
  `logs/vlev_chena_p12.log`, ends with `vref_small.py chena 6|7 n p12`).
- **Next:** (depth rule settled for now: deepest winning surface.) Move `dense,dil` and the depth code into
  `scoops/`, delete `scoops_dev/` and `multi_phi20/v10`, then (with Hig's yes) start
  `run_multisize.sh`. Do not resume the statewide run before that.
- Rough cost with the climb, 3 workers: 10^10 about 1.5 h (mostly reading terrain),
  10^9 a few hours, 10^8 about half a day, 10^7 two to three days, 10^6 over a
  week. The coarse lattice is now the cost; ideas not yet tried: a lower search
  ceiling than 10 r0, and skipping small-class nodes where a larger class already
  needs more than the small class can.

## One slope, one failure: `site_failure.py` (2026-11-24)

Hig asked for a plausible large failure on the SW side of Sadie Peak
(-151.304173, 59.472539) from the 8 m KBay topobathy. `site_failure.py peaks` seeds
with the class lattices, then climbs (Nelder-Mead, volume free) on one 16 m grid to
local peaks of required cohesion, groups them by footprint overlap, and with
`--planes` does the same for planes (spheres of 200 km radius, so the same core).
`products --make ID ...` re-climbs on the 8 m grid and writes thickness, the ground
with the mass removed, and the ground before, cropped to the scar + 2 km.
Output: `/Volumes/Nunatak/terrain_build/scoops_state/sites/sadie_peak/`
(`peaks_phi30.json`, `climbs_phi30.json`, `preview_phi30.png`). Run at phi 30 only.
The evaluator is `scoops_climb` with no moves allowed. A full run with planes is
about 25 min on 5 workers, nearly all of it the planes (each scans the whole grid).
Traps: seeds in a wide box go to other slopes (use `--box 2100 --box-off 1400`); the
"on the face" test uses the centroid's bearing from the site, which means nothing for
a failure at the summit itself.

**Friction sweep and the mapped outline (2026-11-25).** Peaks at phi 15, 17.5, 20, 22.5,
25, 30, 35, 40 (`peaks_phi<phi>.json`). `site_polyfit.py` fits spheres of a series of
radii to an outline (`polyfit.json`) and, with `--compare`, scores every free peak
against it (`peaks_vs_outline.json`). **Hig asked for this one use of an inventory
outline (record 1558, Sadie Peak); the standing rule is otherwise unchanged.** Found:
the outline is matched (IoU 0.85+) only by spheres of R 1.4 to 2.3 km, 4 to 7e8 m3;
those need no cohesion at phi 30; the free search has a local peak like the outline
at phi 20 (4.25e8 m3, 525 kPa, IoU 0.70), a weak one at 22.5 that shrinks when
re-climbed at 8 m, none at 25 and above, and at 17.5 and below the peak outgrows it.
Hig's reading of 20 deg + 525 kPa: phyllite carries many failures here, and the cohesion
is plausibly rock bridges plus permafrost strengthening the upper part of the surface.
The solver is dry, so 20 deg is an effective value.

## The question (Hig's framing)

"How strong must the slope be in order to be so steep?" This looks at the
driving force only: no rock strength is supplied. Every trial surface gives a
requirement, and we ask what strength the observed topography demands.

**Standing rule: no comparison with landslide mapping.** Keep inventories,
landslide polygons and slide-chosen sites out of this work until Hig lifts the
rule, to avoid confirmation bias. The windows below were chosen without any
landslide knowledge.

## Method

- This is a rebuild of Scoops3D from the USGS manual (Reid et al. 2015, TM 14-A1).
  It uses spherical trial surfaces over DEM columns and the Ordinary and Bishop
  methods, with γ = 26 kN/m³, dry and with no seismic load. The required
  cohesion at F = 1 is c = τ − σ tan φ, where τ and σ are moment-weighted means
  over the surface.
- The Fortran source was not compiled, because www.usgs.gov returns 403 to
  curl. Benchmarks against the USGS reference data pass (`benchmark_usgs.py`,
  `check_identities.py`; data in `/Volumes/Nunatak/terrain_build/scoops_ref`).
- The core is C called through ctypes (`scoops_core.c` → `_scoops_core.so`).
  Numba is broken on this anaconda. `scoops.py` `_load()` recompiles the `.so`
  whenever the `.c` file is newer. If compiling fails, the Xcode licence may
  have been revoked by an update (see memory).
- Volume classes are set by r0 = 1.36 V0^(1/3): 10⁶ m³ gives 136 m, 10⁷ gives
  293 m, 10⁸ gives 631 m, 10⁹ gives 1360 m and 10¹⁰ gives 2930 m. The search
  lattice uses dz = r0/2 and dr = r0/4, with a fine pass at 1/3 of that near
  demanding nodes.
- **The grid spacing is not purely numerical.** Keep r0/dx ≈ 6.5 for every
  class, which gives dx = 20, 45, 100, 200 and 450 m. At Chugach 10⁸, going
  from dx 100 to 50 m reads c within 1 % at φ 0, 8 % low at φ 30 and 20–30 % low
  at φ 40, which is about 1° of friction.

## Lines of work, and where each stands

1. **Required-cohesion maps for one (φ, size) pairing** (`statewide.py`,
   `bake_required.py`). The runs are in `/Volumes/Nunatak/terrain_build/scoops_state`.
   - `phi20_200m`: 10⁹–10¹⁰ m³, **complete** (139/139 blocks). It is baked to
     `/Volumes/Nunatak/terrain_build/pmtiles/sp_creq_phi20_200m.pmtiles` and
     shown as a dev overlay row (`sp-creq-20-200`). That row is uncommitted and
     lives in `map.js` `_OV_CATS` under *Surface → Required strength
     (exploratory)* and in `views._RAMP_SPECS`.
   - `phi30_100m`: 10⁸–10⁹ m³, **stopped at 15/139 blocks**. Rerunning
     `run_both.sh` resumes it from `done_blocks.txt`. After it finishes, run
     `--mosaic` and rebake, because partial bakes leave holes.
   - Hig wants this as a **map overlay only, not in the analysis panel**.
2. **Painting and depth.** The question was which cells a surface colours and
   how deep it is (`paint_cases.py`, `paint_figure.py`, `paint_max_figure.py`;
   windows in `scoops_state/cases`, `scoops_state/cases_max`).
   - Hig pointed out that depth must be the maximum over overlapping surfaces.
     `h_any` is the maximum over every surface with c > 0. `h_win` is the
     maximum over surfaces holding the top c in at least one cell, which needs a
     second search pass and so about twice the run time.
   - The recommendation was h_win, painting D as the main map, and E shown
     separately.
3. **The envelope** (latest work). This asks what (σ, τ) the terrain requires,
   by size.
   - `envelope.py` records (σ, τ) for every surface and keeps the most demanding
     surface per tile of 10 r0.
   - `run_envelope.sh` covers 6 windows × 5 sizes: the full 150 km window for
     10⁸–10¹⁰ m³, and 12 random patches of 40 r0 for 10⁶–10⁷ m³. Patch positions
     come from the crc32 of the window name.
   - Output goes to `scoops_state/envelope/windows` (30 npz files, 8.7 h in
     total). Figures are made with `envelope_figure.py DIR PREFIX` and
     `envelope_summary.py DIR OUT.png` and are in `scoops_state/envelope/figures/`.
     Start with `summary.png`.
   - The windows are listed in this table.

     | Window | Lon, lat |
     |---|---|
     | chugach | −148.81, 61.38 |
     | coast | −135.6072, 59.2779 |
     | stelias | −141.5778, 60.3569 |
     | kodiak | −153.2389, 57.5068 |
     | alaskarange | −150.5, 63.1 |
     | brooks | −151, 68 |

## Envelope results

- **The pilot's c/(γ r0) collapse did not hold.** The pilot was one steep
  block. Across whole windows, c/(γ r0) falls about 20–30 % by 10⁸ m³ and then
  roughly halves per decade.
- **There is no single τ(σ) per size.** Each class rises steeply at its shallow
  edge and rolls off at its deep edge. Both edges come from geometry, not rock.
  So only the **upper hull across sizes** bounds strength.
- **The hull is concave.** At p90 of tiles, σ of 0.2, 0.4, 0.8, 1.6, 3.2, 6.4 and
  12.8 MPa gives τ of 0.29, 0.53, 0.93, 1.55, 2.47, 3.64 and 4.56 MPa.
  - The no-cohesion φ falls from 56° to 20° along it, and τ goes roughly as
    σ^0.7.
  - Linear fits give about c 0.2 MPa + φ 40° for σ 0.4–1.6 MPa, and about
    c 0.9 MPa + φ 24° for σ 1.6–6.4 MPa.
  - The last doubling is probably limited by relief.
- **The windows fall in two groups.** Coast, Chugach, St Elias and the Alaska
  Range sit 30–45 % above Brooks and Kodiak at σ ≥ 1.6 MPa.
  - At shallow σ the Alaska Range joins the low group.
  - At matched relief/r0 the contrast survives at 10⁶–10⁸ m³ and mostly vanishes
    at 10⁹–10¹⁰ m³.
  - Coast and Brooks do not overlap at 10⁶–10⁷ m³ in a bootstrap by patch.
- **Most of the large-size decline is relief.** Within a relief/r0 bin, φ0
  barely changes with size. This check is partly circular, because relief/r0 is
  close to the tile gradient.
- **Demand at φ = 30° peaks around 10⁸ m³.** p99 is about 0.8–1.0 MPa in Coast,
  Chugach and St Elias, 1.4 MPa in the Alaska Range and 0.2–0.4 MPa in Brooks
  and Kodiak.
- **Caveats:**
  - It is a lower bound. It equals the strength only where the terrain has been
    cut to its limit.
  - Each range is one window.
  - Glaciation, uplift, relief and lithology are all confounded.
  - Ice is treated as rock, and glaciers hide valley depth.
  - The sea is cut at 0 m, which truncates fjord walls.
  - Use p90 rather than p99, because of DEM artefacts.
  - The cut-down search lattice makes friction read about 1° low.
  - The vertical stripes in the 10⁹–10¹⁰ m³ clouds come from the discrete
    search lattice.
- **Reading `env_cloud.png`.**
  - The black line is p99 of the tiles and the gray line is p50. The summary
    numbers above use p90.
  - The dotted diagonals are 30°, 40° and 50°, unlabelled.
  - The cloud uses Ordinary τ, while the cohesion tables use Bishop.
  - The x axis stops at 30 MPa, but the 10¹⁰ m³ data reach about 40 MPa.

## Awaiting Hig: do not start without a yes

- **Proposed next step:** about 6–10 more windows, chosen to separate
  glaciation from relief and rock type. At about 1.5 h per window, this would be
  an overnight run.
- **On hold:**
  - a depth rule
  - a stricter winning rule (a surface currently qualifies on one cell)
  - a statewide rerun at 200 m with the D, E and depth maps
  - the 100 m statewide run
- **Unanswered:**
  - whether to compile the USGS source if Hig supplies it
  - whether to remove the old `sp_slope` archive from R2
  - the overlay background
- When this pipeline is committed, it needs CLAUDE.md notes. If it ever ships,
  the Methods page needs updating too.

## Resuming after a restart

- Nunatak must be mounted. The dev overlay is a symlink:
  `data/overlays/sp_creq_phi20_200m.pmtiles` points to
  `/app/nunatak_overlays/…` through the local-only `docker-compose.override.yml`
  mount, which must never be committed.
- Check free disk space and swap before any long run. As of 2026-11-05 the boot
  disk had 131 GB free and Nunatak 193 GB.
- Long runs use 6–8 workers. Never send bulk output to stdout.
- The `timeout` command is not available on this Mac.
