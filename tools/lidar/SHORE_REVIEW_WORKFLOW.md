# Shore review workflow: fine-tuning blocks by hand (draft, 2026-11-03)

Hig's ask, reviewing the contour shore model's sixth test: rough out the full workflow for using the review files and
contours to fine-tune blocks, including contours further up into the intertidal, and deleting or adding closed contours
and having them interpreted successfully.

Nothing here is built except where marked **exists**. Hig's decisions are in section 9.

## 1. The idea

The model makes a first surface from the data. A reviewer corrects what the model cannot know, by editing **facts on a
map**, not the surface. The edits live in version-controlled files located by coordinates, so they survive every rebuild
and apply to every block whose grid holds them. Every edit is checked after the next run, and anything the model could not
honour is reported rather than dropped.

Two rules follow from Hig's reviews so far:

- **Editing a contour says something; leaving one alone says nothing.** An untouched model contour is not a constraint,
  so better data can still move it later. Only what the reviewer changed, added, deleted or explicitly accepted is held.
- **A deletion must be an instruction, not an absence.** The model derives contours from data, so removing a line from a
  file would simply bring it back on the next run. Deleting means recording what is wrong.

## 2. What wins

Highest first. Each level overrides those below it; every override is listed in the run's report.

| Rank | What | Notes |
|---|---|---|
| 1 | Reviewer's `remove` | Withdraws evidence inside a closed line, including any of the sources below |
| 2 | Observed surfaces (lidar, multibeam, SfM) | Reviewer lines do not move these. A conflict is reported; to overrule one, use `remove` |
| 3 | Reviewer's lines and closed contours | Beat 3DEP, ArcticDEM and casts where they meet |
| 4 | 3DEP land above the tide's reach, ArcticDEM (lower-of rule) | **exists**: 3DEP land beats a sounding (Hig, 2026-11-03) |
| 5 | Casts, at any position the reviewer gave them, and the lines they trace | **exists**; moves new |
| 6 | The model: rim slope, rollover, basin, gap blur | **exists** |

## 3. The edit files

All in EPSG:6334, under `tools/lidar/water/`, matched by location. All carry `who`, `date`, and a free-text `basis`
("Maxar 2019-08-02 at +1.2 m tide", "my reading of the trough"), so the product's provenance can name them.

### `tide_lines.geojson`: contours (**exists** for open lines; the rest is new)

| Geometry and properties | Means | Status |
|---|---|---|
| Open line, `level_m` at or above the base | The surface passes through this level here | exists, never run |
| Open line, `level_m` up to the top of the tide | Same, further up the intertidal | exists for the fit; the room rule below is new |
| Open line, `level_m` below the base | A bathymetric contour: sites in the basin's fit | new (today ignored below the base) |
| Closed line, `op: add`, `level_m`, `inside: up` | An island or shoal: everything inside stands at or above the level | new |
| Closed line, `op: add`, `level_m`, `inside: down` | A pit, pool or basin: everything inside stands at or below the level | new |
| Closed line, `op: remove` | The feature here is wrong: see section 5 | new |
| Any line, `op: accept` | Hold this model contour as it stands | new |

### `cast_edits.geojson`: decisions on single soundings (new)

`cast_edits.py apply` also moves the casts in the dev database, which the dev viewer's soundings draw from (geometry only, the depth stays; dev only, never production). A cancelled move goes back. `load_soundings` replaces a survey whole, so run `cast_edits.py sync-dev` after one.

Points carrying the sounding's `survey` and `id`, so a decision follows the cast rather than a nearest-neighbour guess.

**A cast is moved, not rejected** (Hig, 2026-11-03): NOAA's casts are unlikely to be truly wrong, they are in the wrong
place. So the main decision is `move`: the point sits at its new position and the file records the translation, `x0`, `y0`,
`dx`, `dy`, with `basis`. The depth never changes, and the published source files are never touched; the model applies the
translation when it reads the casts. In QGIS this is simply dragging the point in a copy of the review layer, which carries
each cast's original position, and the diff tool writes the record.

The other decisions are `use` (keep what the spike filter dropped) and `questionable` (a note: the cast stays as it is and
stays flagged until someone moves it). A cast the model overrides (3DEP land wins) needs no decision to stay overridden. It stays on
the worklist until it has been moved somewhere its depth makes sense or left as `questionable`.

**A depth is changed only by reading the survey's own sheet again** (`reread`, Hig, 2026-11-05). "The depth never
changes" is about our opinion of the sea bed; it does not protect a copying error. NCEI's digital soundings were keyed
from the smooth sheets, and where the keyed number disagrees with the sheet, the sheet is the measurement. The first case:
`nos_h05187:55404` (59.16419, -151.51308), 0.9 m among 11-13 fathoms with no rock on the chart; the 1931 sheet shows a
plain "12" there, keyed as "1/2" fathom. `cast_edits.py reread KEY DEPTH_M --basis "..."` records it, and `apply` writes
the sheet's depth into the edited CSV (the digital value kept in `depth0`) and into the dev database (`depth_orig` keeps
the digital value, the note the basis). The rule for using it: look at the sheet (NCEI serves a 400 dpi scan per survey,
`Smooth_Sheets/<id>.tif.gz`, and the original records with their type and cartographic code, `GEODAS/<id>.a93.gz`), match
the digitized soundings around the record to the handwritten numbers until only one number is left for it, and write
that down as the basis. A depth that merely looks unlikely is no reread: check first whether it lies along a shore or at a
charted rock, where a lone shallow cast is what one expects.

Each survey's recorded translations are shown together, as arrows, with a note when moves on one survey line disagree. That
is the prompt to look at the whole line, and the same records are the raw material for the survey-distortion tool if it is
ever built (BAY_SOURCES 8.6a).

### `cast_line_edits.geojson`: the troughs and ridges (new)

`reject` a line the detector drew, or `add` one with a depth at each end, for a trough the casts only suggest.

## 4. How lines are interpreted

**Contours up the intertidal.** Today an edited line pulls on the base contour and fixes the surface where it falls in
the intertidal. What is missing:

- *Room.* A line at level L needs the contour below it to lie far enough seaward. The no-wall rule (12 degrees from higher
  ground to the base) has to count edited lines as higher ground, or a high line in the sea stands as a wall.
- *Precedence.* A line must clear 3DEP's fixed cells near it (within about 20 m), or the two fight. Observed cells stay; a
  conflict is reported.
- *Stacking.* Several levels on one flat are simply more fixed values in the same harmonic solve, so a reviewer can give
  only the MHW line, or all five. Levels offered: base, MLLW, 0, MHW, MHHW, or any value.
- *Ends.* An open line that stops in open water holds what it touches and lets the model take over beyond its ends over
  about 40 m. A line that crosses a lower or higher line is reported as a conflict.

**Closed `add`.** The ring is fixed at its level. Inside, a surface rises (`up`) or falls (`down`) from it: a low dome by
default, or to `crest_m` if given, or the 3DEP or observed heights inside it where those agree with the direction. The
outside gets a shore face down to the base. A ring small enough to fall below the grid's useful resolution (under about
200 m2) is refused with a message rather than becoming a speck.

**Which way is up.** A closed contour alone does not say which side is high. Rather than guess silently, `inside` is
required in the file, and the tool defaults it from the current surface so the reviewer normally only confirms it.

## 5. What `remove` does

Inside the ring, for the next run:

- 3DEP and ArcticDEM no longer stand as land or hold the contour.
- Casts at or above the base stop holding the contour there and are marked `overruled_by_edit` in the review file, and stay
  on the worklist to be moved. Their depths are not thrown away.
- `sources: [...]` optionally names observed surfaces to withdraw too (the Chugachmiut sea-as-ground case). Without it,
  observed cells stay and the conflict is reported.
- The space becomes ordinary sea or flat, modelled from whatever is left.

## 6. One block, start to finish

1. **Baseline.** The block has a run under the contour model, with its gate clean or its failures understood.
2. **Open the review.** One command prepares the block's layers (section 7).
3. **Walk the coast** with the worklist, in order of how much each item matters:
   - contradictions between data and surface: casts `contradicted`, then `near_land`, then `spike`
   - unsupported steep sites from `step_check.py`, grouped into stretches
   - islands: 3DEP or imagery showing land where the surface has none, and the reverse
   - troughs and ridges the detector proposed
   - then a free look at the contours over imagery, at the tide levels
4. **Edit.** Move, add, delete or accept contours; decide casts; judge the lines. Each stretch can be marked `reviewed` so
   the walk can stop and resume.
5. **Preview.** Rerun the shore model over the edited window from cached inputs (section 8), see the new surface and
   contours, and repeat steps 4 and 5 as needed.
6. **Full run.** `run_box.sh` for the block, with the edit check as part of the gate.
7. **Edit check.** For every edit: does the surface now do what it says?
   - a line: surface within a tolerance of its level along its length (start at 0.25 m)
   - a closed add: the ring exists in the output contours, with the right side up
   - a remove: nothing stands above the base inside it unless observed
   - a moved cast: read at its new position, the surface there meets its depth, and the review file shows the translation
   - Failures name the edit and the rule that beat it.
8. **Sign off.** Hig approves the block; the block's status table records the tag, the date and the edit count, and the
   edits are committed. Neighbouring blocks see any edit falling in their 2 km overlap and rerun if so, so the feathered
   seam stays consistent.

## 7. The review layers

Prepared per block by `shore_review_prep.py <box> <tag>` (new), from the run's outputs.

| Layer | From | Status |
|---|---|---|
| Surface, hillshade, slope | the run | exists |
| Contours by level, with stable ids | `shore_contours.geojson` | exists, ids new |
| Casts with what the model did to them | `cast_review.geojson` | exists; needs each sounding's `id` |
| Lines the casts trace | `cast_lines.geojson` | exists from the seventh test |
| Unsupported steep sites | `step_check.py --csv` | exists, as CSV |
| 3DEP and ArcticDEM alone, as separate surfaces | sources | new (the island check needs them) |
| Imagery with its date and tide stage | basemaps; tide stage new | partly |

**Round trip.** The contour file written by the model is stable: each line has an id built from the block, the level and
its position. `shore_edit_diff.py` (new) compares the reviewer's copy with the baseline. Changed stretches become held
lines, deleted lines become `remove` proposals for the reviewer to confirm, new lines become `add`, and unchanged lines
produce nothing. Two model details have to change for this: lines shorter than 8 vertices are dropped today, so small closed
contours never appear; and the beads along a flat at the base level (Taylor's delta edge) are noise to suppress.

## 8. Tools

**Editor: QGIS** (Hig, 2026-11-03). An in-browser custom tool may come later, but that is a way out. So the prep step
writes a QGIS project per review stretch with the layers of section 7 styled and the edit layers ready, and
`shore_edit_diff.py` turns the edited copies into the edit files.

**Preview.** Rerun the shore model over the edited window from cached inputs (section 8), see the new surface and
   contours, and repeat steps 4 and 5 as needed.
6. **Full run.** `run_box.sh` for the block, with the edit check as part of the gate.
7. **Edit check.** For every edit: does the surface now do what it says?
   - a line: surface within a tolerance of its level along its length (start at 0.25 m)
   - a closed add: the ring exists in the output contours, with the right side up
   - a remove: nothing stands above the base inside it unless observed
   - a moved cast: read at its new position, the surface there meets its depth, and the review file shows the translation
   - Failures name the edit and the rule that beat it.
8. **Sign off.** Hig approves the block; the block's status table records the tag, the date and the edit count, and the
   edits are committed. Neighbouring blocks see any edit falling in their 2 km overlap and rerun if so, so the feathered
   seam stays consistent.

## 7. The review layers

Prepared per block by `shore_review_prep.py <box> <tag>` (new), from the run's outputs.

| Layer | From | Status |
|---|---|---|
| Surface, hillshade, slope | the run | exists |
| Contours by level, with stable ids | `shore_contours.geojson` | exists, ids new |
| Casts with what the model did to them | `cast_review.geojson` | exists; needs each sounding's `id` |
| Lines the casts trace | `cast_lines.geojson` | exists from the seventh test |
| Unsupported steep sites | `step_check.py --csv` | exists, as CSV |
| 3DEP and ArcticDEM alone, as separate surfaces | sources | new (the island check needs them) |
| Imagery with its date and tide stage | basemaps; tide stage new | partly |

**Round trip.** The contour file written by the model is stable: each line has an id built from the block, the level and
its position. `shore_edit_diff.py` (new) compares the reviewer's copy with the baseline. Changed stretches become held
lines, deleted lines become `remove` proposals for the reviewer to confirm, new lines become `add`, and unchanged lines
produce nothing. Two model details have to change for this: lines shorter than 8 vertices are dropped today, so small closed
contours never appear; and the beads along a flat at the base level (Taylor's delta edge) are noise to suppress.

## 8. Tools

**Editor.** Three candidates:

| | Good | Bad |
|---|---|---|
| QGIS | Works today, nothing to build, Hig knows it | Hand-managed files, no worklist, no preview, level typed per line |
| `topobathy/review_tool` (local Flask) | **Exists** for water flags: worklist, keys, dispositions, writes the files the chain reads. Same imagery and pyramids. Never on the droplet | Needs line editing with levels, and closed contour handling |
| `/lidar/` traces | Polished line editing, session and undo | Writes to PostGIS, is the other stream's file, and would put a working tool into a public page |

**Recommendation:** QGIS for the first edit test, because it answers whether the model's interpretation works before
anything is built on it. Then extend `review_tool`, borrowing `scarps.js`'s session and handle mechanics.

**Preview.** A full box chain takes 5-15 minutes per test crop. The shore model only needs its inputs, which it can already
dump (`SHORE_DUMP`). Caching that per block gives a rerun of just the shore model, without the build. That is what makes
a move-a-line, look, move-it-again loop usable, and it is the single most valuable piece after the file semantics.

## 9. Hig's decisions (2026-11-03)

1. **Editor:** QGIS. A browser tool may follow eventually.
2. **Review unit:** the worklist presents coastal stretches; edits are filed by location, so they apply to the blocks either way.
3. **Casts are moved, not rejected,** with the translation recorded (section 3).
4. **Reviewer:** only Hig for now, so `who` is a formality and everything stays local.

## 9a. Confidence levels for automatic rejections (Hig, 2026-11-03)

The next phase is flow modelling and review-based correction, so an automatic rule should mostly propose, not decide. Each rejection of a pit or a spike carries a confidence level:

| Level | What the run does | Bar |
|---|---|---|
| High | Rejected, and off the worklist | The rule's own class, scored against lidar: at least 90 % truly false (Hig's bar) |
| Medium | Rejected in the surface, and stays flagged for review | Better than even, scored the same way |
| Low | Left in the surface, and flagged for review | Anything else the tests pick out |

First scoring of closed pits in the ArcticDEM + 3DEP stack against lidar (2026-11-03, pits at least 5 m deep; 38 KBay lidar tiles of 16 km2 not used to pick the rule, 637 pits, 20 false). No rule clears the high bar yet. "3DEP median slope under 5 degrees, 3DEP at least 5 m above the bottom, no pit in 3DEP" picked 16 of 16 false on the tuning windows but 5 of 7 on the clean tiles, so it is a medium rule. "Spread (ArcticDEM mad) at least 1.5x its surroundings on under 10 degrees, or at least 4x" went from 13 of 15 to 1 of 8, so it is low. Tightening the first to a depth of at least 6 m gave 3 of 3 on the clean tiles: promising, too few to trust. Filling every pit raises the error inside them (2.9 to 4.4 m mean), because most bottoms are real and it is 3DEP's rim that is false.

Only the highest confidence goes unflagged. Every level is listed in the run's report with the rule that set it, and every rejection can be undone by the reviewer. Where no rule clears the high bar, nothing is rejected silently. The spike filter on casts is held to this too: casts are moved rather than rejected, so a dropped cast stays flagged whatever its level.

## 10. Order of work

1. **First edit test (Hig), in QGIS, on the current path.** Open lines only, one crop. It exposes how the existing
   interpretation behaves, and may change everything below.
2. **Interpretation:** room and precedence for high lines, lines below the base, closed `add` and `remove`, `accept`, the edit
   check. Tested on crops with Hig's edits as the fixture, the edit check as a gate row.
3. **Cast moves and line decisions** read by the model; soundings' ids and original positions in the review file.
4. **Round trip:** stable ids, `shore_edit_diff.py`, small closed contours, suppress beads.
5. **Preview** from cached inputs.
6. **Whole boxes.** Switch the template to the contour model; a baseline run of the bay. Box seams get their first real
   test here, and it should precede real fine-tuning, because edits made before seams are understood may need redoing.
   Disk first: Nunatak has 47 GB free and Powder 79 GB, and run_box.sh refuses below 15 GB.
7. **Review project prep** for QGIS, then Hig reviews outer-coast stretches, Taylor first and then the queue.
8. **Docs:** `lidar_methods.html` for the model, edits and their provenance.
