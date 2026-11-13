# Terrain comparisons under `/lidar/` — plan for review (2026-11-12)

Status (2026-11-12): **the phase 0 spike grew into a usable tool and was
deployed** as `/lidar/compare/` (`pages/lidar_compare.html`,
`lidar_serve.compare`): URL-only state, nothing stored, public, Start button
on `/lidar/` for editors. What it does and what was learned building it is
§11. Still to do from this plan: the `lidar_scene.js` extraction (§8),
server-side sheets with owners, publish and branch (§6, phases 2–4), the
pane cap and the shading-throughput options in §11. Hig answered the open
questions on 2026-11-12; the decisions are in §10 and folded into the text.

## 1. What it is

A *comparison* is a sheet of up to N small 3D (or map) views of different
places, drawn so they can be read against each other: same ground scale, same
tilt, same vertical exaggeration, same shading or image source, same pane
size. Each pane keeps its own place, its own compass direction and its own
title. Moving one pane moves them all.

- An editor collects views while browsing `/lidar/`, opens the draft, frames
  each pane, saves.
- Anyone with the link sees the saved sheet and can navigate it; all panes
  move together. Nothing a visitor does is stored.
- Another editor can branch a sheet into their own copy and publish that.

Working name **Comparisons**, at `/lidar/compare/`.

## 2. What is shared and what is per pane

| | Shared by every pane | Per pane |
|---|---|---|
| Ground scale (m per screen px at the view centre) | yes | |
| Tilt (pitch) | yes | |
| Terrain exaggeration, 3D on/off | yes | |
| Shading: Hillshade / Preset / Slope, or an image (survey ortho, or a basemap from `basemaps.js`) and every shading slider | yes | |
| Base layer (one basemap from `basemaps.js`, e.g. an image source) | yes | |
| Opacity of the shading over the base layer | yes | |
| Pane size in pixels | yes (the grid enforces it) | |
| Centre (lat, lon, centre elevation) | | yes |
| Bearing | | yes |
| Survey (`ds`) | | yes, as it was when submitted |
| Title | | yes |

**Same scale is not same zoom.** Web-Mercator zoom means a different ground
resolution at every latitude (Utqiagvik vs. Ketchikan differ by a factor of
1.7). The stored quantity is metres per pixel; each pane's zoom is derived
from it and its own latitude, `z = log2(C·cos(lat) / res)`, and recomputed as
the pane pans north or south. This is what Horowitz's same-scale map does.

**Base layer and opacity** (Hig, 2026-11-12). Every pane has the same base
layer under the shaded survey, draped on the terrain in 3D, and one opacity
slider in the key fades the shading over it in all panes at once. Setting the
base to imagery and moving the slider goes back and forth between image and
shaded surface everywhere together.

**Sun direction.** Because bearings differ, a fixed true azimuth lights a
north-facing pane and a south-facing pane differently, which defeats a
slope-to-slope comparison. Default: the sun is fixed **relative to the view**
(upper left of every pane), so each pane's true azimuth is `az + bearing`.
A switch in the key gives true azimuth instead. View-relative sun means a
rotation re-shades the tiles, so that happens when the gesture ends, not
during it.

## 3. Navigation

A gesture in any pane is reduced to four changes and applied to all:

- **pan**: the displacement in the driver's *screen frame*, in metres. Each
  follower moves the same screen vector, rotated by its own bearing. Dragging
  left moves every pane's ground left, whatever way it faces.
- **zoom**: one shared metres-per-pixel value.
- **rotate**: the same change in bearing added to each pane.
- **tilt**: one shared pitch.

Two modes:

- **Frame** (owner only): pan and rotate act on the one pane, to set its
  place and direction. Zoom, tilt and style stay shared; they are never
  per-pane, which is the point of the tool.
- **Together** (everyone; the only mode for visitors): all four are shared.

**Locks are the creator's working aid** (Hig, 2026-11-12). The key carries a
lock per shared property: scale, tilt, exaggeration, rotation, position,
style. While a property is locked, the creator's own gestures and controls
cannot change it: lock tilt at 35°, then keep working on look direction
without the drag nudging the tilt. Locks are saved with the sheet so they are
still set when the creator returns to it. They do not constrain visitors, who
can always navigate and have a **Reset** button back to the saved view.

**Rotation in Together mode turns every pane by the same amount**, which is
how a creator finds one look perspective that is a fair compromise across all
panes.

## 4. The page

```
┌ title · by · branched from …            [Frame|Together] [Save] [Branch] ┐
│ KEY: scale bar │ tilt 35° 🔒 │ VE ×1.5 │ Preset ▾ (colour key) │ sun ↖   │
├───────────────┬───────────────┬───────────────┐
│ Title A   N↗  │ Title B   N↓  │ Title C   N←  │   title strip is outside
│               │               │               │   the map frame; it holds
│   (map only)  │   (map only)  │   (map only)  │   the title and a north
├───────────────┼───────────────┼───────────────┤   arrow, nothing else
│ Title D       │ …             │               │
```

- **One key for the sheet**: scale bar (true for every pane's centre), tilt,
  exaggeration, the colour key of the active shading, sun, locks, credits
  (the union of the panes' survey attributions). Panes carry nothing: no
  controls, no attribution, no traces, soundings, water or footprints.
- The north arrow sits in the title strip because bearings differ and a
  reader needs it; it is outside the map box.
- **Right-click / Ctrl-click in any pane** copies `lat, lon` with the same
  format and toast as `/lidar/` and `/inventory/`. The handler moves out of
  `lidar_preview.html` into a small shared file so all three use one.
- Grid: columns chosen by the owner (default `ceil(sqrt(n))`), all panes the
  same pixel size. On a narrow screen it collapses to one or two columns.

## 5. Collecting views on `/lidar/`

Editors get **＋ Add to comparison** in the viewer's sidebar. It asks for a
title and appends the current view (centre, centre elevation, bearing,
survey) to the editor's open draft, creating one if there is none. A small
tray lists the draft's panes (rename, remove, reorder) with **Open draft →**.
The draft is a server-side row, so it survives a reload or a change of
machine, as staged polygons do. The first pane's scale, tilt, exaggeration
and shading become the sheet's shared values; later submissions contribute
only place, direction and survey.

## 6. Storage and permissions

New Django app `compare/` (SQLite, like `drops/`; needs its volume mount
line in `docker-compose.override.yml`).

`Comparison`: `slug` (random token), `title`, `note`, `owner` (FK User),
`parent` (FK self, `SET_NULL`) + `parent_title` text so the credit survives a
deleted parent, `status` (draft / published), `state` (JSON, below),
`created_at`, `updated_at`.

```json
{"v": 1,
 "shared": {"res": 2.4, "pitch": 35, "ex": 1.5, "t3d": true,
            "mode": "preset", "base": "esri-img", "op": 0.8, "shade": {"az": 315, "alt": 45, "…": 0},
            "sun_rel": true, "cols": 3,
            "locks": ["tilt", "style"]},
 "panes": [{"title": "Barry Arm", "lat": 61.15, "lon": -148.15, "ce": 420,
            "bearing": 110, "ds": "barry_arm_2020"}]}
```

| Action | Who |
|---|---|
| View a published sheet, navigate it | anyone with the link |
| See a draft | its owner, other editors |
| Create, add panes | `inventory_editors` + superuser |
| Save, publish, delete | the owner (superuser may also) |
| Branch | any editor; the copy is theirs, a draft, credited "branched from … by …" |

Routes: `/lidar/compare/` (index: published sheets, plus the caller's
drafts), `/lidar/compare/<slug>/`, and JSON under `/lidar/compare/api/`
(`<slug>/` state, `create/`, `<slug>/add/`, `<slug>/save/`,
`<slug>/branch/`, `<slug>/delete/`). `/lidar/` is already disallowed in
`robots.txt`, so sheets are reachable by link, not by search.

**Gated surveys** follow the default-view rule of 2026-10-30: store the sheet
as set, warn the owner on save, serve each visitor the state minus what they
cannot see. A pane whose survey a visitor may not see is served with `ds`
removed and draws the public context terrain (Mapterhorn) under a plain note
in its title strip. The survey id never reaches them. `owner_only` surveys
get the same treatment one tier up.

## 7. Rendering and cost

**One MapLibre map per pane.** It reuses the viewer's whole stack and gives
real interaction in every pane. The alternative, one hidden map rendered into
N canvases in turn, cannot navigate smoothly in 3D; it is right for the PNG
export and thumbnails (it is what `ls_export.js` already does), not for the
live page. Embedding `/lidar/` N times in iframes was also considered: N full
copies of a 2,500-line page with no shared caches.

What bounds it and what is done about it:

- **WebGL contexts.** Browsers cap live contexts (about 16 in Chrome, fewer
  on phones) and silently drop the oldest. Cap panes at **9**, pending the
  spike's measurements. On context loss a pane shows a still and restores on
  click.
- **Total pixels are about one full-screen map.** Nine panes on a laptop are
  ~440 × 280 px each: 2–6 DEM tiles a pane at 512 px. Tile traffic is roughly
  1.5× a single `/lidar/` view, not 9×.
- **Shared caches.** All panes live in one page, so the shading package's
  decoded-elevation and finished-tile caches are shared. Known fault to deal
  with first: its URL-keyed cache hands a second map a buffer already
  transferred to the first map's worker (the `&export=1` workaround in the
  PNG export). Each pane's tile URLs get a pane key, or the package is fixed;
  this is the same issue already owed to the package owner.
- **Followers move in the driver's frame.** The driver's `move` event applies
  `jumpTo` to the others under a re-entry flag; no easing, `fadeDuration: 0`.
- **Slider changes** re-shade N sources once per animation frame (the
  viewer's existing throttle).
- **Only visible panes are live.** An IntersectionObserver parks panes
  scrolled out of view (no sync, no tile requests); they catch up on return.
  Cold load creates maps in sequence, visible ones first.
- **Lean panes**: no vector basemap or glyphs in the shading modes, no fog,
  no live context fill, small per-map tile cache, device pixel ratio capped
  at 2.
- The 3D camera needs the centre elevation (`ce`) and the link-camera hold
  the viewer gained on 2026-10-19; each pane uses the same mechanism.

**The risk to settle first: "same scale" in 3D.** With terrain on, MapLibre's
zoom fixes the camera's distance to the centre point at the centre's
elevation. Equal metres-per-pixel should therefore mean equal camera distance
in every pane, but that needs measuring, not assuming: a known ground length
drawn in panes at different latitudes, elevations and exaggerations must span
the same pixels.

## 8. Shared code

The survey registration, shading sources, terrain set-up (including the
terrain-floor workaround) and camera hold are inline in
`lidar_preview.html`. The compare page needs them N times. They are
extracted, behaviour-identical, into `lidar_scene.js`
(`LSLidarScene.attach(map, opts)`), with `/lidar/` as the regression
baseline, and then used by both pages. This follows the repo's shared-module
rule and is the largest single piece of work.

Sequencing: `lidar_preview.html` had uncommitted work when this was written
(the point-cloud link and other gating work). The extraction starts after
that is settled, or it will tangle with it.

## 9. Phases

Each phase goes dev → Hig tests → ship, as usual.

0. **Spike** (dev only, thrown away). A hard-coded page of 4, 6, 9 and 12
   panes: the same-scale and linked-navigation maths, the 3D scale check of
   §7, frame rate and memory on Hig's Mac and one modest laptop, the
   transferred-buffer fault. Output: the pane cap, and a yes/no on one map
   per pane.
1. **Extract `lidar_scene.js`** from the viewer. No visible change; checked
   against the viewer's existing links (3D `ce=` links, difference, export).
2. **Compose.** The `compare` app, the tray on `/lidar/`, the sheet page for
   its owner with Frame/Together, the key with its locks, the shared base
   layer and opacity, save.
3. **Publish.** Visitor view, Reset, right-click copy, gated-survey
   fallback, a section in `/lidar/methods/`, analytics events
   (`compare_open`, `compare_save`).
4. **Derive and share.** Branching, the index page with thumbnails, PNG of
   the whole sheet through `ls_export.js`, a link that carries a visitor's
   navigated state.

Not in the first version: the difference mode, traces/soundings/water in
panes, profiles, per-pane shading.

## 10. Decisions (Hig, 2026-11-12)

1. **Creators** are `inventory_editors` and superusers.
2. **Locks** are a creator's working aid during revision (§3), not a
   restriction on visitors.
3. **Sun** is view-relative by default, with a true-azimuth switch.
4. **Nine panes** for now; Hig wants to see performance at nine.
5. **A gated survey** in a published sheet falls back to public terrain with
   a note for visitors who may not see it.
6. **Rotation** in Together mode turns every pane equally.
7. **Survey per pane** is the one active at submission, changeable in Frame
   mode. **Base layer and shading opacity are shared** across panes (§2).

## 11. Phase 0 results (2026-11-12)

Spike at **`/lidar/compare/spike/`** (dev only, 404 unless DEBUG;
`pages/lidar_compare_spike.html`, `lidar_serve.compare_spike`). URL params:
`n` panes (1–12), `sh` preset|hillshade|slope|none, `m` frame|together, `res`
m/px, `p` tilt, `ex` VE×10, `bm`, `op`, `sr`, `az`, `lk` locks, `pn` panes,
`cache` shading-cache size. The perf box reports what is below.

Measured in Chrome on Hig's Mac (automated tab, ESRI base, hillshade, 3D,
tilt 50°, 3 m/px, laptop window ≈ 1520 × 780):

| | 1 pane | 4 panes | 9 panes |
|---|---|---|---|
| shaded tiles, cold | 86 | ~160 | 274 |
| cold load until every pane idle | 5.2 s | 7–9 s | 27–29 s |
| every pane idle after a 180 px pan | — | 0.19 s | 7.7 s |
| frame rate during a scripted drag | — | 38 fps | 35 fps |
| idle frame rate | 120 | 120 | 120 |
| JS heap | 40 MB | 50–100 MB | 70–220 MB |
| WebGL contexts lost | 0 | 0 | 0 |

- **Same scale in 3D holds.** Measured through the camera (distance from
  MapLibre's camera position to the centre at its elevation ÷ the optical
  distance in pixels): ×0.999 in every pane at VE 1 and 2.5, at 55°N and
  61°N, after pans, zooms and rotations. The condition is that each pane's
  centre sits on the ground; the spike re-solves it (`settle`) holding the
  zoom, where `/lidar/`'s tracker holds the camera and re-solves the zoom.
  The first instrument (rays cast ±N px onto the terrain) read relief, not
  scale (×1.95 on a 12 m rise beside the centre) and was dropped.
- **Linked navigation is exact.** A pan moves every pane the same metres in
  its own screen frame (9 panes, 544 m each for a 180 px drag); rotation adds
  the same angle to every pane; zoom gives one m/px at every centre (55°N to
  61°N). Frame mode moves only the dragged pane with tilt and scale still
  shared; the tilt lock holds.
- **The bottleneck is shading throughput, not WebGL.** Nine panes need ~3×
  the tiles of one full-screen view, not ~1.5× as §7 guessed: each tilted
  pane loads its own horizon. Time grows faster than tiles (3.2× tiles, 5×
  time), which points at the package's **single worker**. Enlarging its
  caches (220 → 600) changed nothing cold (27.3 vs 28.8 s); they matter only
  for revisits, and with per-pane URL keys a pane can never reuse another's
  shaded tile.
- **Trap found (keep for the real build):** `jumpTo()` on a map stops a
  gesture in progress on that map. Correcting the dragged pane's zoom for its
  latitude every frame killed a slow drag after one frame. The dragged pane is
  now left alone mid-gesture and corrected when the gesture ends.
- Not measured: a person's drag (the scripted one drives events from rAF in
  a background tab; the frame rate above is likely pessimistic and should be
  judged by hand), a second machine, phones.

**Options, to decide after Hig has tried it:**

1. A pool of shading workers in the package (2–4), the direct cure for the
   settle time. A package change, shared with trailgeek.
2. Fewer horizon tiles per pane: lower the per-pane tile budget at a tilt, or
   cap the pitch at ~60° in the tool.
3. Share shaded tiles across panes where URLs coincide by fixing the
   package's transferred-buffer fault instead of keying URLs per pane.
4. Cheaper panes: device pixel ratio 1 in panes, hillshade before preset
   (preset at 3 m/px is mostly mod-5 bands anyway).

**Round 2 (2026-11-12, after Hig's first try: "pan and shift perspective
… only moves one discrete jump", "other panels reload even on a simple
pan").** Fixed in the spike, each a rule for the real build:

- **A drag released outside its own pane never ended** (MapLibre ends a mouse
  drag only on a mouseup aimed at its own map; in a grid most drags end over
  a neighbour). A document-level mouseup is forwarded to any pane still
  mid-gesture.
- **Page-wide text selection was re-enabled mid-drag** by every linked
  `jumpTo` (MapLibre's handler reset calls `enableDrag`). The page is
  `user-select: none` except editable titles.
- **Every pane lurched when a pan ended**, re-seating its centre on the
  ground. Now followers get the ground elevation with each linked move, and
  the dragged pane through `_getTransformForUpdate` / `_applyUpdatedTransform`
  (the package tracker's route, which does not stop the gesture). Measured:
  every centre within 1 m of the ground at release, no settle jump.
- **The per-map tile cache was capped at 80**, below what a tilted pane shows
  at once, so a pan evicted tiles still on screen. Cap removed; a small
  there-and-back pan now costs 5 network requests, all panes idle in 0.65 s.
- Followers whose camera did not change are no longer re-jumped.
- Cost: frame rate during a nine-pane drag 27 fps (35 before), the price of
  following the ground every frame. The stall itself was not reproduced by
  scripted input; the perf box now shows the gesture state if it recurs.

**Round 3 (2026-11-12, Hig): survey pull-down.** Lists by default only the
surveys whose footprint (real outline, not bbox) reaches the lower two thirds
of the pane (the top of a tilted pane is horizon), with *Show all surveys…*
and *Context terrain only*. **Auto** is each pane's default: when the centre
is where the pane's survey has no data (a new location, or panned off its
edge) it takes the finest, then newest, survey covering the centre, or the
context terrain if none does. A survey picked by hand is pinned (`m` as the
sixth `pn` field) until Auto is chosen again. Checked: a pane added from a
`/lidar/` link with no survey on, over Homer, took `homer_2019` (0.5 m, over
the 1 m NCMP flights); a pane moved from Maynard to Portage switched to
`portage_2020`; a pinned pane moved off its survey kept it.

**Round 4 (2026-11-12, Hig): titles.** A page title above the key (`t=`,
also the browser tab's name) and per-pane titles (fifth `pn` field), both in
the link only, editable in Frame mode (dashed underline; Enter or clicking
away commits; emptied reverts to the default: "Untitled comparison" / the
survey's name), read-only in Together. Auto ranking stays finest, then
newest (Hig: "the user can adjust as needed"). Checked by typing both and
reloading from the copied link.

**Round 5 (2026-11-12, Hig): scale bar, north arrow, hide controls, PNG
export.** Scale bar and north arrow are drawn on a canvas and laid on the
terrain as an `image` source with four ground corners, so MapLibre drapes
them: foreshortened by the view, bent over relief; lengths are horizontal
ground metres. Split per Hig: the **spec is shared** (one *Scale bar & north
arrow…* panel in the key: on/off, length + presets, segments, bar weight,
label size, colours, arrow size; URL `sb=`, `na=`), the **placement is per
pane** (dragged in any pane while the panel is open; URL `sp=`, `np=`). The
bar's orientation is **relative to the pane's look direction**, default
perpendicular, set per pane by a small *bar ∠* control at the pane's bottom
left (−90…90°), so it stays square to the view as panes rotate. While the
panel is open a press on a bar or arrow drags it (capture-phase listener on
the pane, ahead of MapLibre) and any other press works the map; *Done* fixes
them. *Re-place in view* resets every pane's spots. Checked: drag moved the
bar exactly with the cursor and not the map; a press off it panned; after
Done a press on it panned.

*Hide controls* leaves page title, pane titles (with north indicators), the
panes and credits; a *Show controls* button brings them back. *Export PNG*
(2× / 4×) sets each map's pixel ratio to the scale and rebuilds the shade
and base sources with tileSize 256/scale (deeper tiles, real detail), waits
for every pane's `idle`, then composites each pane with `map.redraw()` +
`drawImage` as laid out on screen, under the page title and a key line
(scale, tilt, VE, shading, base, sun), with the credits below, and restores.
Checked: 4 panes at 2× → 3504 × 1994 px in 16.5 s, every pane, bar and
arrow present.
