# Required strength from topography (Scoops3D rebuild): exploration state

Status as of 2026-11-05. **Exploratory, uncommitted, dev only.** Nothing here
has been pushed or deployed.

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
