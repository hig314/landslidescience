> **VERSION 1 TO PUBLISH; VERSION 2 FIX LIST (Hig 2026-11-23 evening).** Version 1 = run_1118 as published to dev at
> 20:14: `datasets.json` entry `kbay_topobathy_v1` on this branch (archive_mode copy, src
> `/Volumes/Powder/lidar_build/kbay_joint_s3/kbay_topobathy_v1.tif`, a COG of the final crops' mosaic), `dev_only` until
> Hig rules on the gated Grewingk Lake multibeam (sonar2023) in a public derived product; hosting builds and publishes.
> Version 2 is to be developed, then run as a LOW-PRIORITY background task when time permits. Hig's list:
> 1. **The shoreline fix as discussed at Petrof** (the slight ridge along the lake shore, accepted for v1).
> 2. **Homer Harbor on the latest code** (r6c2, r6c3 predate harbor_1123).
> 3. **The ramp beneath the Grewingk face**: the basin should be solved as though there were no shore at the ice, and the
>    ice should drop vertically to it; at present the bed shoals rapidly toward the face.
> 4. **The solve more faithful to sparse soundings** (the most involved; needs Hig's review). His example: the deep reef
>    near 59.13641,-152.03722 (boxes r1c0/r1c1), "large discrepancies between soundings and the surface... maybe there is
>    something confused pulling the surface?"
>    FIRST LOOK (same evening): within 2.5 km the 1977-84 survey H09890 (983 casts, ~95 m apart) is met: surface minus
>    sounding p5/p50/p95 -3.6/0.1/5.5 m, none over 15 m. The large misses (to 63 m, both signs) are ALL the 1915-31
>    lead-line survey H05194 (86 casts, sigma 4): where one lies within 80 m of a modern cast the two differ by
>    -48..+55 m (p5..p95). The old survey is MISPLACED, not wrong: one translation of about +300 m E, +500 m N (50 m search
>    grid, to refine) takes the median |old - modern| from 11.1 m to 2.1 m -- an old-datum offset. So two things pull the
>    surface there: the modern casts (rightly) and the old ones in the wrong place. Candidate general rule: register each
>    pre-NAD27 sheet to the modern casts where they overlap on relief, record the translation (casts are moved, not
>    rejected), and re-place the whole sheet; then judge what misfit is left (H09890's own 71 casts over 5 m off on the
>    reef's steep sides are the true "faithfulness" question).
> Also for v2, from earlier notes: skip a solve window with no product (the "tile FAILED" rows); Picnic Harbor hole;
> survey-edge artifact 59.38863,-152.29149; subtidal anomaly 59.65321,-151.67248; Bradley Lake depth.

> **RUN run_1118 FINISHED 2026-11-23 17:21; SPEED PATCHES APPLIED 20:20.** All 48 boxes chain cm22 + solve s3, published
> to dev `kbay_joint_s3`; then r4c5 re-solved (snd_edge_1122) and r4c1/r4c2/r5c2 re-solved on the harbour rule, final
> publish 20:14 (the box's Seldovia window matches the window test of the rule, median difference 0).
> - **The three fill patches are in** (shoreface per-level union, per-lake loop windowed, spline lattice): A/B on scratch
>   copies of r2c3 and r7c1, fill 2546 -> 1449 s and 5074 -> 2869 s (both -43%), every raster identical except the filled
>   surface (max 0.03 mm). Test tree and outputs: `$W/review_1121/perf/` (`ab/`, `ab_test.sh`).
> - **Any script that runs solve_box must carry the runner's thread caps** (`OMP/OPENBLAS/..._NUM_THREADS=2`, and
>   `PYTHONPATH=$W/guard`): the after-run scripts lacked them and tiles ran 5-7x slower (4 workers x 10 BLAS threads in
>   the multigrid's SVD); r4c5 took 65 min for 19. Same results, only time.
> - **"N solve tile(s) FAILED" in r1c4, r1c5, r0c3-r0c5 is old and harmless but should be fixed properly**: tiles wholly
>   outside the product crash on a percentile of nothing in the solve's comparison log line (solve.py, `dz = (z - cur)...`),
>   and the driver keeps the (void) product. Do NOT just guard the log line: the solve then returns a surface over the
>   void (kind 5) and the driver writes it. The fix is for the solve (or the driver) to skip a window with no product.

> **RUN STRATEGY AND PERFORMANCE (2026-11-23; Hig asked for these to be kept as notes).** Timing record: `$W/run_timing.py`
> -> `run_timing.csv`, `run_timing_stages.csv`; per-step times inside the fill are in each box's `timing_fill.log`.
> At 38 boxes: chain 38 h, solve 9 h; fill 20 h of the chain.
> - **CORRECTION: r3c3's 422 min "edited lines" step was the Mac ASLEEP** (2026-11-19 06:36 on, `pmset -g log`; that chain was
>   not even this week's -- the log spans earlier passes), not the code. `run_timing.py` now leaves it out.
> - **Strategy 1 -- skip the chain when only solve rules changed.** Most rule changes of 2026-11-21..23 were the solve's
>   (snd_edge, snd_far_local, harbor), yet a full rerun redoes every chain (~80% of the compute). `rules_ledger.json` already
>   names each rule's `stage`: a rerun should compare, per box, the live rules of each stage against the ones the box's
>   products were made under (`rules_applied.py`) and redo only from the first stage that differs -- solve-only where the
>   chain's rules are unchanged (as the Petrof and Seldovia re-solves did by hand). Not built; a design job for the next run.
> - **Strategy 2 -- two boxes at once.** The chain is mostly single-threaded numpy; the solve is threaded. Outer boxes
>   (rows 0-2) are light (chain 20-40 min, little memory). Rule so far is ONE window solve at a time and no crops beside
>   a fill (r6c3 took 8.5 h under swap, 2026-11-11). Candidate: a second chain beside the first for outer boxes only,
>   under memguard, never two solves. Hig 2026-11-23: revisit the evening the run is still going, to use the machine fully.
> - **Publish (done):** `composites/kbay_joint/publish_inc.py`, called by `$W/publish_s3.sh` since 11:38 (the whole rebuild
>   is `publish_s3_full.sh`; `FULL=1` forces a clean start). Only crops a changed box touches are re-blended, only their
>   tiles recut (from a VRT of all crops, extents snapped out to whole tiles), slope kept per crop. First use 9.9 min
>   including the one-time slope/overview bootstrap (was ~22); recut tiles agree with the mosaic to 0.05 m at z16 and
>   untouched tiles are untouched. STILL rewrites both PMTiles (3.5 GB) on the boot disk each time -- the snapshot churn
>   remains; `pub_watch.sh` (restarted) skips a publish under 25 GB free. Off-boot-disk PMTiles needs a compose mount
>   in the main checkout (the other stream's file): ask Hig.
> - **Shoreface, edited lines** (`topobathy/shoreface.py:415`): one whole-box distance transform per distinct line level,
>   3-6 s each (r3c3 99 levels, r3c4/r6c4 80); ~0.9 h a run. `review_1121/perf/shoreface.patch` (UNAPPLIED): per-level
>   discs on padded tiles; on r3c3's real lines old 515 s, new 3.4 s, 0 of 1.7 M cells differ. Verify in the pipeline by
>   one box's fill before/after, `fill.tif` byte for byte.
> - **"Tidal lens" time is the per-lake loop before it** (`topobathy/fill.py:1967` on): ~25 whole-grid passes per lake
>   (r7c1 1,695 lakes 41 min; r7c4 588 lakes 21 min; time vs lakes x cells r = 0.90 over 30 boxes; 3.1 h a run).
>   `review_1121/perf/lens.patch` (UNAPPLIED, parses, NEVER RUN): reject tests on each domain's own window. Verify on
>   r7c1: `fill.tif`, `fill_lakes.json`, `fill_water_level.tif` identical.
> - **Soundings splines (9.8 h over 36 boxes, 212 calls; the largest steady cost)** (`topobathy/fill.py:711-829`
>   `_spline_residual`, called at 2810 and per ceiling pass at 2879): a dense thin-plate smoothing spline, <= ~5,000
>   sites, 41 dense solves for the smoothing parameter, then the field on a lattice of up to 4 M nodes in ONE thread --
>   and the lattice is the cost (benchmark: solves 28 s, lattice 435 s). `review_1121/perf/spline.patch` (UNAPPLIED):
>   lattice distances by one BLAS product in a thread pool (`points_threads`, default 4), one eigen-decomposition for
>   the bisection. Same mathematics. Benchmark 463 s -> 61 s; the real function old vs patched on a synthetic lake
>   51 s -> 12 s, max difference 2.4e-7 m. Estimate for a run 9.8 h -> ~2 h (extrapolated). Verify on r2c3 or r3c5:
>   `fill.tif`, `fill_sigma.tif` within 1e-4 m, same spline log lines. Not done: warm restart of the ceiling passes
>   (3.1 h), the point reading before the spline (1.3 h). See `spline_README.txt`.
> Apply the patches only between runs (each box's chain starts fresh processes: an edit to fill.py goes live mid-queue).

> **Harbour rule LIVE 2026-11-23 10:36:05 (Hig: "The rule looks good - commit and deploy, and go ahead and put rerun of Seldovia at the
> end of the queue").** `harbor=1` by default. `$W/after_run_harbor.sh` waits for the runner and `after_run_r4c5.sh`, re-solves
> r4c1 r4c2 r5c2, publishes. Boxes solved before the live time (Homer's among them) do not carry it.

> **Harbour as a terrain (Hig 2026-11-23: "a single tree of rules to work for both cases ... explicitly identifying a
> 'Harbor' as a type of terrain"). DEVELOPED, `harbor=0` by default, waiting on Hig; ledger `harbor_1123`.** The
> speck rule `obj_hull` is withdrawn (it caught a third of the cells). The rest were the same thing in bigger clumps and
> further from a typed cell: multibeam / green-lidar returns off hulls and float undersides classed BED, some ABOVE
> the water level. Rule in solve.py: HARBOUR = tidal water within 30 m of typed structures/objects totalling >= 400 m2
> (typing raster = 5); there, a measured cell standing 1.0 m over the grey opening of the measured surface by a 12 m
> disc (the floor a boat-sized probe can rest on; banks, breakwater toes and planes survive it) is no measurement.
> The tree, same for Homer and Seldovia: (1) joint `structure`/objects: returns on the works are water; (2) solve: a
> typed object is no ground, anywhere; (3) solve, harbour only: what stands over the probe floor is the works.
> Windows (`review_1121/solve/`): Seldovia `seld0` -> `seld2`: 542 cells freed, all mounds gone, the only rises are
> the pits beside the old mounds coming back to the floor (-7 -> -6); Homer `hom0` -> `hom1` (r6c3, 588704 6607817
> 589704 6608817): 16.9 ha harbour, 1203 cells freed, one leftover mound gone (-4 m), nothing raised, rest unchanged.
> Pictures `review_1121/seldovia_harbor.png`, `homer_harbor.png` (script `harbor_png.py A B out`).
> NOT tested: other piers/docks (Halibut Cove, Jakolof, the deep-water dock), a rocky anchorage with skerries narrower
> than 24 m inside 30 m of floats (they would be planed).  To make live: default harbor=1, re-solve r4c1 r4c2 r5c2
> (Seldovia) and r6c2 r6c3 (Homer) at least; every box with typed structures differs.

> **Seldovia floats, traced (2026-11-23).** The chain is clean there (fill under the floats p50 -4.9 m); the mounds are the
> SOLVE's. The floats are typed structure/object and are water, but ~180 cells in ~50 specks (largest 21 cells) of
> multibeam (`bathy2009`) and green-lidar (`seldovia2019bed`) returns off the hulls are classed BED at -1.9..+0.4 m on a
> -5 m floor, held as confident measurements; the solve drapes a mound on each. New rule `obj_hull` in solve.py (a speck
> of bed touching a typed object, within 2.5 m of the level, 1.5 m over other bed within 15 m, <= 200 m2, is no bed):
> on the window (`review_1121/solve/seld0` before, `seld1` after, picture `seldovia_floats.png`) it frees 182 cells and
> removes three of the big mounds, but most remain and a few cells rise -- so there is a second cause, suspect the
> compilation prior (ptype 2: ocm2023/ncei2010 carry the floats; its level correction ran p50 +1.25, p95 +4.3 m here)
> on the freed cells.  **`obj_hull` defaults to 0 (unfinished)**; the running queue is unaffected.  Window command:
> `python topobathy/solve.py kj_r4c1 NAME 572560 6589108 573160 6589708 --tag cm22 --out $W/review_1121/solve --nofig --set snd_mode=1 obj_hull=1`.
> Disk: Hig thinned the snapshots 10:20 (69 GB free); pub_watch left off (13 more publishes would churn ~45 GB).

> **2026-11-23, Hig: floats in Seldovia harbor (59.43693,-151.71542) stand in the s3 surface; "we should do some of the
> same cleanup as we did in Homer"** (the 2026-11-12 Homer rules `structure` / `superseded` / `bedprotect` are live but
> do not clear it). The harbor sits in the r4c1/r4c2 overlap (r5c2 only clips it). First look, `$W/review_1121/seld_probe.py
> BOX` on a 300 m window: ~3,000 cells stand >1 m over a 60 m median of low ground, in ~60-100 patches (largest
> 0.44 ha), z -3.7..+5.5; by `class_now` they are bed (3) ~45%, ground (1) ~35%, water (2) ~20%, so the floats come in
> mostly as green-lidar "bed" and lidar "ground", not as unclassed returns -- which is why `structure` passes them.
> NOT yet traced to a layer or rule. Then rerun r4c1 + r4c2 (+ r5c2).
> **Boot disk, same day:** every publish rewrites ~3.5 GB in `data/lidar_dev/pmtiles` and the hourly Time Machine
> local snapshots keep each old copy for 24 h (disk fell 57 -> 12 GB free over a day of per-box publishes).
> `pub_watch.sh` stopped by hand at 10:10; the runner's PUB marks still publish. Old dev layers kbay_joint
> cm16/cm18/s1/s2 and `data/iceboost/{raw,tmp}` deleted at Hig's word (catalogue backup `catalog.geojson.bak_20261123_pre_prune`).

> **Hig's review, 2026-11-22 evening:** Homer Spit/Harbor (r6c3) looks good; Woz delta (r5c2/r5c3) looks good.
> Run at 24 of 48 boxes. `perched_over_deep_ha` is up against the best run in r6c3, r5c2, r5c3, r5c4 (untraced).

> **Runtime record (Hig 2026-11-22: the run is time-consuming; keep the data for a later optimisation job).**
> `$W/run_timing.py` reads `run_1118.log` and each box's `chain.log` and writes `$W/run_timing.csv` (chain and
> solve minutes per box) and `$W/run_timing_stages.csv` (minutes per chain stage). At 19 boxes: chain 31 h,
> solve 6 h; the chain's `fill` stage alone is 19 h (about 60% of chain time), next `high line`, `water`,
> `shore` at 1.4-1.9 h each. So the fill stage is the place to look first. Rerun the script when the run ends.
> Hig also asked to review the islands between 59.50689,-151.51829 and 59.55031,-151.46739 (box r5c2, fallback
> topo there) as soon as it publishes.

# Topobathy compositing: tools and interface

**Petrof shore rule revised + r4c5 queued (2026-11-22 ~09:50; Hig: "queue a rerun of that box after the others").** Issue 1
below is fixed in solve.py (ledger `snd_edge_1122`; test `pl_edge2`, `review_1121/petrof_delta.png`, `delta_cmp.py`).
`$W/after_run_r4c5.sh` (running, under caffeinate) waits for run_1118 to end, re-solves kj_r4c5 and republishes. Left: a
0.4 m hump at the shoreline itself (15.2 then 14.8 m inland) in the test profiles. Hig 2026-11-22: "there's still a
slight ridge along the lake, but it's definitely good enough at this point" -- recorded, not to be chased now.

**HIG'S MORNING REVIEW, 2026-11-22 (14 boxes published): "looking good enough I think we should finish the full run and make
this a published product, assuming nothing worse shows up (waiting on Homer Harbor and Woz delta for instance)".** So: let
run_1118 finish all boxes (pub_watch republishes after each); then the publishing step (a `datasets.json` entry on this
branch, hosting builds/publishes -- WORKSTREAMS.md; ask Hig before anything leaves dev). ISSUES TO RECORD AND LOOK AT IN A
FUTURE RUN (not fixed now):
1. Petrof shore feather (`snd_edge`) "didn't work right - there is a ridge at the shoreline, and then the level descends
   inland before jumping up sharply rather than gradually rising to blend into the topography", worst on the prograded
   delta 59.40068,-150.82033. (Mine: the lift is zfix + (level - z) x t(d): where the ground is the 3DEP lake flat (8.42)
   out to > 40 m from the mapped shore, the raised surface FALLS with t back to 8.42 at 40 m, then meets real ground in a
   step. The rule must not fade toward ground that is itself below the level: flood-fill the below-level ground connected
   to the lake and treat ALL of it (raise to the level and grade up to the first ground above it), not a fixed 40 m.)
2. A strange deep hole in Picnic Harbor, 59.26840,-151.43624.
3. Survey-edge artifact at 59.38863,-152.29149.
4. Grewingk "has developed something of a ramp up to the bottom of the ice-face" (new in the r6c4 redo: suspects snd_far_local /
   snd_far / snd_edge at the ice; r6c4's gate also flags solid_over_level_water 1019, face_bad_cells 115, perched_over_deep 0.9 ha).
5. Small anomaly in the shallow subtidal, 59.65321,-151.67248.
6. Bradley Lake "mapped as extraordinarily deep - we need to return to interpreting the old surveys here, but regardless of
   that I'm confused as to why it would be suggested it's such a deep basin". NOT DIAGNOSED: r7c5's solve leaves it "as
   the product has it" (a lake with no sounding), so the depth is the CHAIN's (fill stage / the pre-dam lakebed points in
   obs/bradley), e.g. 194 m at 59.752,-150.83 against a 348.6 m level; no row of soundings_edited.csv lies in
   59.70-59.82, -150.95..-150.65. Start at how obs/bradley enters the r7c5 recipe and its datum (1955 contours in feet?).

**OVERNIGHT RUN, 2026-11-21 21:52 -> (Hig: "let this run through the night ... review whatever has been produced" in the
morning, unrun areas as holes).** `GO_AFTER_FIRST_PUBLISH` is set: `run_1118.sh` goes on past the hold into the other 39
boxes (none has a cm22 chain: each is chain + solve on the current code, ~3 h a box). `$W/pub_watch.sh` (new, under
caffeinate) republishes `kbay_joint_s3` -- latest-code boxes only -- after EVERY box solve, so the dev layer is current
whenever Hig looks. kj_r6c2 was re-solved by hand at 21:52 (snd_edge + snd_far_local; its old solve.json is
`solve.json.pre_edge_1121`). Both branches pushed 2026-11-21 ~21:45 at Hig's word (topobathy 2b839ed, main 81dc811); prod
not deployed. Morning: check the log for failures, that pub_watch's last publish follows the last solve, then look at
Petrof (r4c5: 14.5 m, shore rule, local profile), Grewingk's shore under snd_edge (r6c4), Homer pimples (r6c2).

**`snd_far_local`: ON by default since 2026-11-21 20:55 (Hig: "put this update into future boxes"; ledger `snd_far_local_1121`).** The lone-cast fix: far from casts the bed
goes to the lake profile SCALED by the casts of its own area (cast depth / profile depth, carried by inverse distance
softened at the casts' spacing, never over 1). Window test `review_1121/solve/pl_local` vs `pl_edge`, picture
`review_1121/petrof_local.png` (`petrof_cmp.py`): Petrof's southern basin becomes a broad 15-25 m floor about the 19 m and
13 m casts instead of two mounds in a 35 m basin; lake mean depth 28.1 -> 24.0 m, deepest unchanged (49.0), every cast
still met, the north untouched; 226 s (412 s before). Not tested on Grewingk / Halibut Cove Lagoon / Beluga. Distance is
straight-line (through a peninsula). To turn on: default `snd_far_local=1` in solve.py + a ledger row, then re-solve the
sounded boxes.

**Recorded, not fixed (Hig 2026-11-21, on `review_1121/petrof_14p5.png`):** "seems ok except the sounding at 59.37869,
-150.81877 is postulated to be on an isolated ridge, rather than to be representative of that area of the lake. Not a
huge issue, but worth noting." That is cast id 37222 (19 m deep): `snd_far` pulls the bed back to the lake's depth
profile ~50 m beyond a lone cast, so a lone SHALLOW cast in a wide unsounded area stands as a mound (the 13 m cast south of
it likewise). The fix belongs with the wavy-cast-surface note: far from other casts a cast should move the local profile
(its area's depth), not just a cone about itself -- e.g. the departure from the profile decaying over the distance to the
NEXT cast or the shore rather than a fixed 50 m.

**Petrof Lake at 14.50 m + the shore rule (2026-11-21, ~20:00).** Hig, after the viewer's ArcticDEM datum was fixed (main,
b91077e): "might be as high as 15 m, maybe 14.5 ... handle the edge with feathering that makes sure not to go below
that lake level". Casts of `pg_2022_a` set to 14.50 - depth in `soundings_edited.csv` and `soundings_20261121.csv`
(14.5 is MY pick of his two numbers). New solve rule `snd_edge` (ledger `snd_edge_1121`): fixed ground within 8 m of
sounded still water is raised to its level, feathered out to 40 m. Window test `review_1121/solve/pl_edge` + `edge_chk.py`.
It applies to every sounded lake (Grewingk's lidar shore will rise up to ~1 m at the water: CHECK in r6c4). kj_r6c2's solve
started 19:39 on the code before this: after the HOLD, set aside its solve.json, relaunch the runner (re-solve + republish).

**Petrof ArcticDEM datum, SETTLED 2026-11-21 evening (supersedes the "+1 m shift" question below).** The 1-2 m Hig
saw in the viewer profile is the profile's datum: `LSDemRead.ARCTICDEM` reads PGC's `Height Orthometric` (EGM2008),
and on the outwash plain that is 1.06 m BELOW our staged NAVD88 mosaic (p10-p90 -1.14..-0.98, 21 ha; same at the
shore cells), while Mapterhorn = our 3DEP (+0.01). So the +1 m Hig asked for is already in the staged mosaic and the
chain's ground product: NO shift applied. ArcticDEM's own water surface mid-lake reads 12.78 EGM2008 = 13.84 m NAVD88,
which backs the 14 m level. Left over: ~0.6 ha of staged ground at 12.2-12.6 m within 4 m of the mapped shore, nearly
all at the lake's SOUTH end (59.37616,-150.83073; 59.37695,-150.83497; 59.37664,-150.82747) and one spot at
59.38155,-150.80775 -- below a 14 m lake; not explained (another strip at a lower stage?). A 4 m bilinear resample
hides these cells (it drops cells beside voids): read shore ground at the native 2 m. Scripts: `$W/review_1121/
datum_cmp.py, datum_cmp2.py, shore2m.py, shore_low.py, s_end.py`. Viewer follow-up for the main stream: the profile
could add the local EGM2008->NAVD88 offset to ArcticDEM, or say the datum in the legend.

## Resume here (2026-11-18, 12:45) — FULL RERUN run_1118: every box, chain cm22 -> solve s3, on one code

**STATE 2026-11-21 17:33: REDO of four boxes RUNNING on commit 7ded876 (Hig's review of the first publish; ledger `review_1121`):
`run_1118.sh` under caffeinate redoes chain + solve of r6c2 r6c4 r3c5 r4c5 (markers: a line appended to chain.log, `out_s3/solve.json`
set aside as `.pre_1121`), skips r7c4 r3c3 r4c0 r0c2 r7c5 (unchanged by construction), republishes `kbay_joint_s3` (latest-code
boxes only: `publish_s3.sh` reads `$W/LATEST_CODE_T1`; the five unchanged boxes pass on their 2026-11-20 solves) and HOLDS.**
Expect ~5 h.  The other 39 boxes wait for Hig: `touch $W/GO_AFTER_FIRST_PUBLISH` and relaunch.  If a rule changes again, rewrite
`$W/LATEST_CODE_T1` and revisit the SAME set in publish_s3.sh.  Hig is to check at the republish: Petrof Lake (59.38363,-150.82546;
59.38981,-150.81310: -34.3 m both in the box test, deepest cast -40.6), its embayment (59.39672,-150.81461: 8.6 m, the lake's level),
Division Island (59.42142,-150.69406: 27.5 m; its chain is redone with the island cut out of the high-tide water), the pimples
(59.69257,-151.57322).  Not seen at box fidelity before this run: the joint rule (fuse test only), Grewingk's lake and Halibut Cove
Lagoon under snd_far / snd_cap (r6c4), the islands in the CHAIN.  Open question for Hig: Petrof Lake's level, 8.42 or 12.04 m
(see the diagnosis below).  Trap met again: `until ! pgrep -f "solve_box.py X"` in a wait loop matches its own shell -- never ends.
**Petrof Lake's level (Hig 2026-11-21 evening): "~14 m"; "the shoreline prograded between 3DEP and the image I used to trace";
"ArcticDEM ... vertically shifted downward by 1 m ... use the ArcticDEM surface shifted up 1 m to match 3DEP in the outwash plain
(which is not vegetated)".**  DONE: the 26 casts of level_key `pg_2022_a` stand at 14.00 m (z = 14.00 - depth, +5.58) in
`obs/soundings/soundings_20261121.csv` (= 20261017 + this) and in `soundings_edited.csv` (pre-edit copy
`soundings_edited.before_petrof14_20261121.csv`; cast_edits.py LAKE points at the new base; `apply` was NOT run -- it syncs the dev
database).  r4c5's chain had not started, so the redo takes it.  NOT DONE, owed to the main stream: the level row in the dev and
prod databases and `KNOWN_LEVELS` in tools/soundings/ingest_higman_glacial_lakes.py still say 8.42.
NOT DONE, ASKED HIG: the +1 m ArcticDEM shift.  In OUR staged mosaic (`arcticdem_v41_navd88_2m.tif`) ArcticDEM - 3DEP on the
plain (3DEP 14-24 m, slope < 4 %, 16 ha) is -0.2 to -0.3 m (median), not -1; and its ground at the mapped shore (0-4 m outside
Hig's polygon, 2,649 kept cells) stands p5 12.1 / p10 12.3 / p25 12.7 / p50 14.3 m -- so about a quarter of the measured shore
ground lies UNDER a 14 m lake (the water stage's 12.04 is that lower envelope).  `$W/review_1121/outwash.py`, `adem_off.py`.

*(Past, LIFTED: the queue was relaunched 2026-11-21 17:33 for Hig's review_1121 fixes.)* HOLD (Hig, 2026-11-20
~22:00, power-cord trouble): the queue is stopped by the main session once kj_r7c4's
solve finishes. Do NOT relaunch run_1118.sh or start any other analysis until Hig says so. The last line of
`$W/run_1118.log` records where it stopped. The stop is done by the detached `$W/hold_after_r7c4.sh`
(waits for the first log line naming kj_r3c5, then kills the queue and that box's solve; kj_r3c5 has no cm22
solve.json, so it is redone on relaunch).

**RUNNING since 2026-11-20 16:53 on commit 0568d6f (47c95d6 + `line_yield_water`; ledger row `solve_1120`): `run_1118.sh`
under caffeinate.**  (2026-11-20 ~20:51-21:25 the Mac HIBERNATED on a flat battery (power cord) in kj_r7c4's solve; uptime
unbroken, every process resumed on wake, but kj_r7c4's solve_box then died silently ~21:46 (no output, no traceback); the main
session had paused the queue at 21:40; relaunched 21:47 on AC, r7c4's solve redone from the start (rc 0 at 22:37: 94 of 100 tiles, 21.9 % replaced).  **HELD by Hig
at 22:37 after r7c4 (main session): no further boxes until Hig says so**; r3c5's solve stopped at its start, redone on relaunch.
**RELEASED by Hig 2026-11-21 10:48** as a possible FINAL run, gated on the first publish: queue r3c5 r4c5 r3c3 r4c0 + **r0c2**
(open ocean, casts only, 7,950 casts) + **r7c5** (alpine: 3DEP 210 km2, ArcticDEM + arcticdem_alpine) -> publish -> the runner
HOLDS (`touch $W/GO_AFTER_FIRST_PUBLISH` and relaunch runs the other 39).  `publish_s3.sh` now mosaics ONLY boxes whose s3 solve
names cm22 and postdates 0568d6f (list in `$MOS/boxes_latest.txt`); every other box is a hole (Hig: "only reviewing what is truly
the latest code").  Pre-edit copies `$W/publish_s3_before_1121.sh`, `$W/run_1118_before_1121.sh`.  If a rule changes after the
review, move the date in publish_s3.sh's filter with it.
**FIRST PUBLISH 2026-11-21 14:58, runner HOLDING for Hig's review:** `kbay_joint_s3` in the dev viewer = r0c2 r3c3 r3c5 r4c0 r4c5
r6c2 r6c4 r7c4 r7c5 only (holes elsewhere), all solves rc 0, no failed tiles.  Checked in boxes: r6c4's perched bodies no longer lift
the bed (s3 = chain).  To look at: r7c4 12.8 ha +7.2 m vs s2 at 59.78798,-151.01201; gate rows r4c5 (2, as cm21), r4c0 water_km2
336 vs 1344 (x4 exactly, one older tag: untraced), r7c5 (2).
**Hig's review of the first publish (2026-11-21 ~15:30): "looking pretty good, including throughout the intertidal".**  To fix before the rerun:
- Petrof Lake (r4c5): deep basins assumed where there are no observations (59.38363,-150.82546; 59.38981,-150.81310) -- "most likely the
  observations are representative and these deeper basins don't exist".
- The little embayment at 59.39672,-150.81461 (r4c5): "still struggling".
- Division Island 59.42142,-150.69406 (r4c5): no shoreline, though 3DEP and ArcticDEM should have it -- "a substantial island".
- Pimply bumps in an E/W band through 59.69257,-151.57322 (r6c2), not visible in the 2008 or 2019 lidar.
Recorded, NOT to hold the full run (fix only if synergistic):
- A low but sharp seam running N/S through 59.40543,-152.10082 (r4c0).
- Bathymetry from casts: some mechanism (e.g. splines linking points along strongly curved features) to modestly raise fidelity to the
  observations and avoid wavy surfaces that wobble between successive evenly spaced casts.
**Diagnosis and fixes (2026-11-21 evening; ledger row `review_1121`; UNCOMMITTED; pre-edit copies `$W/*_before_1121.*`; tools and
figures in `$W/review_1121/`: view.py jpt.py wpt.py wfig.py islands_scan.py, window solves `solve/pl_*`, `div_fix`):**
- **Division Island = yesterday's `line_yield_water`.**  The chain had it at 27 m; the solve sank it to -28 m: it lies whole inside
  tide_high_water.geojson (no line traced; its core < 200 m, so tide_vectors' island rule missed it), and "unmeasured land inside the
  mapped high-tide water gives way".  Fourteen islands in all (80 ha, boxes r4c5 r3c5 r4c1 r3c1).  Fix at the source,
  `tide_vectors.py islands`: a 3DEP blob >= 10 m, >= 0.3 ha, DETACHED >= 50 m from the polygon's edge is an island, cut out down to
  5 m (tide_high_water / tide_sea / tide_islands.geojson rewritten: 7 kept + 14 new).  Window `div_fix`: 8.5 ha back, new - chain p50 0.
- **Petrof Lake basins** = the lake fit's DEPARTURE from its depth profile never decays: casts in 48 m of water 30 m off a steep shore
  stand 30 m under the profile, and that offset ran into the arms with no cast (31 + 30 = -61 m; deepest cast -40.6); plus the plate
  overshooting beyond the last cast of a line (-71).  Plus the tiles: the lake lies across two, one saw 21 casts, one 8 (-51.6 in the box
  against -40.9 in one window).  Fixes: `snd_far` (beyond the casts' spacing the bed is the profile; reach `snd_far_len_m` 50 -- 99 m
  left a third of the lake on the cap, 20 m made a cone of every cast), `snd_cap` (no deeper than the body's deepest datum, cast or
  measured bed), solve_box `--lake-max` (a sounded lake crossing tile cores is solved whole in one window).  Window `pl_fix4`.
- **The embayment** = an order-of-operations fault: the casts' level (8.42) was given to cells with a body id only; the inlet has
  none (8 m body raster) and kept the product's 12.04 m, its bed 2-4 m over the lake.  The level now goes to all connected water.
  **ASK HIG:** the product's 12.04 m is the water stage's (ArcticDEM ground on 40 % of the shore stands 3.6 m over 3DEP's flat);
  the casts' 8.42 is 3DEP's hydro-flat ("2022 level unknown").  The solve takes the casts' -- which is right?  3.6 m of depth hangs on it.
- **Pimples** = the 2019 Homer (DGGS) flight's north edge: its ground thins out over 500 m and what is left is canopy classed
  ground; by date it superseded the solid 2008 ground.  joint.py: supersede by date only where the newer lidar is solid
  (`supersede_solid_m` 25, `supersede_solid_frac` 0.6).  Fuse test `$W/review_1121/joint_pimples`: lumpy cells 11.2 -> 1.7 % in the
  band, voids 34 -> 6 %.
- **Seam at 59.40543,-152.10082 (not fixed):** the west edge of NCEI 2010's grid in the joint; east of it the product keeps the
  compilation, west the 16 m deep pass from casts lowered the bed ~3 m; the grid's edge cells are fixed.  Same family as the wavy
  cast surfaces: for a later session.
- **What must be redone:** chains + solves r4c5 r3c5 (islands), r6c2 r6c4 (joint).  r7c4 r3c3 r4c0 r0c2 r7c5 are unchanged by
  construction (no sounded still water -- checked with SND_PROF_ONLY on their deep passes --, no new island, no lidar superseded by a
  lidar): their cm22 / s3 stand.  Restarted twice at 20:22 / 20:27 for nice 10 + 2 math
threads per process (Docker starved).  Restarted once at 16:53 in r6c2's solve for Hig's second note on Petrof: "a hill on the beach below
the high-tide line - the surface should grade from the high tide line down to the lowtide line".  The hump was 3DEP
(fixed) and ArcticDEM (soft) standing 8-12 m INSIDE the mapped high-tide water; unmeasured land inside
tide_high_water.geojson now gives way wholly, traced islands excepted.  Window `pet_ct5`: every profile grades 3.65 ->
-1.7 m.  Hig on the lagoon window `hcl_p4`: "looks good".)  Every s3
solve made before it is redone (markers set aside as `out_s3/solve.json.ring_bug_1120` / `.ring_only_1120`); chains are
kept except kj_r4c5's (a line appended to its chain.log makes the runner redo it: restored high line).  Order: solves of
r6c2 r6c4 r7c4 r3c5, r4c5 chain + solve, r3c3 solve, r4c0, then the FIRST `kbay_joint_s3` publish (~12-15 h), then on.
**To check at that publish (not yet seen at full-box fidelity -- windows only):** Halibut Cove Lagoon 59.57183,-151.19578
(r6c4; window `hcl_p4`: solve - product p1/p99 -1.3/+1.2 m, figure `$W/review_1120/halibut_lagoon_window.png`) and Petrof
59.36015,-150.82710 (r4c5 AFTER its chain rerun: the chain's own high_line ramp now has the line too; window `pet_ct4`,
figure `$W/review_1120/petrof_before_after.png`; a 0.4 m scalloping of the 3.65 m contour along the line remained in the
window).  The per-box change report `$W/run_1118_diff.txt` is the regression check for the five global solve changes
below; none was tested outside these two windows.
**What went live at 16:44 (Hig's answers of 2026-11-20: retire the low bound; "think of the high tide line as
approximately a contour - it should drop to that level"):**
- solve.py: (1) a still body's SHORE = cells that are not water -- the `snd_shore` ring, the hinge band and the lake
  profile's distance (see the fault below); (2) `fix_grid_bed_edge`: a confident gridded bed under water is fixed to its
  edge (the lattice's 10 m stripes were "edge" everywhere: 9 % of the lagoon window fixed, 15 % soft; now 24 % / 0.4 %);
  (3) `comp_near_bed_m` 30: a compilation's pull fades beside a measured BED (not beside land: the Woz inlet rule
  stands); (4) `snd_cast_beside_m` 15: NOS / CRM casts within 15 m (or one coarse cell) of a measured bed are left to it
  (H09884: 131 of 324 stand > 8 m above the multibeam); (5) `low_bound_m` 0: the guard is retired (code left in);
  (6) `line_yield_m` 15, `line_yield_min_level_m` 2: unmeasured land (3DEP, dry fill, ArcticDEM; never a lidar's) within
  15 m of a traced high line gives way, its cells ON the line are held at the level, and the line is a hinge.
- solve_box.py: deep modelled cells within 2 coarse cells (32 m) of a layer's cell keep the product, not the 16 m pass.
- tide_vectors.py `rescue` (run it after build / merge / review; build does not call it): a dropped HIGH edge under
  700 m with the sea on one side and land on the other 100 m off (>= 80 % of it) is a shore.  12 edges, 2.16 km, flagged
  `rescued` in tide_lines.geojson and tide_vector_lines.geojson (pre-edit copies `$W/tide_*_before_1120.geojson`).
  Boxes holding one: r1c1 r1c2 r2c1 r2c4 r3c4 r4c5.  Hig's Petrof polygon has straight edges of 159-271 m; LONG_EDGE_M
  150 had dropped them as closing edges, so the beach had no high line and the cap's plate ran to 3DEP's edge.
- NOT done: point-cloud hosting of the reclassified sets (Hig: wants "our best reclassified version ... hosted online",
  deferred to its own session; only kenai_2008 r1 is on R2, KBay reclass + Kenai r2 are local only).
**The stop of 15:11 and the diagnosis:**
- **The fault (`snd_shore` ring, solve.py):** "the outermost ring of a still body's cells is data at its level" took the
  ring beside ANY cell outside the still/pond set -- other water included.  The lagoon is cut into ~47 bodies (sea, tidal,
  lake, perched), so every body line in open water was held at +1.5..2.5 m over a -60 m bed in the 16 m deep pass
  (window `hcl_deep`: fit - cast RMS 18.1 m, 415 ring cells); the box kept its deep modelled cells and the 2 m tiles then
  bent 21,000 measured multibeam cells (soft: swath edges) up to it.  Peaks topped near -15 m = scope_deep_m.  Fix: the
  ring (and the hinge band) is beside cells that are NOT WATER (shore_label 2/3).  Window `hcl_deepfx`: 2 ring cells,
  RMS 4.4 m.  In the repo's solve.py since 15:11 (uncommitted; pre-edit copy `$W/solve_before_1120.py`); needs a
  rules_ledger row once r6c4 confirms it.  Residual to look at in r6c4: the fixed deep pass still differs from the
  product by -9 / +11 m (p1 / p99) along some gap lines; NOS H09884's casts there disagree with the multibeam by tens of
  metres (131 of 324 stand > 8 m above it).  s1's grooves in the swath gaps are a separate, older matter.
- **Petrof terraces (Hig: "digital terraces, especially along the high-tide", 59.36015,-150.82710) = the solve's LOW
  BOUND.**  The only evidence within 30 m of those free cells is the fixed prior land at the cliff foot (3DEP, 8.99 m),
  so they were put at 8.49 m in 60 m blocks.  `low_bound_m=0` gives a clean ramp (window `pet_nolb`).  Narrower rule in
  `/Volumes/Nunatak/lidar_build/lidar_dev/topobathy/solve.py` ONLY (not the repo): fixed and soft PRIOR land is no
  evidence for the bound -- the terrace at Hig's point goes (8.7 -> 2.8 m ramp), 1,973 cells of blocks remain to the
  west (a 3.1 m shelf between the high and low lines where they run 20 m apart) plus two narrow tabs.  Dropping the
  lines from the evidence made it WORSE (the bound is a minimum: less evidence = higher bound).  Third time this guard
  makes blocks (Fox squares, Beluga shelf): proposed to Hig to retire it now that pit_post raises closed basins.
  Separate design point: 3DEP stands 8.7 m AT the traced high-tide line (level 3.65); should prior land within ~15 m
  of the line yield to the line's level?
- **Disk 2026-11-20 (Hig's go-ahead):** removed box archives cm0 cm1 cm2 cm5 cm16 cm17 fix2h v8, kbay_joint_cm1,
  kbay_v3_boxes; and, R2-verified by the audit agent, cog/dixon_2023.tif, kbay_reclass_test full/mosaic_v1 full/out_v1
  viewer_build/{pm_v2,kbay_2023_reclass,kbay_2023_reclass_v2}, anchorage_2015/tiles*.  Nunatak 68 -> 149 GB free.
  Not removed, need Hig: kenai_2008/dtm 18.5 (manifest src, COG on R2), superseded/ 16, kenai_taught feat cache 58,
  kbay_reclass_test/site 60 + model caches 9 (retraining caches, local only), Powder dev_viewer_retired 15.
- A killed solve_box leaves its spawned tile workers running (load average 220): `pkill -f "multiprocessing[.]spawn"`.

**Was RESTARTED 2026-11-20 14:15** (`run_1118.sh` under caffeinate, on AC; Nunatak `diskutil verifyVolume` clean, the
last outputs read whole).  It resumed at kj_r3c3's solve; then r4c0, then the first `kbay_joint_s3` publish (Docker
was not running at the restart: the dev viewer needs it).  **Disk:** Nunatak 69 GB free, ~2.7 GB a box so far, so the
run's own 20 GB stop will trip some 18 boxes on unless more is cleared (ask Hig).  Checks done at the restart:
- r3c5 island (59.26687,-150.88567): 0.9 m in s1 -> 30.8 m in cm22 / s3.  Fixed.
- Petrof (59.36015,-150.82741): s3 = s2 (the 3.7 m plate is in cm22); still the design question for Hig.
- r6c4 perched_over_deep 0.25 -> 0.90 ha = six "perched" bodies of 0.04-0.5 ha, joint's own water at 1.6-2.6 m over
  the -33..-61 m multibeam, at 59.5634,-151.1989 (four) and 59.5750,-151.1938 (two).  The chain's bed is unchanged
  there, but s3 stands up to 20 m higher within 40 m of them (max -37.9 against -58.5): NOT diagnosed; asked Hig what
  is at those spots.
- Gate rows clip_shelf (r4c5 8629, r3c3 6351, r3c5 1118, r6c4 2849) are near what cm21 / cm18 / cm5 had; land_km2
  "MOVED" in all four (r3c3 150.6 -> 207.7): cause not looked up.  r3c3 solid_over_level_water 88 (cm18 460).
- Still owed: Picnic Harbor and the west-edge line once r3c3 / r4c0 have solved.

**Was STOPPED 2026-11-19 ~17:00 (state as of the 19:45 shutdown).** The Nunatak drive dropped off the Mac (not
visible to diskutil; Powder stayed mounted) during kj_r3c3's solve; the runner and the solve died with it, then Hig
shut down for a power problem. Nothing is running. **Before restarting:** First Aid on Nunatak (unclean disconnect),
then check `$W/run_1118.log` and that the last outputs open (`$W/kj_r3c3/out_cm22/kj_r3c3.tif`, `$W/kj_r4c5/out_s3/`).
Restart: `cd $W && nohup caffeinate -i -s zsh run_1118.sh > /dev/null 2>&1 &` (it skips what is done; r3c3's
half-written `out_s3` has no solve.json so its solve reruns; then r4c0, then the first `kbay_joint_s3` publish).
- Done on cm22 + s3, no failed tiles: r6c2, r6c4, r7c4, r3c5, r4c5. r3c3: chain cm22 done (gate: 4 rows), solve not.
  **No s3 mosaic has been published yet.**
- Checked in the box products (`$W/review_1118/pt.py BOX TAGS lat,lon [k]`): r6c2 false water all land, reservoir
  water, Beluga margin clean; Grewingk lump +3.7 -> -12.6 m (casts -17 to -19: still 4-6 m shallow); Fox basins
  -8.0 / -6.1 -> +0.9 / +0.8 m; Fox square step gone.
- **Not yet checked (owed to Hig before the hand-off):** r6c4 gate rows perched_over_deep_ha 0.25 -> 0.90 and
  clip_shelf_cells 2451 -> 2849; the hill in r3c5 (59.26687,-150.88567); Petrof r4c5 (gate: 2 rows); r3c3's 4 gate
  rows; Picnic Harbor and the west-edge line once r3c3 / r4c0 have solved.
- Sleeps on battery stretched r3c3's chain to ten hours (caffeinate -s only holds on AC).
- `$W/stop_1119.sh` is a one-off timed stop for that evening; its deadlines are hard-coded, do not reuse as is.

**Was running:** `$W/run_1118.sh` under caffeinate (log `$W/run_1118.log`, per-box change report `$W/run_1118_diff.txt`), resumable; order:
Hig's issue boxes r6c2 r6c4 r7c4 r3c5 r4c5 r3c3 r4c0 -> publish `kbay_joint_s3` (`$W/publish_s3.sh`; mosaic on
/Volumes/Powder/lidar_build/kbay_joint_s3, tags s3,s2,s1,cm19,cm18) -> rows 7..0, a publish every 8 boxes.  **Don't edit joint.py /
solve.py / fill.py in the repo while it runs** (test edits in /Volumes/Nunatak/lidar_build/lidar_dev).  The edits of today (solve.py, joint.py,
rules_ledger.json) were committed at 13:45 with the run already going on them; pre-edit copies `$W/*_before_1118.py`.
**Disk:** Nunatak 85 GB free after Hig's go-ahead to remove the old mosaic folders (run2 cm17 fix2h_blend cm16 cm5 s1; per-box archives,
dev-viewer copies and the cm18 mosaic kept); the run needs ~55 and stops itself under 20 GB with a log line.
**Hig's review of s2 (2026-11-18) and what was found** (window solves in `$W/review_1118/solve`, tools at.py cmp.py lab.py there):
- Homer flats as water (41 ha, r6c2 cm21): the any-water lake-sheet rule of 1fd33b7.  Tightened (sheet under, not beside, the other
  survey's water) it still took 14 ha of crop c18_flat; **off by default now** -- the reservoir stays water by Hig's hand-placed depths
  (crop c17_res0).  Hig asked about lidar INTENSITY: only part of KBay 2023 has a raster (obs/kbay_2023_intensity), none for the Homer
  surveys, joint uses none -- untested, open.
- Beluga / reservoir "stitch margins": not ArcticDEM -- the solve's low bound put a 30 m ring inside the shore 0.6 m under the lake
  (evidence within 30 m = the shore held at the level).  `under_ &= ~still`.  bel_fin vs a plain window: 183 cells differ.
- Fox flats square = r7c4 tile r3000 c5000 FAILED (pyamg coarse pinv: SVD did not converge) and kept the product: splu fallback.
- Fox flats closed basins: product bed < -6 m is the "deep" prior even with no datum: `deep_datum` (a deep patch with no cast, measured
  bed, compilation or window edge in/beside it is no prior) + `pit_post` (closed basins raised in the plain solve) + the low bound
  smoothed on its high side (60 m square blocks; scarps 3.95 -> 0.60 km in the window).
- Outer-coast island planed at the tide (59.26678,-150.88559, r3c5): Hig's closed high-tide island line IS in tide_lines.geojson; the
  solve read islands only from tide_islands.geojson (7 polygons).  Closed island lines (1,719) are prior land now; fixed above the tide.
- Grewingk lump 59.60276,-151.07896 (r6c4, s1): current code gives -12.6/-15.6 there (casts -17/-19); the knob 100 m SW (+21 m) is REAL
  (Berg casts +7..+21).  West-edge line (r4c0) and Picnic Harbor's cast misfit (r3c3): s1 boxes had no casts-as-data deep pass; rerun.
- **NOT fixed:** Petrof Beach 59.36015,-150.82741 -- 3DEP stands 14-16 m at Hig's high line, then a flat 3.7 m plate 70 m wide, then a
  -1.5 m plate (high_line stage; stair-steps = 3DEP's edge).  A design question for Hig.  Picnic: H05187 (1915-31) casts sit on land by
  up to 30 m of height -- a position shift for that survey is not made.
- A mound in a sounded lake is checked against the casts before it is called an artifact (spot2.py / casts.py were scratchpad tools).
**Load:** three window solves at once drove the load average over 300 (BLAS/AMG threads): run window solves ONE at a time.

## Resume here (2026-11-12, 10:00) — full run on one code: run_1110b (A B C) then run_1112d (D)

**Running:** `$W/run_1110b.sh` (groups A -> publish, B -> publish, C -> publish) and `$W/run_1112d.sh`, which waits for
"run_1110b done" in `$W/test_cm18.log`, then group D = the 17 boxes no earlier rule touched (rows 0-1, r2c0 r2c1 r5c0 r5c5 r6c5)
+ the 6 that ran before cfe8b9f (r3c4 r4c3 r7c2 r7c3 r5c2 r4c1), then publishes all 48.  r6c3 requeued at the end of group A.
**Live 2026-11-12 09:42 (cfe8b9f, ledger 0948e7e), from Hig's Homer Spit tip review** (crop harbor_t, dev1112d):
`structure` (joint: returns with no ground/water/bed near sea-level water = structure over water -> water), `superseded`
(composite: a lower take not fully compatible with class-now never shows under a higher layer -- DGGS 2019 decks blended
through the green-lidar bed), `bedprotect` (highline: a measured bed is protected from the tide lines -- Hig's high-tide trace
along the deep-water dock stood at +4.9 on -39 m), `perchedsound` (fill: NOS/CRM casts stale in water above 4.5 m), `spitpuddles`
(water/edits.geojson dmax 0.1).  Entrance +1.95 -> -8.26, spike line +4.88 -> -39.48, small-boat floor +2.75 -> -3.3.
**Open:** a few dock decks remain where the DGGS 2019 POINT cloud classes them ground (joint bit 128); gate clip_shelf counts
Hig's 0.1 m puddles by design.  Don't run crops beside a fill: r6c3 took 8.5 h under swap pressure 2026-11-11/12.

## Resume here (2026-11-10, 19:10) — run_1110b: the 31 boxes the rules touch, on a41945e

**Running:** `$W/run_1110b.sh` (log `$W/test_cm18.log`): group A r6c3 r5c3 r4c1 r7c4 r6c2 r6c1 r6c4 r4c5 r3c4 r4c3 r7c2 r7c3 r5c2 ->
publish; B r7c0 r6c0 r2c5 r3c5 r2c4 -> publish; C r7c1 r4c2 r3c1 r3c3 r5c1 r5c4 r4c4 r7c5 r2c2 r2c3 r3c2 r3c0 r4c0 -> publish.
run_1110 was stopped in r6c3's joint.  Untouched by any rule (scan of joint outputs, left as they are): rows 0-1, r2c0 r2c1 r5c0 r5c5 r6c5.
**New rules (Hig 2026-11-10):** `changetaper` (recipes' change_pairs kbay2023-bathy2009, Woz front), `derivedwater` (ArcticDEM at/above a
lidar's water is no ground), `lowangle` (flats/active deltas: no traced tide lines, polygons or low-tide cap; 3DEP in their gaps unless
at/above a lidar's water -- Grewingk Creek delta, Hig: the traced shorelines are not synchronized with the data there).
**Open:** the 59.55909,-151.27770 channel's pocked OCM floor (smooth it or drop it -- asked Hig); /lidar/ "saturates at" reads m/yr
(Hig took it for m: offer a metres-equivalent label, main repo).  Low-angle crop set (wozflat tongue selhead ad_r7c4 kasflat) was still
running at commit: check review_1110/*_lowangle.png and $W/run_lowangle.log.

## Resume here (2026-11-10, 17:50) — run_1110 rerun of all 48 boxes; three more rules live mid-run (53ff22a)

**Running:** `$W/run_1110.sh` (log `$W/run_1110.out`, `$W/test_cm18.log`), group 1 of 3; r6c1 done, r6c2 in its fill.  r6c1/r6c2 lack 53ff22a.
**Hig 2026-11-10:** Seldovia strip 59.42358,-151.87706 is an artifact; Woz basin 59.54998,-151.38389; plateau 59.55444,-151.38656.
- `dgbed` (joint): derived ground (Chugachmiut's DTM, n=1 a cell everywhere) below 0 m over a bed beyond low water - 1 and > 3 m under it
  yields.  sb_r4c1 12.4 ha, chug_t 5 ha; strip -0.7/-1.2 -> -4.6/-5.3.
- `fillzoneday` (fill): joint writes zone_day.tif; the fill's casts older than the zone's survey are dropped (woz_t 17).
- `perched` (fill): the low-tide cap skips river/tidal bodies whose own water stands > 0.5 m above -1.87 (the basin: min -1.87 -> +0.76).
- The plateau is the REAL 2023 front against the 2009 multibeam (OCM 2023 there = the same multibeam): the vintage seam.  Asked Hig
  whether the older bed should yield within ~20-40 m of the front so the fill models a foreset.
- r6c1 gate clip_shelf 1144: upland Anchor River reaches at 67-92 m modelled exactly 0.1 m deep (as hazel_l's); pre-existing, not intertidal.

## Resume here (2026-11-09, 17:10) — Hig's answers on the review; tidal-flat + low-tide-water rules live (5235e9e); review boxes queued

**Hig (2026-11-09):** the Woz 2023 lidar is very good — use it "essentially with no modification, except minor (<1.5 m, maybe 0.5 m)
incision in channels"; only fit simple surfaces where there are TRUE data gaps; real sandbars (Woz, Kasitsna) stay; nothing in ArcticDEM
helps on these flats; high rocks are in the rocks/shorelines data and "anything not in these is not a real protrusion"; the Anchor Point
+1.5 m tongue (59.7287-59.7327,-151.847..-151.851) is "a large artifact"; sloped water implies shallow water (river-channel mapping).
**Live in the repo (5235e9e, ledger 1ab8a27):**
- joint `sheet_level_tol_m` 0.5 / `sheet_level_slope` 0.0015: a sea sheet whose part over the water/bed lies within the tide's reach and
  that SLOPES as a whole (>= 1 ha, rising >= 1 m) is a tidal flat; only its level pieces at that water's level go.  Same slope test for the
  own-water lake sheets.  Woz: 404 ha of KBay 2023 in the tide band, 159 kept before, 382 after.
- joint `low_water_stand_m` 1.0: ground in water/tide_low_water.geojson (-1.87 m) beyond 30 m of its line and 40 m of every rock/island,
  > -0.87 m, over a measured bed or cast 2 m below OR seen sparsely (< 50 % returns about) is the sea.  Hoen's flat (dense, nothing
  measured under) is spared.  Tongue: 17.4 -> 2.5 ha above -0.87 in the polygon (crop `tongue`).
- joint `derived_under_sheet_m` 6: compilations yield under a lidar sea surface within the tide's reach with no measured bed / deep cast
  under it (Kasitsna: KBay 2023's sea at -0.08 m over OCM's pocked -3.4 m, 54 ha).
- the mesa rule (from last night's dev copy) and the dump's `why` dict (a NameError with --dump before).
- fill `flat_fit_min_ha` 5: modelled cells of a tidal flat in a true gap take a robust quadratic/plane trend through the observed cells
  around them + the harmonic of the residual, raise only, under an observed water level and tide_max (Seldovia head basins).
- fill `low_water_cap`: modelled bed in the low-tide water polygon held at -1.87 (not in closed gaps of observed ground above it).
- fill `slope_water_depth_m` written but OFF (0): the median-gradient version raised Halibut Cove Lagoon's basin by 44-72 m (lagoon_t);
  sloping water needs the river-channel mapping first.
**Crops:** wozflat, kasflat, selhead, tongue (+ the ten regression crops' abA/abB/abC); compare with `$W/crop_vs_crop.py <crop> devTAG repo1109`.
**Running:** `$W/run_1109r.sh` waits for run_1109 (r7c4's fill took > 2.5 h, old code), then r5c3 r5c2 r4c2 r7c1 under 5235e9e and
`publish_cm18_1109r.sh`.  The other 44 boxes still lack these rules (rules_applied.py cm18).
**Bradley Lake:** /Volumes/Nunatak/lidar_build/obs/bradley/ — georef v1 (Hig: good enough), lakebed v1 (Table 8 within 3 %), points CSV;
the flooded valleys outside the 1955 lake are a placeholder; AEA's NV5 2022 lidar (DOWL) and 2016 lidar not public — Hig is asking.
**Open:** Bluff Point 1.3 ha of Homer 2018 kept by the slope test (-0.4 m, 11 returns/cell — real lower beach?); channel incision on the
flats; r3c5 sounding bias (59.29582); cast-disc depressions; Bear Cove; Gore/Petrof lakes; 59.41784 r4c0.

## Resume here (2026-11-09, 00:40) — Hig's second cm18 review (≈45 spots); overnight queue running; mesa rule in dev only

**Running:** `$W/run_1109.sh` (log `$W/test_cm18.log`, `$W/republish_cm18.log`): r6c3 r5c3 r6c1 r7c1 r5c2 r4c1 -> publish dev,
then r4c3 r6c2 r4c2 r7c3 r7c4 -> publish (`$W/publish_cm18_1109.sh`).  Code = HEAD (gap fit + derived-sheet rule; review source 1 at -0.5 m).
**Triage of Hig's spots** (probes `$W/spot.py`, `$W/zoom.py`): (a) most "seam" artifacts = a land survey's SEA SURFACE left in holes of the
2009 multibeam (Chugachmiut's DTM plane at -1 m, KBay 2023 / Kenai 2008 returns; the sheet rules need the bed UNDER the sheet): the
derived-sheet rule already clears r4c1's (crop mesa_r4c1); the rest wants the **mesa rule** (`lidar_dev/topobathy/joint.py`, not live:
a level piece in the tide's reach whose ring is mostly a bed bed_stand_dz_m below it) -- catches the KBay spikes (59.46560, 59.47241) and
r4c3's rims; not yet the 150 m Chugachmiut hook at 59.36553,-151.83973 (920 cells at -1.07 m, joined to the shore piece).  Crops
mesa_r4c1 mesa_r4c1b mesa_r4c3 mesa_r6c3 mesa_r7c3 spike_r5c3 spike_r5c2 (baseline joint in each crop's `base/`).  (b) the seamount at
59.17719,-151.06135 = tide_lines feature 5250, a 25 m "high tide (Hig)" ring at 4.45 m in 107-160 m of water (the only such stray found).
(c) Woz front / Kasitsna = OCM 2023's own surface on the flats.  (d) swales 59.68006 / 59.66122 = interior fill at the base contour.
(e) soundings: vs NOS 1977-84 ours NMAD 0.57-0.78 m unbiased (NCEI 1/3" 0.69 where it covers); vs 1915-31 NMAD 1.2-2.1 m;
59.29582 (r3c5) biased shallow (median +2, p90 +9.5): to diagnose.  Bradley Lake: USGS OF 56-62 sheet 1 (1955 contours) + WSP 1610-A table 8.

## Resume here (2026-11-08, evening) — cm18 reviewed by Hig; gap fit + derived-sheet rule live; lake recipe in dev only

**Live in the worktree (local commits c63d42a, eb81cbd; rules `gapfit`, `gapfitspeck`, `derivedsheet` in the ledger):**
- `fill.py` **gap fit** (end of `fill_holes`): modelled wet cells (mask 1/2/3/4/9) within `gap_fit_w0_m` 6 m of an observed
  patch (>= `gap_fit_min_cells` 25) take the harmonic through the observations, fading to the model by `gap_fit_w1_m` 25 m;
  ceiling kept.  Fixes Hig's multibeam-edge list on the saved outputs: Halibut Cove Lagoon grooves and wall mesas (-6 m in a
  -60 m bed), Coal Cove mesas (-3.9 -> -6.4 vs -7.6 obs), the 59.358 trough (+8..13 m), Homer flat lines.  Not the r5c2 "hole"
  (observed 2009 multibeam to -26 m at a swath edge: an outer-beam question).  Prototypes `$W/gapfit_proto.py`, `$W/feather_proto.py`.
- `joint.py` **derived-bed sheet rule**: a lidar sheet within the tide's reach, flat by |z - 5x5 median| <= 0.3 m and level
  < 1 %, standing > 4 m over the shallowest compilation on half its cells, is the sea with the pocks it encloses.  Took
  Kenai 2008's +3.8 m strips off Anchor Point (anchor_t gfix2: 331 sheets, 17.6 ha); a rougher +1.5 m strip remains there
  (sea at another tide, or a real reef? ask Hig).
- First box with the gap fit: **kj_r4c1** (run_1108c.sh, fill from 21:42; its joint ran before the sheet rule), then
  `final_publish_cm18_1108_r4c1.sh` republishes dev.

**Lake recipe (Hig: "the marine version where there are soundings") — NOT live: `/Volumes/Nunatak/lidar_build/lidar_dev`
(a copy of tools/lidar), patch `$W/lake_marine_wip_20261108.patch`.**  Petrof (26 soundings < sound_hyps_min_n 30, so Dmax
101 m from the rim; each sounding a pinned cell with a lone-pin disc + harmonic cone).  Tried on crop `petrof_t`: (1) the
sea's basin prior + rollover + soundings as spline sites (chi 0.2): no cones, prior 7 m shallow, spline pit; (2) profile with
Dmax from the soundings (53 m): the spline at chi 0.2 overshot 32 m above the water from 25 soundings, guard cut it onto its
cone (creases); (3) profile/basin + the soundings' misfit by the cascade, faded at the shore: smooth, but the narrow north
arm (300 m wide, 40-52 m deep 100-150 m from shore) sits inside every shore-distance construct and the misfit stays 17 m
(p90).  Next idea: let the soundings, not distance to shore, carry depth in narrow arms (shore-at-level sites + soundings in
one smoothing fit of ABSOLUTE z, no distance-based prior), and lower sound_hyps_min_n.

**Gate notes from today's six reruns:** r6c3/r6c4 "regressions" are the Grewingk River reach (pre-existing, 2164 cells) and
Glacier Spit's back-barrier lagoon now kinded perched at 3.4 m (surface identical); r4c0/r6c0/r7c0 rows MISSING = gate.py
crashes on mixed 2 m / 4 m rasters (fix gate, not data); r7c1 clip_shelf = Anchor River lakes/channels inland.

## Resume here (2026-11-05, late morning) — full-bay run cm16 stopped after 13 boxes; three shore-model faults found; fixes next

**State.** The contour shore model (high shore authoritative, low level from real bed slopes, rocks as soundings,
convex ramp; branch commit `5fa0168`, local only, not pushed or merged) ran the nine test boxes and then the full
bay under tag `cm16`. Hig stopped it on 2026-11-05 after reviewing an interim mosaic: the queue was emptied and the
main loop killed, so no mosaic or publish follows. `kj_r6c2` and `kj_r6c3` were left to finish (in their fill step
at ~10:30; kept awake by `caffeinate -w` on their run_box pids); they ran the old rules and must be checked for the
faults below before they are kept. The 35 boxes never started are listed in
`/private/tmp/claude-506/-Users-Hig-Claude-projects-landslidescience-topobathy/139a4b79-d429-427d-9429-157512d2ad84/scratchpad/queue_cm16_left.txt`
(the same list is `ORDER` in `run_bay_parallel.sh` minus the 13 run boxes).

**Finished under cm16** (archives `$W/<box>/out_cm16`, `W=/Volumes/Nunatak/lidar_build/composite`): passed the
gate `kj_r3c4 kj_r3c5 kj_r2c4`; gate-held `kj_r3c3 kj_r2c3 kj_r2c2 kj_r2c1 kj_r4c4 kj_r3c2 kj_r4c2 kj_r4c1`.
Held rows (current vs best): r3c3 solid-over-level 460 cells (0), r4c1 336 (3) + bed-labelled-land 1943 (1151) +
perched-over-deep 0.69 ha (0), r2c3 142 (0), r3c2 129 (0), r4c2 97 (28) + perched-over-deep 12.92 ha (3.12, NOT
yet explained), r2c2 90 (0); land/water km2 "moved" rows on most, some by a lot (r2c1 land 3.05 -> 36.86) — probably
comparisons with much older runs, unconfirmed. Boxes ran 1-1.5 h each except r4c1/r4c2 (~4 h; their fill 2.7 h
against ~1 h on fix2h — cause unknown).

**On the dev viewer:** `kbay_joint_cm16`, "Contour shore model, 11 boxes so far", an interim feathered mosaic of
the 11 boxes done by 09:19 (published by hand with run_bay_parallel.sh's own mosaic/merge/publish steps; merged
review files in `$W/kbay_joint_cm16/`; edit checks 5526, not honoured 1310, unexamined). Also kept:
`kbay_joint_cm16_r3c4`, `kbay_joint_cm16_r3c3`, `kbay_joint_cm15_taylor`, `joint_v3j` (its removal asked, unanswered).

**Hig's review of the interim mosaic, and what each fault is** (all new in cm16; earlier runs cm1/cm5/fix2h were right):

| Spot (Hig) | cm16 | fix2h | Fault |
|---|---|---|---|
| 59.18565,-151.59493 (r2c2) | -2.3 m plate ~130 ha, casts to -61 m "not fit" | -48.8 | 1 |
| 59.18479,-151.77715 (r2c1) | -3.4 m plate 1.4 ha amid -50..-110 m | -66.7 | 1 (code and setting match; reach not tested there) |
| 59.17311,-151.42426 (r2c3) | flat top at -90.96 m, 9 ha | -118.6 | 2 |
| 59.44631,-151.74859 Hoen's (r4c1, r4c2's margin) | -3.7 m over 3.4 ha where lidar reads 0.80 | 0.80 | 3 |

1. **Mapped-shore band swallows open sea** (`shoreface.py`, the "THE MAPPED SHORES ARE THE MODEL'S SHORES" block):
   `m_band = C & Hw_ & ~Lw_ & within shore_mapped_reach_m (1000 m) of a low line` is "between the shores", and
   `tide_low_water.geojson` covers only the mapped coast (1,481 km2 against high water's 2,745), so open sea past the
   low polygon's extent within 1 km of a low line becomes intertidal (fill_mask 9, at the base contour) and its deep
   casts are set aside as "not fit, for review". Tested at r2c2: 185.6 of the 185.9 ha the rule predicts carry
   fill_mask 9; the plate's south edge is 1 km from the low line. Same signature (clusters of deep not-fit casts) at
   59.29378,-150.74956 (r3c5, 62 casts up to 58 m off) and 59.20888,-151.79502 (r2c1, 16 casts up to 19 m). Bay-wide
   up to 6,056 ha (2,228 ha wider than 200 m) — an upper bound, as real flats are in it too; largest piece 940 ha at
   59.70158,-151.27993 (unrun box). Fix: only between a low line and a high line that face each other (not past the
   low polygon's mapped extent), and a good cast deeper than the low level puts its cell back in the sea.
2. **no_peaks clips deep-water peaks flat** (`high_line.py no_peaks`, called from `composite.py` ~line 910): the
   spline's -79.6 m overshoot between casts of -82.2 and -93.2 was cut to -90.96, the surface at the -82.2 cast's
   cell (itself 8.8 m deep of the cast: basin soundings aggregate to ~210 m sites). r2c3's highline log: peaks 135 ha
   (max 12 m), pits 268 ha (mean 5.2, max 23 m). Fix: limit no_peaks to the shallow water near the mapped shore (it
   was written for the shore), or anchor to the cast's value and never cut a flat top in deep water.
3. **Hoen's inner flat dug as sea**: its returns are classed water at its own level (joint.py's comment ~line 684:
   Hig, October — a horizontal mud flat at 0.8 m; no measured bed to tell it from water), and the low-water polygon
   takes it in, so the new rule makes it sea right up to the low line. **Hig, 2026-11-05: the lidar height is right;
   fix it in the classification, as a reviewer edit, not a universal rule** ("nearly perfectly flat mud ... genuinely
   a little hard to tell whether it's mud or water even when you're standing there"). Draft polygon
   `tools/lidar/water/hoens_ground_draft.geojson` (EPSG:6334, 9 pieces, 6.7 ha, main 4.9 ha; KBay 2023 water-class
   cells within 0.15 m of 0.8 m, closed, filled, 2 m buffer), properties in the edits.geojson style with
   `kind: "ground"`, `draft: true`. Hig may edit it in QGIS. To build: a `ground` edit kind (polygon) applied in the
   class step — water-class cells inside become ground, so the lidar stands — then move the feature into
   `water/edits.geojson`. The outer lagoon's slough stays water.

**Update, 2026-11-05 ~12:30 (before a session restart).**
- Hig edited the draft: **use feature 1 of `hoens_ground_draft.geojson`** (Hig's polygon, 15.6 ha, one part, bounds
  570846-571369 E, 6589950-6590680 N, EPSG:6334, no properties yet). Feature 0 (my draft) is superseded; its 0.81 ha
  outside Hig's polygon are pools Hig judges mostly real but very shallow, so they stay water.
- `ground` edit design: in `joint.py fuse`, right after each lidar source's `obs_lidar` read, inside the polygon set
  `z_g = zmin_g = z_w`, `n_g = n_w` where `z_g` is NaN, then `z_w = NaN`, `n_w = 0`; log ha changed. `water.py`'s edit
  matcher skips `kind == "ground"`. Move Hig's polygon into `water/edits.geojson` (check its CRS: water.py transforms
  edits from EPSG:4326) with id `hoens_lagoon_inner_flat`, kind `ground`, who Hig, date 2026-11-05, a `why`.
- **Nothing is running now.** r6c2 finished 11:26 and r6c3 at 12:26, both archived as out_cm16 and both GATE-HELD:
  r6c2 solid-over-level 958 (best 219), land 128 km2 (was 166); r6c3 solid-over-level 3616 (best 226), land 120.5
  km2 (was 150.6). The land drops (-38, -30 km2) are unexplained; check them for fault 1 (mapped-shore band) first.
  Run-read files may now be edited.
- **Shallow-pool review (Hig asked: is there an inconsistency in how tide-flat pools are handled? report, don't
  change rules aggressively).** 120 pools on the flat (<= 2 ha, level -1.38..4.5 m, banks at or above the water) in
  the 11 cm16 boxes, 34.1 ha. Cut = level minus bed p2. Between shores 88 (median 0.00 m), tidal lens 3 (0.05),
  interior prior 12 (4.40; 10 inside the mapped low water, bed at the base contour -3.37), ground prior 6 (4.42; 5
  outside the low water, cause undetermined), by soundings 6 (1.89), observed bed 5 (5.10, mostly Seldovia, likely
  real). Against fix2h: ground prior 4.27 vs 0.11, interior prior 4.37 vs 0.33 -- the deep cuts are new in cm16.
  **The inconsistency:** inside the mapped low water the mapped-shores block makes a pool sea and digs it to the base
  contour, overriding the tidal lens (observed surface is the surface). Modest fix offered: inside the mapped low
  water, water observed above the low line's level stays on the lens. Deepest: 59.38286,-151.87502 (6.95 m),
  59.35971,-151.89911, 59.33129,-151.77379, 59.44538,-151.70750, 59.35228,-150.87167. Not yet reported to Hig.

**Update, 2026-11-05 afternoon — the fixes are built (uncommitted) and three test boxes are running under tag `cm17`.**
- **Hig's decisions:** build the pool fix; remove every old run from the dev viewer except the 13-box one (done: `joint_v3j`,
  `kbay_joint_cm15_taylor`, `kbay_joint_cm16_r3c4`, `kbay_joint_cm16_r3c3` out of the catalogue, pyramids and dev_viewer work dirs
  deleted, catalogue backup `catalog.geojson.bak_202611051351`; their sources stand: `joint_v3/out`, `ct_taylor/out_cm15`, the boxes'
  `out_cm16`); proceed in the order below.
- **Step 1, r6c2 / r6c3:** fault 1 absent (their 181 / 372 ha past a closing edge are Mud Bay's flats: casts -1.8..-0.7 m, fitted);
  fault 2 mild (111 / 44 ha moved below the base, dead-flat pieces all under 0.6 ha, against r2c3's 354 ha in 50 pieces over 1 ha, the
  largest 122 ha at -183.5 m, 59.14913,-151.33306); fault 3 not in them; r6c3 has 12 pools dug over 1 m (1.6 ha). The land drop
  (-38 km2 in r6c2) is 38.2 km2 of upland at 100-425 m from source 3 that carries no class in cm16 (shore_label 0, class_now 0):
  a labelling difference from run2, not a shore fault. **Rerun both with the others** (cheap: the stages before the fill take 6-10 min).
- **Fix 1** (`shoreface.py`, the mapped-shores block, `_past_closure`; `tide_vectors.py closures` -> `water/tide_low_closures.geojson`,
  written by `build` too): the low water's outline has closing edges that are no shore (a segment over 150 m, or further than 1 km from
  3DEP's sea edge: 377 of 1,539 km). Band cells whose nearest low-water edge is one of those are judged by DATA: the median of the three
  nearest good casts (within 500 m) more than 1 m under the low shore's level is the sea; else the flat. No casts in reach: land behind
  the low water = past the mapping, land facing = between the shores. Neither test alone works: the closing-edge test alone dropped Mud
  Bay (a broad flat's low shore is drawn in long segments), the land test alone kept half of r2c2's plate (land across the bay).
  Measured on the cm16 archives with the model's own functions: r2c2 165.9 ha dropped (the plate), r3c5 165.3, r2c1 10.0 + 5.5 (both of
  Hig's spots), r6c3 2.1 of 372 ambiguous. Also: good casts > 1 m under the low level inside the band put their 20 m back in the sea
  where that joins the sea or is no pocket.
- **Pool fix** (same block, `_banked_pools`): the water stage's bodies (tidal / perched / ephemeral, <= 2 ha, level between low tide
  + 0.5 m and the tide's top) in the mapped low water whose ring is banks (not the low water's void, no observed ground below the
  level, on 70 %) keep their observed surface (fill_mask 7). On the archives: r4c1 14 pools, r4c2 18, r6c3 41; it catches bodies dug
  4-8 m (59.44409,-151.70815; 59.39979,-151.68614; 59.39776,-151.69255). Not caught: pools ringed by more mapped low water (r3c3 body
  24, 1.25 ha), and the five outside the low water (cause still undetermined).
- **Fix 2** (`high_line.no_peaks(base=)`, `composite.py` passes the base contour): the reconstruction runs on the surface clipped at
  the base contour, so only what stands above it can be a peak; a pit holding a basin below the base is left as fitted. Synthetic test:
  deep rise and hollow untouched, shallow bump and hollow as before.
- **Fix 3** (`joint.ground_edits`, called in `fuse` after each lidar's `obs_lidar`; `water.py` skips kind `ground`): Hig's polygon is
  in `water/edits.geojson` as `hoens_lagoon_inner_flat` (EPSG:4326). In-process on r4c1: 3.44 ha of KBay 2023 water returns become
  ground. `hoens_ground_draft.geojson` is now redundant (untracked; delete once the test passes).
- **Running:** `kj_r2c2`, `kj_r2c3` (fills started 14:24), then `kj_r4c1`, two at a time, detached
  (`scratchpad/run_test_cm17.sh`, log `$W/test_cm17.log`); each writes `$W/<box>/shore_dump_cm17.pkl` (the shore model's inputs, for
  re-tests without a fill; delete after). Compare with `scratchpad/compare_runs.py cm17 <box>` (spots, flats, changes by cause).

**Update, 2026-11-05 ~16:15 — cm17 results, a fourth fault (the 2008 lidar), and the cm18 queue.**
- **cm17 on r2c2 and r2c3: the fixes land.** Gate rows identical to cm16 in both. r2c2: the plate at 59.18565,-151.59493 is
  -58.2 m (cm16 -2.29, fix2h -48.8); 165.9 ha back to the sea; |surface - cast| p90 within 1.5 km of it 37.7 -> 6.5 m (fix2h 10.7),
  unchanged 1.5-4 km out; dead-flat modelled ground below the base 103 -> 4.5 ha. The surface moved > 0.5 m on ~5,000 ha of r2c2's sea
  (median +-1 m; up to 9 m lower just east of the plate): the ~100 casts cm16 set aside are in the fit. r2c3: dead-flat 357 -> 3.7 ha
  (no piece over 0.7 ha), the surface at the casts unchanged. On the dev viewer as `kbay_joint_cm17` (mixed mosaic, `mosaic_blend.py`
  now takes a tag list `cm17,cm16`: each box from the first tag it was run under); `kbay_joint_cm16` kept beside it for the difference.
- **Fault 4 (Hig, 59.68841,-151.34924, r6c3: "we're not successfully overriding the 3DEP or ArcticDEM with the 2008 lidar ... looks
  mixed-source").** Two causes in `joint.py`, both fixed (uncommitted), tested in-process on a 3 km window (joint surface there
  36 -> 86 %; in the 800 m at the spot 9.4 -> 89.5 %, Kenai 2008 has ground on 99 %):
  1. *Epoch rule*: ArcticDEM (derived, dated 2016) superseded Kenai 2008 by its date wherever it had cells, then yielded to that same
     lidar as a derived product: both gone, the cell void, the composite's 3DEP there (r6c3: Kenai 2008 superseded on 50 of 66 km2,
     42 km2 "void under vegetation"). Since ArcticDEM joined the layer (2026-10-31), so in every fix2*/cm* run. Now a derived product
     supersedes only other derived products.
  2. *Own-lake sheet rule* (2026-10-31): a "sheet" is every connected cell flat over 10 m, so a sloping bench or fen is one sheet,
     and ten cells beside a pond took all of it (71.6 ha of Kenai 2008's bare ground in the 9 km2 window). Now only the sheet's cells
     within `own_sheet_tol_m` of the nearest of the survey's water returns are the water's surface (11.4 ha).
  Affects every box holding Kenai 2008 beyond newer lidar (the north shore: r5-r7), and the sheet rule any lidar's sloping flats.
- **Queue, tag `cm18`** (cm17's code + the two joint fixes; Hig: known-issue reruns first): `kj_r2c1` (the false shoal at
  59.18376,-151.77269; started 16:11), `kj_r6c3`, `kj_r3c5`, `kj_r4c2`, `kj_r6c2`; one worker (`scratchpad/queue_worker.sh cm18`,
  queue `$W/.queue_test_cm18`, log `$W/test_cm18.log`; start a second copy when r4c1's cm17 run ends, fill since 15:40).
  `kj_r4c1` cm17 predates the joint fixes (it holds no Kenai 2008; the sheet change could touch its KBay 2023 sheets): judge Hoen's
  and the pools on it, and rerun it under the final tag with the rest. r2c2 / r2c3 likewise before the final mosaic.
- r4c2's perched-over-deep 12.92 ha, explained: 8.3 ha is the lagoon at 59.44074,-151.42861 (body 10, level 1.5 m, 10 ha; observed
  and sounding-fitted bed to -8 m: probably real, a question for the water stage's kind), 3.4 ha Hoen's flat, ~1.2 ha dug pools.
- The five dug "pools" outside the low water (Nanwalek, 59.35,-151.92): specks under 0.12 ha in lidar-observed ground where the 3DEP
  prior stands 8-15 m over the lidar (fill_mask 8). Another mechanism; left.

**Update, 2026-11-05 ~17:45 — the whole bay is queued under `cm18`; the dev version rebuilds itself.**
- Code committed locally: `afe23d0` (not pushed). `cm18` = that commit.
- **Results so far.** r2c1 (cm18): the false shoal at 59.18376,-151.77269 is -51.5 m (cm16 -3.44, fix2h -63.0), the plate -70.3, the
  deep-cast cluster -16.4 (fix2h -16.1); 15.5 ha back to the sea. r4c1 (cm17): Hoen's flat 0.81 m, observed, equal to fix2h within
  300 m; 11 pools at their observed surface (1 in cm16); 5 pools in the mapped low water still dug ~4.4 m (ringed by unobserved low
  water, not banks: 59.44538,-151.70750, 59.39819,-151.68880; Hig told, no decision); a 15 ha dead flat at -3.88 m at
  59.33297,-151.78324 (fill_mask 3, not the high-line step's; 12.7 ha in cm16; not diagnosed). Hig, 17:40: "looks good".
- **A mixed-tag mosaic blends a rerun box with its neighbour's old margin** (Hoen's read -0.05 m in the mosaic: r4c1 0.81 m against
  r4c2's cm16 margin at -3.84): the 2 km either side of a line between a rerun and an old box is provisional until both are rerun.
- **All 13 run boxes are rerun**, not only the six: the five "kept" boxes carry fault 2 (flats the high-line step cut below the base:
  r3c4 408 ha in 62 pieces over 1 ha, r2c4 172, r3c3 115, r3c2 45; r4c4 none, rerun for the sheet rule), and r2c2 / r2c3 / r4c1 ran
  as cm17 before the joint fixes.
- **Queue** `$W/.queue_cm18` (one id a line; empty it to stop after the boxes in hand): r4c2, r6c2, r3c4, r2c4, r3c3, r3c2, r4c1,
  r2c2, r2c3, r4c4, then the 35 unprocessed (`$W/queue_cm16_left.txt`, run_bay_parallel's order). Two workers
  `$W/queue_worker2.sh cm18 2` (at most 2 run_box.sh at once; a refused box is retried after 10 min), log `$W/test_cm18.log`.
  r6c3 and r3c5 were started by the first workers (17:03). About 2 h a box on the cm16 timings (0.8 h open sea, ~4 h where there is much shore: r4c1, r4c2, r6c2, r6c3), not the 1-1.5 h first written: the reruns end about the morning of 6 Nov, the last box about the evening of 7 Nov.
  20:00 check: r3c5 passed its gate (plate at 59.29378,-150.74956 -3.25 -> -17.14 m, fix2h -29.67; dead-flat below base 350 -> 28 ha; not-fit casts 89 -> 20). Its deep sea moved > 0.5 m on 18,000 ha (median +-1.8 m): the basin soundings spline was refitted with 72 more soundings, a site grid of 312 m in place of 308 m and its own lambda 2.2e-04 in place of 9.9e-05 -- a change within the spline's cast misfit (NMAD 3.4 m), not the flat-top fix (that step touched 111 ha, was 438). r6c3's joint stage: class_joint covers 95 % of the 800 m around 59.68841,-151.34924 (16 % in cm16) -- the 2008 lidar fix holds in a full box. The three shore_dump_cm17.pkl files are deleted.
  22:30, Hig's review of cm18: the 0.9 m cast at 59.16419,-151.51308 (nos_h05187:55404) is a keying error in NCEI's file -- the 1931 smooth sheet shows 12 fathoms (the "12" read as "1/2"); corrected to 21.9 m by the new
  `cast_edits.py reread` (SHORE_REVIEW_WORKFLOW.md section 3; basis in water/cast_edits.geojson; the edited CSV and the dev database carry it).  It lies in r1c2, r1c3, r2c2, r2c3, all still queued when it was made.
  Ten more lone casts under 1.6 m among neighbours of 15 m or more (scan of all 326 k): seven lie within ~140 m of a shore, one 37 m from a charted rock awash (h05188:57123), one on a reef 3DEP shows above water
  (h09890:99840) -- left as they are.  One is open: nos_h04759:6591 (59.60354,-150.51992, 0.6 m among 24 fathoms, 225-265 m off the shore), 3 km east of the boxes, so it touches no build; not checked against its sheet.
  Also 21:15: water_review.py crashed on any box with a level_conflict flag (fixed, 2d22ef3; r6c3's review rerun).  And 20:30: the "good cast back to the sea" and past-closure tests took any cast 1 m under the local low
  line as the sea's (r4c2/r4c1, lagoon at 59.40558,-151.69395: 69 casts at -1.8..-0.1 m sunk 2-4 m); they now need the cast below the base contour too; r4c2 rerun to confirm, r6c2 onward built with it.
  23:10: r4c2's rerun confirms it -- no cast above the base sunk (was 69), not-fit casts 235 (cm16 246), |surface - cast| p90 0.79 m (was 1.43 with the fault), 7.7 ha of the box changed against cm16 (Hoen's flat, the pools).
  Boxes built before the change: r2c1, r3c5, r6c3 -- each checked clean of it (scratchpad disc_check.py: casts above the base whose cells went band -> sea and now stand > 1 m above the surface).
  nos_h04759:6591 is settled (Hig): the nearest cast to the shore off a delta, coming up onto the delta's margin; it stays.  r2c2 moved to the front of the queue for the reread cast (started 23:09).
  r6c3's gate row perched_over_deep 3.9 -> 11.1 ha is a label only: one 7 ha sheet of sea-surface returns at 3.4 m (59.61649,-151.18915) went tidal -> perched as its level spread fell 0.40 -> 0.28 m; its bed is the same.
  Open for Hig in r6c3: between-the-shores ground fitted below low tide with no casts near (59.66816,-151.41808, -2.5..-3.4 m where cm16's flat top stood at -1.88; box-wide 148 ha of the band is > 1 m under low tide, 118 in cm16).
  2026-11-06 01:30, Hig's review: "circuitous troughs" at 59.40840,-151.69846 and pimples at 59.40945,-151.71187; 59.41913,-151.89300; 59.35974,-151.81483 (head of Seldovia Bay and west). All are in cm16 too. Causes:
    (a) troughs, 0.8-1 m: KBay 2023 "ground" chains at the flight's water surface count as measured ground, so OCM/NCEI yield in strips 6-16 m wide along them (the Hoen's gap rule); the strips are in the mapped low
        water, the sea's, and the basin's ceiling cut them to 0.1 m under the base (-3.38) with the compiled bed at -2.5 m on both sides.
    (b) pimple 3 m (59.40945): one KBay 2023 "ground" cell at +0.13 m, 13 returns, at the water level, H09940 casts at -3.6 m 15 m off. No measured grid under it and the lagoon returned densely: neither speck rule applies.
    (c) pimple 4 m (59.35974): Chugachmiut sea-surface cells over the 2009 multibeam at -8.6 m are removed, the 30 m2 hole is a pocket "the flat's", and a flat is held at or above the base.
    (d) pimple 4.5 m (59.41913): in the 2009 multibeam grid itself (8 m across at -18.5 m); none of ours.  Not checked against the chart.
    TRIAL, in scratch copies only (the bay run reads the working tree, so NOTHING is applied there until Hig approves): crops art_a (570500 6585000 575000 6592000, of r4c2, with Hoen's Lagoon) and art_b
    (566000 6579000 569000 6582000, of r4c1), tags base / fx2 / fx3 under $W/art_a, $W/art_b.
      fx2 = two shoreface.py changes ($W/art_a/fill_fixes_20261106.patch): the basin's ceiling follows a measured bed at or under low tide standing above the contour (harmonic from the rim, 0 on the contour); a void
            whose every neighbour is a measured bed under the base is the sea's, not the flat's.  Result: troughs gone (cells > 0.5 m under their surroundings within 150 m of the first point 1349 -> 20); 95 ringed
            voids from a median 0.8-1.3 m (up to 4.2 m) above their rim to 0.00; no observed cell changed; Hoen's inner flat 0.81 both; art_a casts p90 |r| 0.43 -> 0.35 m; 4.2 + 0.3 ha changed > 0.25 m; no time cost.
            On dev as kbay_joint_art_a_base / kbay_joint_art_a_fx2.
      fx1/fx3 = a joint.py speck rule ($W/art_a/speck_rule_trial_20261106.patch: a speck at the survey's own water level with soundings about it > 2 m under).  NOT PROPOSED: the first cut missed (b) (shore casts within
            100 m) and took 734 cells of a steep shore's waterline ground in art_b, after which NCEI, no longer yielding between them, stood at -5.5 m against the beach; the second (casts within 50 m, > 30 m off the
            survey's larger ground) still missed (b) -- the "ground" chains at the water level beside it are larger ground by that test -- and still moved the art_b shore.  (b) stays open: level and size do not tell a
            stray of the water surface from wet ground at the water's edge.
    Also landed: r2c2 (corrected cast 59.16418,-151.51297: -3.38 -> -24.01 m, no pinnacle; plate -2.29 -> -58.16 m; dead-flat 103 -> 4.5 ha; one cast at 59.18312,-151.60921, -1.3 m, now 10.7 m over the sea, not looked at),
    r2c4 (gate clean), r6c2 (solid_over_level 1172 cells against cm16's 958, best 219: not traced).
  2026-11-06 01:34, Hig: "let's go ahead and implement these for future boxes" -- the two fill fixes (fx2) are in shoreface.py from this commit.  First box with them: r3c2 (its fill started after 01:34); every box after it
    in the queue.  Still on the old fill, under cm18: r2c1, r3c5, r6c3, r4c2, r6c2, r2c2, r2c4, r3c4, r3c3 -- NOT requeued (Hig asked for future boxes only); the troughs and ringed holes stay in those nine until a rerun.
    Check on r3c2 when it lands: the two new log lines ("ceiling follows a measured bed", "voids ringed by a measured bed"), its gate, and compare_runs against cm16.
  2026-11-06 03:10, r3c2 (first box on the new fill) and r3c3, r3c4 checked.
    r3c2: "the basin's ceiling follows a measured bed standing above the contour on 3.00 ha, up to 1.61 m"; no ringed void in this box.  Gate equal to cm16 on every row (solid_over_level 129 = 129).  Casts: median
      +0.00 m, NMAD 0.05 (cm16 0.06), p90 0.31.  Dead-flat ground below the base 46.0 -> 0.9 ha.  Run time unchanged.
    r3c3: gate equal to cm16 (460 = 460); dead-flat 118.0 -> 3.2 ha.  r3c4: gate clean; dead-flat 408.5 -> 0.1 ha; not-fit casts 32 -> 13.
    OPEN, A LAKE BED 3-6 m HIGHER IN r3c2 (59.31430,-151.73801, 13 ha at 8.42 m, no soundings; modelled in both runs): NOT the fill fixes.  The joint's lake-sheet rule since afe23d0 ("of such a sheet, only the cells at
      the water's level", the fix for the Kenai 2008 bench east of Homer) removed 20.62 ha in this box where cm16 removed 23.06.  In this lake 0.16 ha of KBay 2023 "ground" in 45 specks (largest 184 m2) along the west
      shore and by the island, 0.05-0.28 m above the lake's level (median 0.15, the rule's own_sheet_tol_m), is kept; the water stage calls it land, and the lake's modelled bed follows: the west arm 8-14 m deep ->
      2-6 m, the deepest point 21 -> 16.5 m, median +3.1 m over 10.4 ha.  Whether the specks are a sedge margin or the water's surface is not known (the true shore within 30 m sits 0.03-1.3 m above the level, median
      0.26, so height does not tell).  How 0.16 ha moves the whole basin was not traced inside fill.py's lake prior.  Scan of the 10 landed boxes with a cm16 run (scratchpad lakescan.py): lake beds moved > 1 m only
      here and on 7.2 + 3.4 ha in r6c2 (three ponds near 59.71,-151.74 up 1.4-2.8 m; 59.64473,-151.47036 down 1.6 m); none elsewhere.  Not changed: a joint rule edit mid-run is Hig's call.
    ARCHIVE FAULT FOUND AND FIXED: run_box.sh archived out/*.json and *.geojson by hard link, and those files are rewritten in place, so every later run overwrote the archived copy of every earlier tag (fill_lakes.json,
      fill.json, report.json, water_review.json, water_flags.geojson, polygons_auto.geojson, high_line.json, rock_review.geojson: out_cm16's read as cm18's in the boxes already rerun).  Rasters (new files each run),
      logs (copied) and the gate's saved metrics ($WORK/gate/<tag>.json) were never affected, so no raster comparison or gate verdict is in doubt.  What was overwritten cannot be recovered short of a rerun.  Fixed:
      run_box.sh copies the reports; the 1,458 linked report files under $W/*/out_*/ were given their own copies at 03:00 (content as it stood); the watcher repeats that for boxes whose run began before the fix.
    WATCHER: scratchpad watch_cm18.sh (boxcheck.py + compare_runs.py + lakescan.py per landed box) -> $W/checks_cm18.log; stops on a fault (rc, a gate row worse than cm16, a traceback, < 25 GB free) or every 6 boxes.
  2026-11-06 08:25, r2c3, r4c1, r4c4 (reruns) and r6c4 (Grewingk's box, the first unprocessed one) checked; 15 of 48 landed.
    r2c3: gate = cm16 on every row; dead-flat below the base 357.5 -> 3.2 ha; ceiling rule 15.0 ha (to 1.69 m); lake voids unchanged.
    r4c1 (first run of Hig's Seldovia Bay points on the fill fixes): "371 voids ringed by a measured bed ... the sea's: 0.80 ha", ceiling
      rule 90.7 ha (to 2.06 m); gate solid_over_level 336 -> 328, perched_over_deep 0.69 -> 0.53 ha, bed_labelled_land 1943 = 1943; casts
      median +0.03, NMAD 0.15; changed > 0.5 m on 29 ha (24 ha higher, median 0.9 m).
      At Hig's points (scratchpad art_check.py: modelled cells against their own 30 m median within 200 m), r4c1 cm16 -> cm18:
      troughs 59.40840,-151.69846: cells > 0.5 m under 1,627 -> 43, > 1 m under 151 -> 23 -- gone in r4c1's run; BUT THE POINT LIES 6.8 km
        INSIDE r4c2 AND 1.2 km INSIDE r4c1, so the mosaic there is mostly r4c2, built on the old fill (r4c2 cm18: 1,651 / 254) -- the
        troughs stay on the dev surface until r4c2 is rerun (Hig's call; the two boxes differ by > 0.5 m on 540 cells near the lagoon).
      pimple 59.35974,-151.81483: the point itself -4.34 m (between the shores) -> -8.69 m (the sea's, by the ringed-void rule).
      pimple 59.40945,-151.71187: cells > 0.5 m over 248 -> 244 (the speck pimples, the rule not proposed); 59.41913,-151.89300: observed cells.
      lagoon 59.40558,-151.69395: modelled cells below the base contour cm16 1,051 / cm17 8,288 / cm18 825; masks as cm16 -- clear.
      3.2 ha at 59.44618,-151.74850: modelled -3.75 (mask 2) in cm16, observed 0.81 m in cm18: the reviewer's ground polygon (joint.log: "ground edits: kbay2023: 3.44 ha
        of water returns inside a reviewer's ground polygon are ground"), new since cm16 -- intended.
    r4c4: gate = cm16; lake bed higher by > 1 m on 0.2 ha only.
    r6c4: rc 0.  Only run2 to compare with.  perched_over_deep 9.35 ha (run2 2.20): 7.1 ha is the 3.4 m sheet at 59.61651,-151.18916
      already explained under r6c3 (label only), the rest small bodies at 2.0-2.6 m over Halibut Cove Lagoon's -50..-70 m bed near
      59.567,-151.193 (= run2's 2.2 ha).  solid_over_level 284 cells (run2 50): 0.11 ha in 31 pieces, mostly 1.1-1.3 m over levels of
      -1 to -2 m (59.59321,-151.17758).  The face rows against Grewingk's own box, run 20j: sounding misfit p90 1.95 m (20j 3.22),
      clip_shelf 19 (445), face_bad 0, face_level_spread 0, face_shallow 0.005, Emerald 27.1 m (26.6).
    GATE FAULT FIXED: face_sounding_misfit_p90_m opened joint_v3.tif by name and read MISSING in the first bay box to hold the face;
      it now opens the product by its own name (Win.name_of).  r6c4's gate/cm18.json and gate.log re-saved (the first log kept as
      out_cm18/logs/gate.log.before_fix).
    THE FILL'S LAKE LOOP TOOK TWO HOURS A BOX, FIXED: fill.py formed (lab == i) & ~chan over the whole grid for every non-marine
      domain before its area test, and lake_ids holds the dry voids too -- 65,284 domains in r6c4 for 66 lakes; the fill stage ran 142 min
      there (r4c1 80, r3c2 77, r4c4 48, r2c3 44), and r5c3's was 2.5 h into the same loop (macOS `sample` of the process: array ==,
      ~, &, sum and nothing else).  Now one np.bincount gives every domain's cell count off the channels and a domain under
      lake_min_m2 is skipped before its mask is formed; the bodies loop skips domains holding no body cell the same way.  Same
      numbers by construction; checked on crop hazel_l (14,321 domains, 57 lakes): all 33 rasters cell for cell equal, fill_lakes.json
      equal, fill.json differs only in "seconds" (117 -> 38).  Live from 08:21 (boxes whose fill starts after: r5c2 on).
      scratchpad: patch_lakeloop.py, same_out.py, crop_ab.sh, gate_where.py (where a box's solid_over_level / perched_over_deep cells
      are), fillprof.sh -> fillprof.log (every 4 min, the hottest symbols of each running fill: a coarse profile for what is left).
    MACHINE: fseventsd holds ~20 GB and a full core (37 h of CPU); swap 31.5 of 32.8 GB; memory free 71%.  Not touched (root's).
  2026-11-06 12:20, watcher batch: r5c3, r5c1, r4c3, r5c4, r3c1 all rc 0 (21 of 48 landed; dev republished by hand at 11:42 with 19, Hig's ask).
    r5c3: fill 213 min (the old lake loop); r5c1 15, r4c3 34, r5c4 20, r3c1 64 min since.  r5c4: casts median +0.35, p90 3.51 m (138 casts;
    7.2 ha of ringed voids); r3c1: 7,675 casts, NMAD 0.57, |r|/sigma p90 4.1 -- nos_h09879/h09878 (1977-84) against the multibeam they lie on
    (p90 |r| 2.0-2.6 m, the surveys' own placement), and nos_h10030's 38 casts with six on land at 11-15 m (cast review items, not the fill);
    perched 3.2 ha at 59.32766,-151.78217 over a bed at -3.0..-3.5 = the earlier open item (the flat near -3.88 m at 59.33297,-151.78324).
  2026-11-06 13:00-15:10, HIG'S REVIEW OF cm18, THREE MORE (all diagnosed; two rules tested on crops and LIVE from 15:09 -- joint.py the zone's day, shoreface.py
    the ring; boxes whose joint started before 15:09 (r7c4 14:57, r7c3 13:54 and every box before) lack the first, those whose shore stage ran before it the second):
    RESULTS: (A) the Sea-neighbour guess was wrong (lbase == lfix, 0 cells): the gaps touched, at a corner, a hole cell of ANOTHER DOMAIN (domains 4-connected,
      the voids 8-connected), read as "not bed".  Now any void leaves the ring whole; what breaks it is observed ground at or above the base, the grid's edge or
      data of the void's own.  lagoon_t: 93 -> 112 ringed voids (7.18 -> 8.40 ha), 3,140 cells lowered ~54 m to the bed, none raised; lattice cells at -13 m
      among a bed below -50 m 3,398 -> 434; what remains is the lagoon's walls (the shore face meeting a -57 m bed at the survey's outer line).
      (B) sfix4 regressions: plates_t and bluff_t byte-identical; seldovia_t 0.43 ha in 23 specks (<= 0.06 ha) along the harbour mouth's beach zone, the bay's
      sheet over the multibeam untouched (67 of 72 sheets still condemned).  Live.
    (as first written at 13:00:)
    (A) "thin bathy stripes in the 2008 data ... extreme artifacts along them - worst in Halibut Cove Lagoon (59.57178,-151.19832)".  The 2009
      multibeam (bathy2009) lies in a lattice of lines in the -68 m basin with gaps a cell or two wide between them.  The point is in the overlap
      of r6c3/r6c4/r5c3/r5c4; r6c3 (fill 11-05 21:07, BEFORE the ringed-void rule 2e77b9f went live 01:34) fills every gap as "between shores"
      (mask 9) at -3.4..-22 m -- 4,858 cells in a 240x240 window, 55 m above the bed either side -- and the mosaic blends it in.  r5c3 (after) has
      none; r6c4 (after) still has 1,844: the lattice gaps the rule skips, (i) those at the grid's edge (r6c4's west edge is 280 m off; the mosaic
      weighs the owner's interior there once r6c3 is rerun) and (ii) those whose ring is broken by a cell already the sea's (3-7 of a 500-cell rim,
      the rest measured bed at -65 m).  TRIAL (lidar_fx4 shoreface.py): notbed_ excludes Sea, so a ring broken only by the sea's own void is whole.
      lagoon_t crop [600500,6603000,604500,6607000] of r6c3, lbase/lfix queued.  r6c3's rerun (already #2 in the order) is what fixes Hig's point.
    (B) "mesa issues on Woz delta: 59.55261,-151.39426".  KBay 2023 ground slopes smoothly -0.8 -> -2.9 m down the delta front (10 returns/m2, no
      water class, zmax-z 0.03 m; its own water returns at -2.9 m at the toe: an extreme low tide, the front exposed) over the 2009 multibeam /
      OCM compilation at -4..-9 m and NOS h09877's 1978 cast line at -6..-10 m, 50 m apart: the delta has built out.  The joint rejected the 2023
      ground piecemeal -- the sheet rule (a flat sheet with a measured bed or 5 casts >= 2 m under it is the sea) where the 10 m flat test
      happened to pass on a 3 % slope, and own_surf (ground within 0.5 m of its own water within 30 m, over a measured bed) along the channel
      behind the bar -- so the composite ran -8.5, -2.1, -1.7, -4.7, -3.9 cell by cell.  The point IS in vintage zone 1 (floodplain_kbay2023),
      which excludes only the other LIDARS: the 2009 bed and the casts stayed as evidence.
      TRIED AND DROPPED on woz_t [589000,6601000,593000,6604000]: (i) of a condemned sheet, only cells within 0.5 m of the sheet's height at the
      nearest evidence cell are the sea -- no change at the point (every cell of the front has the old bed under it, so each is its own evidence)
      and 173 ha moved on the delta flats; (ii) a sheet more than 0.5 m above the survey's own lowest water within 300 m was exposed that day --
      222 ha moved, flats raised 6 m: KBay's lines were flown at different tides, so a high-tide sea sheet sits within 300 m of another line's
      river water at -1 m.  KEPT ON TRIAL (sfix4): THE ZONE'S DAY -- per cell the date of the zone's allowed survey (ZD); inside a zone the sheet
      rule's beds, water and casts older than it are no evidence (bed_any, zwmin, zbmin, zbpmin; a point's year from its survey name, else its
      file's, undated = old), nothing changes outside the zones.  woz_t: 54 ha changed, all along the front; the transect reads the 2023 ground
      whole (-2.87 .. -0.84, the channel behind the bar at -2.7) with ONE step of 5.7 m at the lidar's edge where the 2009 bed begins -- the
      vintage seam, observed on both sides, which no fill can bridge; bridging it (the older bed yielding within N m of the newer ground's edge
      inside a zone, the fill modelling a foreset) is a design for Hig.  Sheet-rule regressions queued: plates_t (Taylor Bay 2.9 m over casts),
      seldovia_t (KBay -0.04 over the multibeam), bluff_t (Homer 2019 -0.57 over 11 m) sbase vs sfix4 -- the rule must leave them alone.
    (C) "a small alpine lake within the Chugachmiut lidar (59.27802,-151.53323) - it has a steep margin ... hydroflattened?".  NOT the Chugachmiut
      lidar: the vendor's be_mosaic.tif is nodata for 150 m around it.  The ground there is ArcticDEM patches (48 % of the rim) and the 3DEP 5 m
      prior (mask 8, the blocky hillshade); the lake (2.9 ha, mask 5) is a mapped polygon with level 450.00 m = 3DEP's hydroflattened surface ("a
      lake only the prior saw keeps the prior's level", water.py), and its bed is the Hollister bowl (dmax 8.3 m, 443 m at the centre).  Both the
      measured rim (ArcticDEM) and the prior's own land stand 451.0 (p5) .. 452.4 (p50) at the shore: the level is ~1.5-2 m low, the margin is a
      2 m cliff at the shoreline on top of the bowl.  The same signature on three bigger lakes in r3c2 with FULLY measured rims (Chugachmiut's
      flat water cleaned away, the 2012 IFSAR level kept): 37 ha at 59.28815,-151.43820 level 18.73 vs rim p5 21.0; 42 ha at 59.28290,-151.50338
      95.78 vs 97.5; 7.5 ha 35.08 vs 36.2.  A lake fills to its outlet: PROPOSED (not built) -- a level the prior alone gave is raised to the
      measured rim's low quantile when the rim stands above it.  Nothing under water can be recovered: no survey reached the bed.
  2026-11-06 17:00-17:30, THE LAKE RULE BUILT (water.py, LIVE 17:27) AND A FAULT IN THE 17:02 BATCH (Perl Island, kj_r1c2; fix on trial):
    (C) THE RIM OVER A PRIOR'S LEVEL, Hig's go-ahead with two cautions ("only in a place with mediocre data like this"; "lakes do not
      [always] fill to their rim ... gentle slopes below a rim may be real -- it's just a very steep rim like this that's suspicious").
      Built in water.py after the mapped-lake block: a lake or ephemeral body whose level is a PRIOR'S ALONE (no survey water, no mapped
      lake's surveyed level, no edit, above tide_max_m) takes the low decile of the measured ground nearest the water's void on each side
      (the innermost measured cells within lake_rim_m 20 m, inside the body or out; prior cells and a void collar are no ground) when that
      stands lake_rim_step_m 0.5 m or more over the prior's level, by at most lake_rim_max_m 6 m, with measured ground within reach on
      lake_rim_frac 15 % of the edge (>= 25 cells), AND when that ground is an independent measure (median |ground - prior| >= 0.15 m).
      Tested on lakes_t [582500,6571200,589800,6573900] of r3c2 (kbase / kfix / kfix2): the 40 ha lake at 59.28551,-151.50639 95.78 -> 97.16 m
      (the KBay lidar's dry shore on 100 % of the edge, 1.4 m over the 3DEP level; the shoreline step 1.6 m -> 0.2 m, 79 ha raised ~1 m through
      the basin), the 33 ha lake at 59.29063,-151.44453 18.73 -> 20.72 m.  Hig's alpine lake (59.27802,-151.53323) does NOT move, and the
      13:00 diagnosis was wrong on it: ArcticDEM there equals 3DEP's own shore to 2 cm (levelled to it where no lidar overlaps) -- the
      'measured rim 1.5-2 m up' was the prior's own bank read farther out.  3DEP's shore rises 1.2 m in 10 m from the 450.0 level; the margin
      is 3DEP's shape plus the bowl, and nothing is recoverable.  Hence the independence guard.  Regressions (water stage alone on the crops'
      out/): plates_t one 5 ha lake at 59.31120,-151.06901 205.46 -> 207.10 (lidar rim, 82 % of the edge); seldovia_t, bluff_t, woz_t,
      lagoon_t no body changed -- after the tide guard: two Seldovia slough pools at 3DEP's 1.1 m had been raised 0.6-1.4 m to their banks,
      and a tidal pool's banks stand over its low-tide water by nature (the tidal lens is its level).  Boxes whose water stage ran before
      17:27 lack it (r1c3 and r7c4 running then included).
    PERL ISLAND (r1c2, landed 17:01): 863 ha of the island (ground to 398 m) modelled as "between shores" sea bed, -27 m median against 3DEP,
      -116 m at worst (scratchpad/mask9_scan.py: r2c1 11 ha in 262 specks at 59.13838,-151.81339, r1c1 1.9 ha, every other box < 5 ha).
      Mechanism: no lidar in r1c2; ArcticDEM in patches (9.9 km2); the 3DEP fallback's "footprints" is layer_class.tif with near_m 60, so
      every gap between ArcticDEM patches was "flown" and the prior withdrawn from it (void); the island is enclosed by the compilation sea
      body, so the voids were "holes inside bodies that no survey saw as land, taken as water" (80 holes, 1206 ha), split into 636
      "ephemeral" bodies with no observation; the shore stage labelled them water, build 3 withdrew the prior inside the water, the fill
      carved them.  The gate saw it as flat_water_steps 6595 and land_km2 0.71 -> 11.12.  The same collar void rings the alpine lake
      (95 % of its 4 m ring is void between the hydroflat polygon and the ArcticDEM patches).  FIX ON TRIAL (lidar_fx4): joint.py's layer
      stage writes layer_flown.tif -- a lidar's return of any class (zmax), a NON-derived grid's cell (multibeam, sonar: the gaps between
      their lines are the sea), or water the layer decided; derived grids (ArcticDEM, the compilations, the cleaned vendor DTM) observe
      nothing where they have no cell -- and the recipes' fallback footprints read it instead of layer_class.tif.  perl_t crop
      [572500,6551000,578500,6556000] of r1c2, pbase / pfix queued behind lakes_t.  If it holds: template.json + every box and crop recipe
      (footprints only; the joint layer's class raster stays layer_class.tif -- a sed over the whole file hit it, perl_t's recipe fixed by
      hand), r1c2 on the rerun list, r2c1's specks too.  Boxes whose joint ran before the change read layer_class as before.
  2026-11-06 17:30-18:05, HIG'S SECOND REVIEW OF THE AFTERNOON ("patches of bad coastal data ... in the nearshore ocean near Anchor Point,
    59.72032,-151.83655"; "keep track of which rules were applied to which blocks, and we'll go back and rerun some of them"):
    THE LEDGER: composites/kbay_joint/rules_ledger.json (rule -> stage -> the moment the live file was replaced) and rules_applied.py TAG,
      which reads each box's stage start times from its chain.log and prints Y/n per rule and the rerun candidates per rule.  Add a row
      when a rule goes live mid-run; rules live before the run began are in every box.
    ANCHOR POINT: Kenai 2008's single returns classed ground at +1.7..2.2 m, one per cell over the nearshore sea, at the level of the
      survey's own water returns 1-4 m away (the sea at its 1.9 m stand), over NOS h10033 casts at -4..-6 m (4 within 100 m, 32 within
      300).  joint.py's speck-at-own-water rule skipped them: it requires the cloud to be SPARSE there (any return in <= 25 % of cells,
      the test that keeps Hoen's mud flat), and over this sea the water class is dense (~45 %).  TRIAL (lidar_fx4 joint.py, speck_cast_m
      150): a speck at its own water level is the sea when the shallowest cast within 150 m stands bed_stand_dz_m (2 m) or more below
      it, dense or sparse (hc_ = a separable maximum filter of zbpmin).  Crop anchor_t [564000,6619500,567500,6622500] of r7c1,
      abase / afix queued behind perl_t pfix2.
    PERL ISLAND, SECOND LAYER: with the flown footprint (pfix) the island's 740 phantom bodies fell to 6 and z - 3DEP over its land went
      from -46 m median to -0.2, but a plateau at 4-5 m stayed over 251 ha (fill mask 9, shore label 0/1, class_ctx3dep void): the fill's
      ground-prior rule saw no large DRY void (the interior void joins the sea's), and the shoreface's "prior above the tide's reach is
      land" rule was blocked by _ground_sea -- water_3dep.tif code 2, the MAPPED HIGH-TIDE WATER, covered the interior.  tide_sea.geojson
      feature 42 (286 ha, 3DEP p50 75 m) is a 200 m ring on the island's side of its own coastline: Hig's Oct 2024 High_tide_polygons.shp
      holds Perl Island inside Shore polygon #1 (1,201 ha) with no Island polygon, so `high` kept the island and buffer(line) & high fell
      inland.  Five such landmasses in the bay (3DEP >= 10 m blobs inside the high-tide water with a core beyond 200 m): 1,135 ha Perl
      Island, 1,555 ha Elizabeth Island (59.157,-151.829), 1,222 ha the east Chugach island (59.128,-151.475), 5,667 ha of mainland at
      59.354,-150.687, 154 ha at 59.456,-150.714.  FIX (live, 3c97e6a): tide_vectors.py `islands` cuts them from tide_high_water and
      tide_sea (536 ha of tide_sea cut, 44 -> 266 polygons; tide_islands.geojson written; `build` does the same); the ArcticDEM tide clip
      regenerated from arcticdem_ground_2m_v2.tif (clip voided 8.18 km2); topobathy_chain.sh rebuilds water_3dep.tif when
      tide_sea.geojson is newer.  perl_t pfix2 (flown + islands) queued; the flown footprint then goes live with the 76 recipes.
      Hig may also cut the islands in the shapefile itself; the automatic cut is harmless on top.
    QUEUE HELD from 17:33 (a placeholder process the workers count as a running box) so no outer box starts on the old footprint; r7c4 runs alone.
  2026-11-06 18:05-18:40, ANCHOR POINT RESOLVED ON anchor_t (three passes), r7c4 STUCK THREE HOURS IN THE SHORE STAGE (fixed, live 18:32):
    anchor_t afix (speck over a cast + flown footprint): pimples (observed cells > 2 m over a modelled neighbour, 1 km window) 4825 -> 173,
      but the surface at Hig's point rose -1.66 -> +1.22 m over casts at -5: with the strays gone the sea there became a TIDAL LENS domain
      (Kenai 2008's water class at its 1.9 m stand = "an observed water surface", so the bed was modelled AT the surface, 0.87 km2) -- the
      base had the same lens at -1.7, hidden among the strays.  afix2 (fill.py: the lens guard -- a lens cell whose shallowest cast within
      lens_cast_m 150 m stands lens_cast_dz_m 2 m or more under the surface is water over a bed, the floor path, the surface its ceiling;
      Taylor Bay's unobserved-body rule per cell): the hump gone, but the pimples back (1771) -- the strays the speck rule missed (9,526
      cells in the window in 375 pieces of 10-2,743 cells: too big for the speck test, too porous for the 5x5 flat test) had been dropped
      by the fill as "observations at the water level inside the lens", and off the lens they stood.  afix3 (joint.py: a FLOATING
      COMPONENT of any size -- >= 90 % of its cells at the survey's own water level within speck_near_m of its water returns, a cast
      >= 2 m below within 150 m of >= 50 % of them -- is the sea; a beach's component rises out of the water, a mud flat has no cast):
      7,495 pieces, 6.1 ha; the point -6.00 m on the compilation bed (casts -5..-6), pimples 2, surface - cast p50 -0.13 / p90 0.97 m
      (base +0.03 / 1.14), no observed cell left between 0 and 3 m in the window.  Hoen's inner flat has no NOS cast within 300 m.
    r7c4 (the Fox River flats, 1,048 bodies, 593 tidal): the shore stage ran from 15:41 at 99 % CPU with no log line (stdout block-buffered
      to a file) -- `sample` showed a Python loop of whole-grid boolean subscripts: shore.py's level_raster `for k in tidal_ids: bid == k`
      and the shoreline rings' `wl == k` per water component, each over 66 M cells (the fill had the same shape this morning, 8ce694c).
      Both now take each object's find_objects slice; outputs byte-identical on lagoon_t (labels, levels, 33 rings).  Live 18:32:48; r7c4
      killed (rc 143) and put back at the head of the queue; the queue held by two placeholder processes until joint.py / fill.py go live.
  2026-11-06 18:45-19:10, ALL NINE RULES LIVE; THE EMPTY BOXES: joint.py (flown footprint + speck/floating over a cast, zone-aware except
    under lone returns -- the first cut read the Woz front's 1978 casts through the zone's edge and condemned 11 ha of the 2023 front; and
    Anchor Point's strays had made their own beach zone, so a lone return (n_g <= 2) is judged by every cast) live 18:45:51 with the 76
    recipes on layer_flown.tif (f251ab1); fill.py (the lens over a cast) live 18:55:17 (ba8b209; lagoon_t / plates_t fills changed 0.0 /
    1.4 ha).  Regressions of the joint on seldovia_t 0.29 ha, lagoon_t 0.37 ha, woz_t 0.10 ha; anchor_t's strays stay condemned (7,355
    floating pieces, 4.1 ha).  Ledger rows tideisl, flown, floatcast, lenscast.  Holds released 18:58; r7c4 (rerun) and r1c4 started.
    r1c4, r0c1, r0c2, r0c3 then FAILED in 20 s each: no source has a cell there (open water beyond every lidar and grid; the casts and
    CRM are the fill's points, not the layer's) and fuse took max() of no dates.  Fixed (90a596a): an empty box writes a void layer; r1c4
    run by hand as the test -- its water stage found the sea from the points (212 km2, one body) and the chain went on; the three r0
    boxes put back at the head of the queue.  The watcher now keys rc lines by line number (a rerun box is checked again).
    QUEUE TRAPS LEARNED: the workers count `pgrep -f "run_box.sh kj_"`; a shell command of mine containing that literal held a worker
    (never put the literal in a command while the run is up); a placeholder `sh -c 'sleep N; true' run_box.sh kj_hold` holds the queue.
  2026-11-06 20:30-21:30, HIG'S THIRD REVIEW: GREWINGK'S SPARSE SOUNDINGS, THE ISLAND BENCHES, THE ROCKS:
    "There are a few soundings in upper Grewingk Lake where the model diverged from them, leaving little spikes beneath them or holes
    above them. Given the sparse points in this area, our best guess should be that these points are correct - even if they imply
    cliffs on the bottom of the lake. We should also look closely at the code to make sure we have appropriately converging logic
    between our marine soundings and lake soundings."  Measured on kj_r6c4 cm18 against all 6,085 Grewingk soundings
    (scratchpad grew_probe*.py, grew_cmp.py): 5,237 sit in cells the joint holds exactly (surface - sounding = +0.36, the survey
    shift); 529 sit in cells the BUILD dropped from the joint's mask (masks/joint.tif 0) -- first read as the despeckle, in fact the
    sequential rules' CEILING: "a bed is below every water surface ever seen over it", and 3DEP's hydroflattened lake at 41.3 m
    (the lake is 49.0) counted as a surface seen, so 474 of the 527 (the shallow soundings, 24-49 m) were "beds above the water"
    and fell to the prior -> a hole -> sparse sigma-1 points at chi 1, missed by up to 10 m; and the cell the joint holds pinned
    the harmonic residual exactly at ONE cell -- a log cusp, the spike / the pit (needle = cell minus its 8-neighbour median: p90
    1.0 m on the 2017 track, 9.3 m on the 18 higman 2019-21 points, 5.2 m on the hand soundings).  THE TWO PATHS DIFFERED: sea
    casts (nolens domains) are fitted by shoreface's spline at shore_cast_chi 0.2 with gap smoothing and are never cells; lake
    soundings are cells (the points unit) + harmonic pins + a chi-1 spline over the hole soundings only.  Fixes, all live:
      (a) build (composite.py post_masks, 20:58:08, fbb91f5, ledger pointcell): a cell a POINTS source contributed to keeps the
          rules' take at full weight -- never despeckled, never feathered (kinds from joint_report.json beside source_bits).
      (a') build rules (axes.py, 21:15:11, 29b7912 + 0c9cfff, ledger priorceil): a prior's water (axes.is_prior: the fallback's
          class step -- the first cut tested condition.py's _prior flag, which 3DEP never carries, and did nothing) neither sets
          the ceiling nor evicts.  grew_t gB3: soundings in holes 467 -> 60; |surface - sounding| p90 0.19 -> 0.00 m.
          (A 105 s slip at 21:15-21:17: the module function is_prior called through a local bool of the same name, TypeError
          in build 1; no box started a build in the window.)
      (b) fill (22:07:24, 981e072, ledger lonepin): a LONE observed cell's residual holds over a disc to half the distance to
          the nearest other observation, <= lone_pin_max_m 20 m -- the harmonic's bounded descent from the disc's edge (a
          crater as steep as the neighbours ask) instead of the cusp; along a track the discs join the fixes.  The first cut
          made lone cells thin-plate SITES instead of pins: the plate through 2,400 track cells with 50 m residuals overshot
          by 78 m (needles 1 -> 26 m, 235 m above the cap) -- the harmonic's maximum principle is what keeps a large residual
          honest; the spline is for the small ones.  grew_t: needles 9.6 -> 0.54 m (higman 2019-21), 5.1 -> 0.32 (hand),
          1.0 -> 0.75 (the track: adjacent fixes' own noise); lower bounds still violated after the fit 35 -> 3.
      (c) chi: lake_chi 0.2 for LAKE domains (the sea's shore_cast_chi); points_chi stays 1.0 for lens domains -- one chi for
          all moved 310 ha of plates_t by up to 25 m (its sites are CRM's sigma-10 centres): its own question, open.
      Regression (fill only, live vs trial): lakes_t 3.1 ha changed (max 2.95 m, the discs), lagoon_t 0.1 ha, plates_t 59 ha
      at p1/p99 -0.07/+0.15 (max 4.5 m beside a lone cell).
    Crop grew_t [604400,6607400,610200,6610600] of kj_r6c4: gA = (a) alone, gB = (a)+(b)+(c) as a fill-only trial on gA's inputs.
    ELIZABETH ISLAND (59.15984,-151.79546, "a broad planar bench ... cuts into topography"): the surface is a plane at 3.78 m where
    ArcticDEM has 80 m; the point is inside the PRE-CUT high-tide polygon (the ring around the island) and inside tide_islands.geojson
    now -- the tideisl rule (18:03); kj_r1c1 (15:13) and kj_r2c1 ran before it.  PERL ISLAND ("the land is really crazy"): the
    whole island's land is a modelled surface (smooth blobs, median 35 m under 3DEP, 90 % of land cells off by > 5 m): the pre-cut
    polygon covered the island wholesale, its land voided by the ArcticDEM tide clip and the prior's water mask, filled as sea floor.
    Same rule; r1c2, r1c1, r2c1 rerun (perl_land.png in the scratchpad: surface | 3DEP | ArcticDEM).
    ROCKS ("I'm not seeing the rock data exposed?"): no rock layer reaches the dev viewer (per-box rock_review.geojson; the viewer's
    soundings come from the dev DB); in the surface a rock is a floor capped under the low line and never overrules an observed
    surface -- over the landed boxes 789 acted as soundings (520 lifted > 0.5 m), 883 sit under an observed surface, 66 not
    modelled.  Waiting on which Hig means; the dev-DB survey of the rocks is the cheap one either way.
  2026-11-06 23:40, OVERNIGHT (Hig: "Continue processing overnight, and once all boxes are run once, rerun any that we know need
    rerunning. I'll take a look in the morning."): 41 of 48 landed; the queue holds the first pass's tail (r2c0 running, r3c0 r4c0 r5c0
    r6c0 r7c0, then r1c4 r0c1 under the rimless rule) and then the nine known-fault reruns in this order: r1c2 (Perl) r1c1 r2c1
    (Elizabeth) r7c1 (Anchor Point) r6c4 (Grewingk) r6c2 r6c3 (the lagoon, Woz) r5c3 r3c2 (the lake rule).  scratchpad
    overnight_finish.sh (its own caffeinate) waits for both workers to exit, then the final mosaic + dev republish (the loop
    republishes only when the box count grows) and writes scratchpad/morning_cm18.txt: results, non-zero rc, every box's gate line,
    the rules table, the watcher's tail.  Gate "MOVED" rows on r5c5 / r7c5 (land_km2 24.6 -> 115.5 / 160.8) are invariants from an
    earlier footprint, not quality rows; r0c0's three MISSING rows are an empty box's.  Open for the morning: the rocks question;
    the lens-domain chi (plates_t); Perl's ArcticDEM land after the rerun; the ledger's wider rerun set (most boxes lack most rules).
  NEXT AFTER THE RUN (Hig, 2026-11-06 10:30): MORE GREEN LIDAR FOR THE HOMER AREA -- additional topobathy point clouds Hig is organising, over
    the coast where sand bars sweep along the shore (Bishop's Beach, the Spit), so surveys of different dates will seam inside the bars.  Work them in
    as units (a ground unit and a bed unit each, own date and sigma, like homer2018 / homer2018bed), check their datum (the geoid model in the LAS
    headers: Seldovia 2019's xGEOID17B lesson) and their level against Homer 2018 on fixed ground and bed, then VINTAGE ZONES (joint.py "vintage":
    a mask or a beach zone, "allow": "newest") over the mobile coast so the newest survey holds a bar whole and the seam falls at the zone's edge;
    a bishop_t crop before any box rerun, and fold it into the reruns of r6c2 / r6c3 rather than run those twice.  Needed from Hig: flight dates,
    LAS/LAZ with the bottom and water-surface classes kept, the datum, footprints.
  2026-11-06 10:00, HIG'S REVIEW OF cm18, THREE POINTS IN r6c2 (old fill): "swaths of points that create false roughness along Bishop's Beach
    (59.63130,-151.58239) - maybe mixing two lidar surveys"; "a data-hole rendered as a mesa" (59.64705,-151.66371); "and here 59.60151,-151.49855.
    I think we had fixes for these so it looks like something of a reversion."  Hig: keep going with new boxes, circle back to some at the end.
    A REGRESSION OF cm18, YES, FOR TWO OF THE THREE: fill.tif is the same in cm16 and cm18 on these cells; in cm16 high_line.no_peaks ran over the
      whole sea and closed every modelled peak and pit nobody measured, and since afe23d0 it stops at the base contour (the 357 ha of dead-flat
      tops and bottoms) -- so what the fill does wrong below the base now shows.  Mosaics: absent in run2 and cm17 (= cm16 here), present in cm18.
    (1) Bishop's Beach: ONE survey (homer2018bed), not two.  Diagonal bands 120-160 m wide where its bed returns are sparse; the holes between
      them were "between the shores" (mask 9) at -4.6 m in a bed at -9 m.  scan_steps.py (modelled cells > 2 m above every observed neighbour,
      a bed among them): r6c2 cm18 73.6 ha, cm16 0.56 ha.  CURED BY 2e77b9f (the ringed-void rule), which r6c2 predates: crop bishop_t on the
      current code: "38281 voids ringed by a measured bed under the base ... the sea's: 37.54 ha", 0.22 ha left; gap cells off their measured
      neighbours by > 0.3 m: 46% (r6c2 cm18; cm16 13%) -> 3%.  What stays is the survey's own noise in its sparse swaths (NMAD 0.06-0.07 m,
      as in run2 and cm16).
    (2) 59.60151,-151.49855: GAPS 1-4 CELLS WIDE BETWEEN SURVEYS, all at -8 m, FILLED AT -45 m; 14 ha in r6c2 (to -128 m where the prior is -90),
      17 ha in r6c4, in every box so far.  Not the casts (all -8..-10 m there) and not the spline: THE GUARD'S CONE (fill.py, 2026-10-31: "bed
      held below the water, descending from the shore at 1 deg") -- 2.5 km off the shore it asks for 44 m of water.  FIXED, live 09:56 (boxes
      whose fill starts after: r5c1, r4c3 on): where the nearest observation of any kind is a bed under the cell's own water, the cap is no
      lower than that bed plus cap_bed_margin_m (1 m, and never more than half that bed's depth -- with the full margin beside a bed just under
      the water the cap became the flat plane 0.1 m under it: 358 more shelf cells on upbay_t).  Only ever a relaxation.  Crops, current code
      against the fix: seam_t (new, of r6c2): gap cells > 2 m off their measured neighbours 10,321 -> 0, guard-held 10,408 -> 4, p1..p99 of
      (modelled - observed neighbours) -54.9..+0.08 -> -0.08..+0.09 m; hazel_l (lakes): 0.20 ha changes > 0.1 m, none > 1 m; upbay_t (the
      tidal creeks the cone was made for; first form of the margin): guard-held 806,574 -> 789,666 cells, 7.6 ha of 4,800 change > 0.1 m.
      The final form, current code -> with it: bishop_t: guard-held cells 1,193 -> 58, gap cells > 3 m off their measured neighbours 1,535 -> 605,
      trenches 0.46 -> 0.01 ha (scan_steps), 0.45 ha changed in all; upbay_t: guard-held 806,574 -> 791,848, 7.5 ha of 4,800 change > 0.1 m
      (0.9 ha > 1 m), clip_shelf_cells 509 -> 688 (first form 867; fix1..fix2h 609-626; the gate's tolerance 500).
      First box built with it, r5c1 (10:14): "the cap relaxed by a measured bed beside the cell on 19.4 ha (by up to 186.1 m)"; scan 0.04 ha above,
      0.64 ha below -- one piece, 59.44928,-151.70439 (Seldovia Slough above the bridge, also in r4c1 and r4c2, cm16 too): 4.9 ha of the sea's void
      modelled at -3.3 m (to -7.8) beside seldovia2019bed at -0.6..+1.5 m, no casts; with it the perched 0.5 ha at 59.45051,-151.70871 (level
      3.70).  The earlier open item (a pool ringed by unobserved low water, dug to the base contour), not the cone.
    (3) The mesa, 59.64705,-151.66371: NOT a reversion (run2, cm16, cm17, cm18 all 1.08 m; bluff_point v1/v2 of 2026-10-15 had -10.5).  4.76 ha
      of Homer 2019 "ground" at 0.93-1.21 m -- the sea that day -- in a hole of Homer 2018's bed.  The sheet rule wants a MEASURED bed or lower
      water under 5% of the sheet, or 5 soundings: under this one there are 19 measured cells at its rim, 2 NOS soundings (-10.5, -10.0 m) and
      the compilations (ocm2023 + ncei2010, -10 m, derived: no evidence for the rule).  OPEN, Hig's call: a rule for a sheet over DERIVED beds
      far below it (say > 5 m over half of it), or an edit.
    Scratchpad: bishop.py, mesa.py, mesa2.py, hist.py (a point in every archived run), moshist.py (a point in every mosaic), regr.py (cm16 v
      cm18 with the fill's intermediates), scan_steps.py, corr_map.py, nbr.py, diff_out.py, patch_cap.py; crops bishop_t, seam_t (make_crop.py).
    TO RERUN AT THE END (Hig): every box whose fill began before 09:56 has the cone's trenches; the nine on the old fill also the holes.
      scan_steps.py over the 16 landed (ha of modelled cells > 2 m above every observed neighbour beside a bed / > 5 m below every observed
      neighbour): r6c2 73.6 / 14.1; r6c3 23.6 / 6.6; r6c4 4.2 / 17.1 (to -40 m, 59.72242,-151.14915, shared with r6c3); r5c2 0.7 / 4.8 (to -151 m,
      59.58333,-151.69160); r4c1 3.6 / 1.6; r5c3 3.2 / 1.0; r3c3 0 / 1.9; r2c1 0 / 1.7 (-146 m on 211 cells at 59.14261,-151.79864, mask 9: not
      looked at); r3c5 0 / 1.5; r3c4 0 / 1.0; r3c2 0 / 1.0; r4c2 0.4 / 0.9; r4c4 0.1 / 0.6; r2c2 0 / 0.5; r2c4 0 / 0.3; r2c3 0 / 0.1.
      The "above" of r6c4 / r5c3 at 59.567,-151.201 (+52 m) is the wall of Halibut Cove Lagoon beside its -60 m bed: not a fault of this kind.
      Order for the reruns: r6c2, r6c3, r6c4, r5c2, r4c1 (+ r4c2 for Hig's Seldovia points), r5c3; the rest are under 2 ha each.
    WATCHER, changed: a box with no cm16 run no longer stops it for rows REGRESSED against run2 (it runs gate_where.py and writes the
      pieces instead); a MISSING row does stop it; the fill stage's minutes are printed per box.
  2026-11-07 09:25, THE MACHINE PANICKED AT 08:52 (kernel watchdog: "no checkins from watchdogd in 92 seconds"; the panic report's
    process table: the two running fills at 71 and 68 GB RESIDENT on a 64 GB machine, fseventsd 42 GB, compressor 35 GB, 14 MB free).
    THE RUN BEFORE IT: all 48 boxes landed by 04:04 and the dev surface kbay_joint_cm18 was republished with all 48 at 04:38 (every first-pass
    tail box rc 0: r2c0 r1c0 r3c0 r4c0 r5c0 r6c0 r7c0 r1c4 r0c1 -- their gate rows are the empty boxes' MISSING and land/water MOVED
    invariants only); of the nine reruns r1c2 (Perl, 02:41, land_km2 11.09 -> 15.27), r1c1 (02:50, clean) and r2c1 (Elizabeth, 03:31,
    land 36.9 -> 37.7) landed and are in that mosaic.  r7c1 (fill from 03:31) and r6c4 (fill from 04:45) were in their fills when the
    memory went: their fill1.log last wrote at 05:17 / 05:26 (r7c1 just after the basin spline for domain 1, r6c4 after the domain 8
    +ceiling1 spline and a coarsen RuntimeWarning at fill.py:226) and then ran 3.5 h with no line while growing to ~70 GB each.  Both are
    9750x10000 grids; their first-pass fills took 104 and 123 min; r2c1 (9750x6000) finished its rerun fill in 38 min with every rule.
    THE STEP THAT GROWS IS NOT YET IDENTIFIED (what follows those lines: the next _spline_residual call and the harmonic).  Their
    out_cm18 archives of the first pass are intact (a rerun archives only at its end), so the 48-box mosaic stands; their out/ holds a
    partial rerun that the next run overwrites.
    LOST: /private/tmp is wiped at reboot, so the whole scratchpad went -- overnight_finish.sh (never ran: no morning_cm18.txt),
    watch_cm18.sh, boxcheck.py, compare_runs.py, lakescan.py, gate_where.py, scan_steps.py, art_check.py, mask9_scan.py, grew_*.py,
    disc_check.py, patch_*.py, make_crop.py, same_out.py, fillprof.sh and the rest.  The run's own tools are in the repo
    (run_box.sh, mosaic_blend.py, rules_applied.py, run_bay_parallel.sh) and $W (queue_worker2.sh, republish_loop.sh); the checks
    log $W/checks_cm18.log stopped at 20:45 on 11-06 (the watcher was not restarted for the night).  Anything the run depends on goes
    under $W or the repo from now on, never the scratchpad.
    RESTARTED 09:24: ONE worker (`queue_worker2.sh cm18 1`, caffeinated) on the queue r7c1 r6c4 r6c2 r6c3 r5c3 r3c2, with
    $W/memguard.sh (every 60 s logs each stage's resident GB to $W/memguard_cm18.log; kill -9 over 44 GB) -- r7c1 alone first, so the
    guard log against fill1.log names the step if it grows again.  republish_loop.sh is not restarted: it republishes only when the box
    COUNT grows, which reruns never do; the final mosaic + dev republish is by hand when the queue empties.  Docker Desktop and the dev
    web container were started by hand (not restarted at boot).  Open from the night: the rocks question; the lens-domain chi;
    Perl's land after its rerun (not yet looked at); the ledger's wider rerun set.
  2026-11-08 01:45, HIG: "run the additional fixes, including the green lidar area, and the tip of the peninsula where I've added coastline".
    Commit ba9813f + ledger rows review1 homer1921 priorreach norim routeground rimlevel.  (1) REVIEW SOURCE 1: water/review_sources.json, level
    -1.75 m ESTIMATED (no image time; its low/sub rocks lie under KBay 2023's flat +0.1..+0.3 m water, so the image is lower; Hig named the line
    new_low) -- ask Hig for the image/time.  rocks.py build reads rocks_hig_review.gpkg (65 typed: 36 new keyless rows -> hig_review:1:<fid>, 29 ENC
    activated, 30 moved, 'Low'/'loq' normalised); tide_vectors.py review adds its 6 lines (new_low 24 km + new_source 12.6 km as vector_low at -1.75,
    2 vector_high at 3.73).  Backups of the pre-review files: $W/rocks_before_review_20261108.geojson, $W/tide_lines_before_review_20261108.geojson.
    (2) HOMER NCMP: homer2019ncmp / homer2021ncmp in the template (a 'homer2019' already existed: the DGGS topo flight, pt*.laz, whose class 2 is the
    WATER SURFACE over Mud Bay -- the first try reused that name and its stale index/units; renamed).  Levels on bishop_t's window: 2019 ground vs
    2018 +0.02 m (NMAD 0.12, >8 m), bed -0.06 (0.12); 2021 vs 2018 -0.04 (0.07): no shift.  bishop_t crop run h1921 first.  (3) WEST COLUMN: no land
    rim -> ext NaN -> 0 km2 of sea (r5c0); and _basin_prior read 0 past ~2 km from casts -> clamped to the base contour (r3c0's corner, the flats):
    a 16x coarser cascade + nearest.  (4) r6c3/r6c4 solid-over-level: a mapped channel at 59.63708,-151.15933 routed the 10.39 m of a 0.1 ha
    lake the rim rule raised (cm16: 19.06 from elsewhere) over 15-22 m ground; shore.py now returns routed cells > 1 m under measured ground to land.
    Not traced: r6c3's 1,446 cells at 59.69220,-151.30644 (z 1.1 over level -0.2; same in cm16).  (5) PETROF: body 2 (lake, 286 ha) got 12.04 from
    the rim rule; fill's domain split dropped any level whose surveys are all priors, so the lake went with its outlet's river domain (channel
    model, 6 m bed, a pit per sounding); it now keeps a rim-rule level.  QUEUE ($W/run_1108.sh, after bishop_t; one worker; then
    final_publish_cm18_1108.sh): r5c0 r4c5 r3c1 r2c1 r3c0 r6c2 r6c3 r6c1 r6c4 r4c0 r6c0 r7c0 r7c1, tag cm18 (overwrites those out_cm18 archives).
- **Dev version:** `$W/republish_loop.sh` rebuilds `$W/kbay_joint_cm18/` (`mosaic_blend.py ... "cm18,cm17,cm16"`) and republishes the
  dev surface `kbay_joint_cm18` every two new boxes (or after 2 h), log `$W/republish_cm18.log`; a last time when the workers are
  gone. On the viewer meanwhile: `kbay_joint_cm16` (the difference base), `kbay_joint_cm17` and `kbay_joint_cm17_r4c1` (both to
  remove once `kbay_joint_cm18` is up and r4c2 has landed).
- **When it ends:** gate table for all 48 (`$W/<box>/gate.log`), the merged review files for the mosaic (run_bay_parallel's Python
  block, tag cm18), remove `shore_dump_cm17.pkl` (r2c2, r2c3, r4c1), Hig's review.

**Next, in order** (steps 1-3 done; step 4 is the queue above):
1. Check r6c2 and r6c3 for faults 1-3; keep them if clean.
2. Build fixes 1 and 2 and the `ground` edit kind; take Hig's polygon (or the draft) into `edits.geojson`.
3. Test on `kj_r2c2`, `kj_r2c3`, `kj_r4c1` under a new tag; gate against cm16 and the best earlier runs.
4. **Rerun only the boxes with an identified fault** (Hig, 2026-11-05): `kj_r2c2 kj_r2c1 kj_r2c3 kj_r3c5 kj_r4c1
   kj_r4c2` (+ r6c2/r6c3 if step 1 finds one). Then the 35 unrun boxes under the fixed rules, and the mosaic of all 48.
   The mosaic takes each box's `out_<tag>/<id>.tif` for one tag, so a mixed-tag mosaic (cm16 for kept boxes, the new
   tag for reruns) needs `mosaic_blend.py` to take a per-box tag, or the kept archives copied under the new tag.

**Housekeeping on 2026-11-05:** Hig deleted the Time Machine local snapshots (boot disk 16 -> 133 GB free). Local
copies of 46 public COGs, 106 pyramids, 19 overlays and 6 sp_slope pyramids were deleted earlier the same night after
md5 checks against R2 (five COGs kept for topobathy: kbay_2023_reclass_v2, homer_2019, grewingk_2021, seldovia_2019,
dixon_2023). Until Hig adds `http://127.0.0.1:8001` to the R2 CORS policy, dev `/lidar/` works only at
`localhost:8001`. Still open: the r4c2 perched-over-deep 12.92 ha; the 1310 unhonoured edit checks; methods docs;
the earlier shore items (2 casts > 3 m, cast dimples, 5.4 ha basin overshoot, creases at inward corners, the rough
river rule).

## Resume here (2026-10-29) — run 20j on dev; the branch committed; what the regressions taught

**State.** The dev viewer (`/lidar/?catalog=dev`) carries three Grewingk-box surfaces side by side, each with its
water and sea surfaces: `joint_v3` (run 20g), `joint_v3h` (run 20h2) and `joint_v3j` (run 20j, published under
Hig's one-time override of the gate). Every run's products and stage logs are archived in
`/Volumes/Nunatak/lidar_build/composite/joint_v3/out_run<tag>/` (19c, 20c, 20e, 20f, 20g, 20h, 20h2, 20i, 20j) and
the gate's stored results in `joint_v3/gate/<tag>.json` (pre-20j copies in `gate/pre20j/`). Hig's review of 20j:
"This is looking really good"; commit, deploy, a flag editor, and this retrospective were asked for 2026-10-29.
The review flow and the flat-water rule are in [WATER_REVIEW_PLAN.md](WATER_REVIEW_PLAN.md); the polygon/rock
tools in [WATER_POLYGONS_PLAN.md](WATER_POLYGONS_PLAN.md).

**Run 20j against the best earlier run** (`out_run20j/logs/gate.log`):

| row | 20j | best earlier | note |
|---|---|---|---|
| floating_level_mismatch_cells | 0 | 135 | ice takes its host water's level |
| clip_shelf_cells | 445 | 897 | the ceiling in the fit, the clip only a guard |
| face_shallow_frac | 0.006 | 0.156 | |
| flat_water_steps_cells | 1,346 | 1,855 | |
| face_sounding_misfit_p90_m | 3.22 | 3.25 | |
| perched_over_deep_ha | 0 | 0 | the sill test (33 bodies, 174.5 ha, now the sea) |
| solid_over_level_water_cells | **424** | 302 | **regressed**: 120 cells of intertidal ground (z 0.67) at 59.60181, −151.16786 labelled water at −1.02, because bodies 29/212/323 re-kinded tidal by the sill test got the tidal band; not fixed |
| every other row | held | | |

Review products for Hig (W3 in WATER_REVIEW_PLAN §9): `out/water_flags.geojson` (565 flags: edge_mismatch 287,
ceiling_bound 235, sill_fail 33, above_water_obs 9, ice_face 1) and `out/polygons_auto.geojson` (430 polygons, 113
agreeing with a mapped one). A local flag browser/editor was mocked up the same day: `topobathy/review_tool/`
(see "Next").

**Not deployed to production, on purpose, and why.** The code is committed and merged; the surface is not on
prod. Three things stand in the way, each Hig's call or the hosting stream's work: (1) the composite's lake bed
comes from `grewingk_sonar_2023`, which is **gated** (Katie Hughes' multibeam) — a public composite needs her
permission, else the survey goes to prod gated (droplet-served, signed-in only); (2) the prod catalogue builder
(`make_catalog.py`) knows nothing of the `surfaces` list that carries the water and sea pyramids, so the water
surface cannot be listed on prod until it does (dev's `dev_viewer_add.py` writes the entry by hand); (3) the droplet
had 4.6 GB free on 2026-10-29 and a gated copy is ~0.7 GB (bed 199 MB, slope 160 MB, water 12 MB, sea 2 MB of
pyramids plus the 343 MB COG).

### The run ladder, 20 → 20j, one line each

| run | what changed | outcome |
|---|---|---|
| 20 (fourth pass, 2026-10-26) | Hig's run-19 review as fuse/shore/fill rules (swaths calibrated, lagoon stands, towers, glacier seam, Emerald blind, low-tide river) | landed; reviewed |
| 20e | Emerald's water_blind loop, void-patch merge guarded, shore rule 5 (every unobserved cell inside a stamp), two sea-stand rules; stopped by swap filling the boot disk, resumed after a reboot | gains at the lagoon and a tide flat; **China Poot lost 40 % of its water** — an `obs_lidar` edit had dropped the `surf` rule |
| 20f | `surf` restored | China Poot back; **the lagoon and tide-flat gains reverted exactly to 20c** — they had been side effects of the dropped rule, not of the rules written for them |
| 20g | the two written as rules (enclosed patch is the surface only within σ_w of the level; a raised water body above tide_max is the sea with no level; bed below the level is never land, enforced once); **`gate.py` born** | passed; lagoon land 3,802 → 0, bed-labelled-land 12.1 k → 0, solid-over-level 1,134 → 302 |
| 20h | Hig's 20g review: the band at the face = a seam between two water stamps (make_ice closes it); the pools = the 2023 SfM's surface as body-less levels (rule 5 over polygons and stamps); 15 ha of levels below low tide (impossible levels cleared) | every target row improved; **face_bad 128 → 882**: a channel inside the lake's fill domain was lifted by the lake's spline and sat outside the bed-below-water hold |
| 20h2 | the hold covers channel cells | face_bad 0; `face_bed_p1_m` "regressed" −124 → −110 m — the row was 20e's model depth, not a measurement; retired for the misfit to the soundings |
| 20i | WATER_REVIEW_PLAN: the flat-water rule (sill test, floating ice), the ceiling in the fit, the decaying correction, flags, reviewed polygons | stopped on four rows: two were gate bugs (flat_steps ignored the sill overrides; clip_shelf counted shoreline, lens and sea), floating ice was cleared before routing (a no-op), and the face shelves were river-kinded bodies at the lake's level given domains of their own |
| 20j | floating ice levelled after routing from its host ring; a river body 90 % at one flat's level is that flat; the two gate rows refined | all rows at or better than the best run except solid_over_level (above) |

### What the regressions had in common

1. **One physical question, answered in many places.** "Is this return the water surface at some stand, or ground
   / bed?" is decided in `joint.py` (`surf`, `above`, the cross-check), in `water.py` (kinds, dry), in `shore.py`
   (tidal stands, enclosed patches, raised bodies, rule 5, the sill test) and in `fill.py` (the hold, the ceiling).
   Each place has its own threshold, several borrowed from another purpose (`sure_dz_m` doubling as "is the
   surface"). Changing one shifts the balance in another: the 20e/20f pair is the clean example — a rule dropped
   in the fuse was "fixed" downstream by two shore rules that only looked right because the fuse was wrong.
2. **Gains that were never attributed.** 20e's lagoon and tide flat were called fixed because the numbers moved, not
   because the rule written for them was seen to fire. The gate now compares against the best run, but attribution
   (the log line, or a smoke copy on identical inputs) is still a discipline, not a mechanism.
3. **Constraints applied after the fit.** The bed-below-water clip made the face's flat shelves; the shore band
   made intertidal ground water; a mapped level stamped as a surface made plateaus. Each was right as a statement
   and wrong as an edit. Moving the ceiling into the active set (20i) is the pattern: a constraint belongs in the
   solve, and a post-hoc clip should only be a guard that logs a flag when it fires.
4. **Remembered models as expectations.** `face_bed_p1_m` gated a later run against an earlier run's *model*.
   Replaced by a misfit to measurements; the rule is that a gate row measures an invariant or a measurement, never
   an earlier output.
5. **A domain that did not match the body.** The fill's domain (a connected wet region) and the water stage's body
   (a kind and a level) are two partitions of the same water; where they disagreed (a channel inside a lake domain;
   river-kinded bodies at the lake's level) each stage applied its rule to the wrong unit. The fix in 20j (a river
   body standing at a flat's level *is* that flat) is one more reconciliation; the general version is one partition.
6. **Process leaks.** Scripts sed-copied from the previous run (`water20j.sh` still writes to `water_i/`); a stage
   that reused the previous run's `out/` through `$out/` steps (bay v2, fixed in the chain script); logs overwritten
   per run until archiving became routine; a `dict(**int keys)` bug that cost a 3.5 h run. None of these is a
   science error, and together they cost more runs than the science did.
7. **Site-shaped by construction.** Every rule since run 19 was written against a site Hig named, proved on a crop
   or a smoke copy at that site, and gated by a window at that site. The wording is general; the tuning is not.
   The gate's site rows hold the *site*, which is not the same as holding the *rule*.

### What generalises — the shape for the next box

- **The invariants are the specification.** Hig's five (every source is observations; water is a level, not a
  height; a level is an inequality on the bed; a gap is filled within its domain; a trusted shoreline arbitrates)
  plus the flat-water rule (the sea has no level of its own; a lake one or more; only rivers slope; perched needs
  a sill; floating ice takes its host's level). A run is correct to the degree it is a clean expression of them.
  Each should be enforced **once**, at one stage, and every other stage should read the result rather than
  re-decide it — the direction `shore_bodies.json` → `load_bodies(overrides=True)` already takes.
- **Gate rows as invariants, sites as diagnostics.** The rows that will carry to any box count impossible things:
  bed above its level, floating cells at another level, perched water over deep bed without a sill, modelled
  shelves exactly at the clip, steps between flat-water cells, observed solid cells labelled water, measured bed
  labelled land, levels below the lowest tide. Those need no window and no history. The site rows (China Poot,
  the lagoon, the tide flat, the face, Emerald, the band) are Grewingk's regression sites: keep them, but as a
  per-box `sites` list, so the next box starts with the invariant rows and gains its own sites as Hig names them.
- **Human judgement as data, not as rules.** The W1–W4 flow turns a reviewed polygon and a disposition into a
  constraint the chain reads (`water/polygons.geojson`, `water/review.geojson`). Every site-specific thing we
  were tempted to encode as a rule (the face line, an island, an outdated polygon, a lake's level) has a home
  there. The flag editor is what makes that cheap enough to be the default.
- **One partition of the water.** Body (kind, level, sill, outlet) and fill domain should be the same object, split
  by the shore stage's levels once; the fill should take domains from it rather than re-deriving them.
- **Recipes carry the tuning, with meaning.** The thresholds that were tuned on this box — `tide_max_m`,
  `band_tidal_m`, `tide_stand_lo_m`, `sure_dz_m`, `sigma_w`, `hole_rim_tol_m`, `over_water_max_m`,
  `derived_adjust_m`, `channel_drop_m`, `points_decay_m` — are physical quantities (a tide range, a lidar's
  vertical noise, a correlation length). A new box should set them from its own tide table and surveys, not
  inherit Grewingk's numbers silently; the recipe should name the basis of each.
- **Attribution before credit.** A gain is counted only when the intended rule is seen to fire (its log line, or
  a label diff on a smoke copy). The gate reports the delta; the run notes name the rule for every improved row.
- **Runs are reproducible artefacts.** One generated run script per tag (not a sed copy), the recipe snapshot, the
  stage logs, the gate JSON and the review products archived together; the dev survey id is the tag.

### Next

1. **Flag review tool** (`topobathy/review_tool/`, local, mask-tool pattern): browse the run's flags by kind over
   the dev surface, set dispositions, redraw polygons, write `water/review.geojson` and `water/polygons.geojson`.
   Then Hig's W3 pass on 20j and a W4 run (start at the water stage if polygons changed, build 2 if only
   dispositions).
2. **The solid_over_level patch**: intertidal ground beside a body the sill test re-kinded tidal must not take the
   tidal band when it is observed ground above the day's water — the same enclosed-patch rule (20g) applied to
   re-kinded bodies.
3. **Multiple levels per lake** (the joint layer must keep per-survey water levels); **mapped rivers as priors**;
   **demoting outdated mapped polygons** by their `agree` score.
4. **Deploy the data**: Hig's decision on gating (permission for the multibeam, or gated on the droplet);
   `make_catalog.py` to read `surfaces`; droplet disk.
5. **The second box** with the same recipe and only the invariant gate rows: the real test of the shape above.

## Resume here (2026-10-23, early) — run 7 reviewed; run 8 (fill split) and the v3 joint stage in hand

Run 7 (`joint_v2`, the first full-box point-first build) finished 01:50 and Hig reviewed it the same night.
**Hig's framing, which is now the rule:** a data gap is NOT water by default. Three kinds of gap need telling
apart — a water body the laser could not see (water returns, a level rim, a mapped polygon), a gap BETWEEN
surveys (the lidar's edge and the multibeam's, or a lidar seam) that must be interpolated across, and ground no
good survey reached (3DEP or the SfM stands in). Run 7 treated every no-return void inside a footprint as a
water hole (`joint.py layer`) and the water stage's kind decision fell through to `lake`, so glacier voids
(59.58648,-150.96854; 59.59525,-150.97584) and slope voids (59.62424,-151.02764; 59.65352,-151.09961;
59.61827,-151.00938) became lakes with basins. The tide flat was dug 5-15 m deep for a different reason: with the
river correctly water, Grewingk Lake, the river and the flat became one wet domain of 7.95 km2 in the fill.

**Hig's direction for rivers (2026-10-23):** the overly deep river is acceptable as an intermediate if a second,
flow-aware step imposes hydraulically reasonable channel bathymetry — keep the channel outline and the sloping
water surface the point cloud gives (smooth and continuous in run 7), set the depth from them. The fill's channel
model (constant section, wide = shallow) is the placeholder; a resistance law on the observed surface slope
(Manning: depth ~ (Q n / (w sqrt S))^0.6, one discharge per river, anchored where soundings touch the channel) is
the planned replacement. Not built.

### Built 2026-10-23 (uncommitted; `topobathy_scratch_20261021/{fill_before_split,joint_run7}.py` are the befores)

- **fill.py `split_by_level`** — a wet domain whose observed water drops > `channel_drop_m` (1 m) is split where
  the surface slopes (> `channel_slope`, pieces > `channel_min_m2`): sloping pieces are rivers (the channel model),
  flat pieces their own domains. Two guards found on the test crop: a sloping piece is a river only if the water
  really drops across it (p98-p2 > 1 m), and only if the flat pieces it touches stand at different levels — noise
  over a lake (bergs, two surveys' levels) had cut Grewingk Lake into five pieces, each given a basin. **A domain
  takes its water body's level and depth only when its own water stands at that level** (± 1 m); a piece split
  off the body keeps its own (a river pool 28 m under the lake was held at the lake's level), judged on its p25-p75
  (a prior's low hydroflattened cells pulled a median). Inside a flat body the split reads the water stage's judged
  level, not the raw surface (3DEP's 41.3 m strip in Grewingk Lake at 48.9 cut the lake in two). Test crop
  (`/Volumes/Powder/lidar_build/fill_test_rm`, 10 x 8.5 km around the river mouth): mesa 1 -9.2 → -0.95, mesa 2
  -4.3 → -0.94, terrace 1 -15.0 → -1.57, river mouth -4.3 → -0.10, tidal transect 17 → 6 reversals, lake centre
  (multibeam) unchanged. Run 8 = run 7's composite + this fill (`/Volumes/Powder/lidar_build/joint_v2/run8.sh`).
- **joint.py**, four general rules for run 9 (`composites/joint_v3/recipe.json`, out `Powder/joint_v3`):
  1. *Ground at the cell's own water-return level, outnumbered by water returns, is the water surface* — KBay
     2023's 0-1 "ground" returns/m2 at +0.81 among water returns at +0.80 superseded the 2009 multibeam and
     stood 52 m above the sea bed (59.56454,-151.20068).
  2. *Grid sources are averaged into the working grid when at or finer than it, bilinear when coarser* — nearest
     picked every other column of the 1 m multibeam and let its sprinkled no-data toggle the pool cell by cell,
     and made NCEI's 10 m cells a staircase: the "gridding artifacts" (59.57435,-151.19127; 59.59794,-151.19744;
     59.61277,-151.21687). Measured: the OCM original is smooth there (Laplacian sd 0.00-0.02), the joint 10-30x
     rougher, the OCM grid aligned to ours — so ours, not Hydropalooza's.
  3. *A grid cell counts `grid_wc` = 0.1 toward the surface that rejects a source* (a lidar cell counts its
     returns, to 5): a product alone or two cannot reject a survey with return support (a 2019 product's -4.6 m
     had rejected 6-14 lidar returns/m2 at -1.7 on the tide flat, 59.61894,-151.19526), and two products that
     disagree cannot annihilate each other and leave the worst third (OCM and the 2009 multibeam did, cell by cell,
     and NCEI showed as a moire).
  4. *A no-return hole is a water hole only when its rim is level* (p90 - p10 of its ring's ground ≤
     `hole_rim_tol_m` 1 m) or it touches water. Glacier and slope voids stay void: the fallback shows there
     and the fill's dry-void handling applies.
- **The SfM over the whole box.** Run 7's `sfm2023bare` and `dixon2023` were the Grewingk pilot's products, cut
  to its 12 x 7 km (8.7 km2 of a 470 km2 box); the Dixon DSM is valid at every false-lake site. Rebuilt over the
  joint box: `dsm_bare.py` against 3DEP, levelled to KBay 2023 reclass v2, the NSIDC 2020 glacier outline as the
  exclusion; ice = the DSM inside that outline (`Powder/joint_v3/obs/`). Hig: the SfM is competitive with lidar
  in the open and beats 3DEP wherever that is all there is (except perhaps under tall forest IfSAR sees through).
  The sigma ordering (lidar 0.05-0.08, SfM 1.0) keeps it under the lidar; beyond it, it is the surface.

### joint_v3 stage, probed 06:40 (fuse + layer only; the chain is run 9)

| Hig's site (run 7 reading) | joint_v3 |
|---|---|
| glacier voids 59.58648,-150.96854 / 59.59525,-150.97584 (lakes at 484 / 391 m) | Dixon ice 504 / 421 m |
| slope voids 59.62424,-151.02764 / 59.65352,-151.09961 / 59.61827,-151.00938 (lakes) | void (fallback shows) |
| multibeam 59.57435,-151.19127 (NCEI staircase) | NCEI bilinear, Laplacian sd 0.068 → 0.032 |
| multibeam 59.59794,-151.19744 / 59.61277,-151.21687 (OCM and 2009 annihilated, NCEI moire) | three beds pooled, sd 0.27 → 0.09 and 0.67 → 0.01 |
| 59.56454,-151.20068 (+0.81 m lidar spike over −52 m water) | water-surface ground dropped; beds pooled −53.7 |
| 59.61894,-151.19526 (lidar tide flat −1.7 rejected by the 2019 product's −4.6) | lidar kept, 44 returns, −1.68 |
| tide-flat / river holes (fill stage) | unchanged here; the fill split handles them (crop test) |

Coverage: supported 0.545 → 0.777 of the box; SfM 8.7 → 52.2 km2 beyond the lidar; ice 85 km2; ground 161 → 195
km2; water holes 3.4 → 2.3 km2. Rejections: bathy2009 and OCM 0.0 km2 high (were 0.04 / 2.0), the lidar pair as
before (kbay high 0.90, gk2021 0.54: the vegetation cases). Open from the stage: Dixon ice rejected high 1.45 km2
(against what — probably KBay 2023 ground at the ice margin, 0.14 yr apart; look at run 9's margin).

### Open at the 07:30 pause (2026-10-23): the lake still splits into pieces at its own level

Crop test, last pass (07:17): every tide-flat and river-mouth site right (mesa 1 -0.82, mesa 2 -0.94, terrace 1
+0.26, river mouth +0.45, transect 6 reversals), but Grewingk Lake comes out as three domains at its level (2.84,
0.71, 0.34 km2 at 48.91 / 49.07 m), each with its own basin -- a step in the modelled bed where no multibeam
exists. Four guards did not close it (real drop, equal-level neighbours, judged body levels, body-less cells
ignored). Next thing to look at: WHICH sloping piece separates them (dump `fill_lakes.json` ids 1 and 29210 and
the river labels between; water_z there; the bodies' kinds) -- a kept river piece must have a drop > 1 m and
flat neighbours at different levels, so one of its neighbours is a flat piece NOT at the lake level: find it.
Run 9 (`/Volumes/Powder/lidar_build/joint_v3/run9.sh`, ~2.5 h, joint stage already done) is worth running as it
is -- everything else in Hig's list is addressed in the stage -- and the lake pieces are the known open item.

### Run 9 (`joint_v3`, published 12:24) and what it taught

Every site on both lists right except ONE new defect: pit 1 (59.61742, the lake's NW end by the outlet) at 21.85 m
in a lake at 48.9 -- the slope-based split (the fill.py of 10:44) cut the outlet reach off as its own domain at
its own median level (22 m) and gave it a basin. The crop test had shown the same (a 0.26 km2 "lake" at 42.9 with
a 57 m basin at the river mouth). **Replaced by the body-based split** (`split_by_level`, noon): a wet domain is
divided by the WATER STAGE'S bodies -- flat bodies within 1 m are one domain whatever divides them (Grewingk Lake
4.03 km2 whole in the crop, one basin), a small flat above the lake takes its level (berg tops), a small flat below
it is a pool of the river, and the river (sloped bodies, pools, body-less water off the lake's level, closed across
gaps, specks < channel_min_m2 folded back) is a RIVER domain: channel_beds `whole_ids` models all of it, it never
takes a body's level or a basin. Crop: lake whole, river mouth +0.4, mesa 1 -0.85, terrace 1 -0.14, channels 12
over 0.57 km2 (were 1 over 0.07). Run 9 probes (vs run 7): glacier voids ice (504/421), slope voids void and back
to 3DEP (760/498/872), multibeam smooth (Laplacian sd 0.65 -> 0.01), spike gone (-53.7), tide-flat lidar kept
(-1.68), the seven pimples 0.7-1.5 m (were -16 to -30), tidal transect 7 reversals, seam 1.42 m.

**The OCM 2023 product is the odd one out** (Hig suspected it): at 59.59794 it reads -50.2 against the 2009
multibeam's -58.1 and NCEI's -54.6; at 59.61277 -45.2 against -40.5 / -41.6. With grids unable to reject each
other the pool followed the multibeam (weight 3.1 vs 0.86) but still moved 1.7 m. Rule added (`grid_better` 0.75):
a clearly better product (sigma <= 0.75 x) counts as a full cell against a grid source, so the multibeam rejects
OCM where it stands off by more than the tolerance (~2.6 m) and OCM cannot reject the multibeam.

Two last rules in the split, found on the crop (13:04 pass: lake 3.81 km2 whole, river mouth 0.15 under its
1.07 water, pit 1 48.81, pools under their surfaces, 9 channels over 0.82 km2): **a prior's water level is no
evidence** (composite.py hands the fill `_water_prior`, the cells a fallback layer won; 3DEP's 41.3 m strip had
read as a drop inside the lake), and **a flat body's own low tail is river** (the water stage's p10-p90 test reads a
body that is 90 % lake and 10 % outlet as flat at the lake's level -- the river mouth 3 km away was in it; water
observed more than 1 m BELOW the body's level goes to the river; above it -- berg tops classed water -- stays with
the lake). `fill_water_level.tif` (new) is the level the fill used per wet cell: the water surface DEM's input.

Run 10 = the joint `grid_better` rule + the full chain with the body-based split + the water surface (`run10.sh`,
launched 12:27; its fill loaded the final fill.py).

### Run 10 (`joint_v3`, published 14:45 with the water surface) and run 11

All 23 sites right but two, both in Grewingk Lake's outlet bay (`scratchpad/rv/cmp9.py`, r9 column = run 10):
pit 1 48.81 whole-lake level, pit 2 48.11, mesa 1 -0.81, mesa 2 -0.95, terrace 1 +0.27, river mouth -2.80
(the channel cut through the beach face, the tide flat itself smooth -- run 7's gouge along the beach is gone),
glacier voids ice (504/421), slope voids 3DEP (760/498/872), multibeam Laplacian 0.02-0.16, pimples 0.66-0.80 /
20.68 / -1.68 / -53.65 / 1.52, tidal transect 7 reversals. Lakes: Grewingk 4.45 km2 at 48.91 (one domain, Dmax
150 from the edit), China Poot 0.39 km2 at 50.46 (Hig's mapped polygon, Riordan's soundings). The chain took
2 h 17 min, the fill 1 h 41 min.

**Defect 1, the seam transect's 7.75 m step** (y 6609500, x 606300-606750, 59.610 N): the outlet bay has the 2023
sonar's bed at 25-39 m under the lake's 48.9 m surface. The joint layer classes those cells BED (3), but the
composite's class steps for the joint layer then paint `lake_2021.tif` (the 2021 lake outline) as water over
them, so `class_joint.tif` read water at z = bed; the water stage (`water.py`, `cls == water`) made dozens of
"lakes" at 19.65, 22.1, 23.6, 24.9, 26.2, 27.5, 29.6, 34.7, 38.7, 39.9, 42.6, 42.9, 44.4, 45.0 m inside Grewingk
Lake (every one "mapped Grewingk Lake"), and the body-based split sent each (a small flat more than 1 m below
the dominant) to the river model: `fill_water_level` 35 m in the lake, the channel bed 1-3 m above the sonar,
a 7.75 m step where a river piece met the lake's basin. Run 7 had the same bodies but no split, so nothing showed.
**Fix (axes.class_raster, general):** a `raster`/`polygons` step mapping to WATER never recolours a measured BED
(`"over_bed": true` overrides) -- a water mask says where the surface is water, the bed under it is still bed.

**Defect 2, pit 1's roughness 1.27:** 93 cells at 41.3 m in the lake's NW arm -- a body whose only level is
3DEP's hydroflattening (41.34, no survey water return there, the joint void) -- a small flat 7.6 m below the
lake, so a pool, so a river domain, in the arm. **Fix (fill.split_by_level + composite `_prior_layers`,
general):** a body whose levels all come from prior layers has NO level in the split (it goes with the dominant
flat around it), and the dominant flat is chosen among the LEVELLED groups.

Also found: `tools/lidar/water/mapped_hig_lakes.geojson` IS Hig's Lakes shapefile, converted 2026-10-15
(`water/sources.json`, `hig_lakes`: 1641 lakes, 1403 islands, 74 rivers) -- the water stage already uses it
(`mapped_name`, `mapped_frac` per body), so the gap discriminant's mapped-water prior needs no new access.

Run 11 (`run11.sh`, 15:09) ran the chain with both fixes and was stopped in its fill at 15:53: the crop test
(`/Volumes/Powder/lidar_build/fill_test_v3/`, run 10's `out/` cropped `-srcwin 1250 2500 5000 4250`,
`composite.py fill recipe.json`) showed pit 1 unchanged, because the 41.3 m patches are not split pieces but
two hole domains of their own, each under a 3DEP-only "ephemeral" body, and **the build's `water_z.tif` carried
3DEP's 41.34 m at every cell where no survey saw water** (the fold takes the newest water-classed layer's z,
prior included, whoever won the cell), so the fill's own-water test agreed with the body. Three rules make
"a prior's water level is no evidence" hold everywhere: (1) `composite.py` fold: a fallback/_prior layer never
writes `water_z`; (2) `fill_holes`: `wz_s` (water_z less the prior-won cells) is what every own-water judgement
reads (the split, the bodies loop, the lake level); (3) bodies loop: a body whose levels all come from prior
layers takes the median of SURVEY water within 3 cells of its domain when there are >= 20 such cells (the lake
around it), else its prior level stands (a lake only 3DEP saw). Run 12 (`run12.sh`, 15:54) was stopped too: the crop
showed the two patches are holes ringed by lidar GROUND (a berg or bar) inside a lake arm that is itself a
no-return hole, so no survey water lies within 3 cells and rule 3 cannot fire. The right place is the water
stage: **one mapped lake, one level** (`water.py`, after the mapped-polygon match): a flat body inside a mapped
lake polygon whose levels all come from prior layers takes the consensus level of the polygon's largest
surveyed body (`consensus_rule` says so, a note records the old level); a lake only the prior saw keeps its
level; edited bodies are left alone. Run 13 (`run13.sh`, 16:18): water -> build 2 -> fill -> build 3 -> publish
"run 13" -> water surface. Stopped 16:45: its water stage still read "joint" water at 29-45 m in the bay,
and the one-lake rule never fired. Two causes. (a) **`fallback` is a CLASS STEP, not a layer key**: composite
(`pri`, the fold's water_z skip) and water.py tested `spec.get("fallback")`, so no prior was ever recognised --
`axes.is_prior(spec)` now reads the class steps, and all three use it. (b) **the joint layer's void placeholder**
(`layer_z` at a void cell is `nearest_fill`) and **a hole's rim** were taken from any finite cell, bed included:
no-return holes between the sonar tracks got z = the bed (29.5 m), the 2021 lake mask then called them water, and
the water stage read bodies at the bed's height. Both now exclude bed (`CJ != bed` in the rim; placeholder from the
nearest SURFACE cell). The water-stage crop test (`composite.py water` on `fill_test_v3`) is NOT valid: the stage
reads the cropped class rasters against the recipe's full grid. Run 14 (`run14.sh`, 16:46): `joint.py layer` ->
the chain -> publish "run 14" -> water surface. Build 1 crashed at 16:50 (`NameError: axes` in `composite._tile_job`;
the import was function-local) and the chain restarted at 16:51 (`run14b.sh`). Its water stage (17:01) was right:
389 bodies, 13 prior-levelled bodies relevelled (pit 1's two to 48.91), the sonar-bed "lakes" gone. **Its fill
(17:04-18:51) wrote every raster and then died dumping `fill_params`** (my `_water_prior` ndarray was not in the pop
list; fixed); `run14c.sh` finished the tail by hand (COG + `fill.json` from the raw grid), ran build 3, published
"run 14" (18:58, z -235..1640 m) and the water surface. Run 14 is in dev, and its fill log named three more general
defects, each visible in the product:

1. **A 1.2 ha pit to -254 m** at 608578, 6609034 (Grewingk Lake near the face). The pooled joint layer holds the
   lake at several stands (2021 and June 2023 water at 49.4-50.9 m beside August 2023's 48.91) and berg tops the
   2021 lake mask recoloured water (52-65 m), so the water stage kept ~60 bodies inside the mapped polygon at their
   own "surveyed" levels; the one-lake rule relevelled prior-only bodies. Body 18 (50.29 m, 5.9 ha) became its own
   domain, off the lake level, so the fill modelled a pool at the surface; 22 berg_grewingk_2017 lower bounds inside
   it (bed >= 130 m down) and **no boundary site** let the soundings spline drag it wholesale (`residual -133 m,
   correction -276..-115 m`). Two fixes: `water.py` -- one mapped lake, one level for EVERY flat body in the polygon
   (the largest surveyed body leads; edited bodies keep their own; `consensus_rule` says why: a prior's alone, or
   another stand / berg top / hole rim in a survey); `fill.py _spline_residual` -- a domain with no boundary site is
   unanchored and the spline is not applied (logged with the residual).
2. **Body 63, a 0.8 ha "lake" at 27.37 m** in the outlet bay, 21 m under the lake: `layer_class` is VOID there
   (`joint_cls` 0, `joint_n` 0) and `layer_z` the void placeholder (27.35, the nearest surface), but the recipe's
   `lake_2021.tif` mask step recoloured the void water, so the layer "saw" water at the placeholder's height.
   `axes.py`: a mask step (raster or polygons) never recolours a void cell -- a void has no height to be anyone's
   level (the bed guard stays beside it).
3. The lower bounds the fill still violates by > 1 m (29 after the pass) are the same sonar lines near the face;
   folding body 18 into the lake puts them in domain 480's spline, which has 3,529 boundary sites.

Also seen, not explained: `tidal lens: bed held at or below the water surface on 112 ha (up to 307 m above)`.
In the finished rasters no tidal-lens cell (`fill_mask` 7) has `fill_prior` more than 30 m above
`fill_water_level`, so whatever stood 307 m above the sea was pinned before the rasters were written; where it
came from (the soundings spline, or a rim) is worth a look if the line repeats in run 15.
Run 15 (`run15.sh`, 19:01): the full chain. Its water stage (19:05-19:14) was right -- "one mapped lake, one level:
142 bodies take their lake's surveyed level (10 were a prior's alone)", all 124 Grewingk bodies at 48.91, body 63
gone -- and its fill **died at 19:45 on a `NameError` in the new spline guard** (`X` undefined: the guard's insertion
had displaced the line building the site arrays; restored). Nothing published; run 14 stayed in dev.

**Hig's review of run 14 (2026-10-23 evening), each item probed in run 14's rasters** (`rv/probe_hig.py`,
`probe_lake14.out` in the session scratchpad) **and fixed as a general case for run 16:**

1. **"Big negative and positive sharp-bounded features in the upper lake -- not just pits, also pillars."** The
   pits near the face are the unanchored-spline domain (fixed in 15) and, beside it, dropped patches; the pillars
   are their neighbours. Along the face the domain closure wrapped a ragged fringe of Dixon 2023 ice and SfM-bare
   "ground" (bergs, ice-cored debris, 50-94 m) into the lake; patch by patch the floating-ice rule judged some inside
   (filled to the basin, -60..-90 m) and kept the rest as surface -- a second survey layer corroborated ice-cored
   debris that persists for years, patches over 2 ha were kept by the size cap, and ice outside Hig's polygon was
   inside the closed domain. Several of them lie 10-230 m OUTSIDE Hig's shoreline (`probe_poly.py`). Fix: **a
   trusted mapped shoreline arbitrates** (`water/sources.json` `"edge": "trusted"` on `hig_lakes`;
   `fill._mapped_lake_sides`): inside a trusted polygon, raised land standing > `mapped_edge_dz_m` (1 m) above the
   water is floating or ice-cored unless a mapped island touches it (a second survey no longer corroborates, no size
   cap); outside it, within `mapped_edge_near_m` (200 m), raised land and ice are shore or glacier -- never floating,
   and cut from the domain so the face is one line at the polygon's edge (`ice_face: free` keeps it off the fill's
   boundary). Both the closure and the later floating rule read it. Hig edits the shoreline or adds an island to
   change the outcome.
2. **"A portion of the multibeam area (59.60878, -151.11323) is shown as dry ground -- the water DEM has a hole."**
   `fill_water_level` was written on the lake DOMAIN only (holes + judged observations); the sonar's measured bed
   inside body 4 had no level, so the water surface was NaN there (and the pad held the bed's own values at its
   edge). Fix: at the end of `fill_holes` every cell of a flat body with a consensus level gets the level where the
   fill set none (`water level written over the measured bed inside flat bodies: N ha`).
3. **"Complicated hills with even higher hills of water along the banks" (59.63076, -151.14389; 59.63679,
   -151.15907).** `fill_water_level` = 48.91 (the lake's level) in small patches on the creek's banks 5 km below the
   lake, with the bed dug 15-27 m under them (z14 -8 m beside banks at 18 m). In `split_by_level` a body-less cell
   took the nearest BODY's kind, and a flat body with no level took the dominant flat's group -- the lake's -- so a
   bank hole beside an unlevelled puddle became the lake's domain. Fix: decided cells are a river's, a tidal body's,
   a levelled flat's and water observed below its flat; every other cell of the domain follows the nearest decided
   cell's piece.
4. **"The ocean water surface is a step up from the river at 59.65211, -151.18874, and dots of elevated water at
   the same elevation (59.64802, -151.18506)."** The viewer draws the chosen tide over the SEA MASK where it stands
   above the inland surface (`ls_water.js levelTile`). `water14.sh` built the mask from tidal-kind bodies + the tidal
   lens + open water below MLLW, so the river's tidal reach (body 9, surveyed at 0.6-1.0 m) sat beside a sea raised
   to the tide, and each tidal pond on the flats (body 363 at 3.29 m) rose to the tide while the flat around it stayed
   dry. Fix: `water_surface.py --flood-to 4.8` grows the mask over every bed cell below 4.8 m (MHHW 4.24 + margin)
   8-connected to the sea, so the flats and the river mouth flood exactly where the tide stands above them
   (`water16.sh`).
5. **"Low-relief seams surrounding smooth-edged areas in the multibeam" (59.61674, -151.21919).** `joint_src` 80
   (bathy2009 + ncei2010) inside the patches, 112 (+ ocm2023) around them: OCM passed the cross-check in some
   cells and failed in others, so the pooled bed stepped 0.5-1 m at the patch edges. Fix: `joint.py` **`derived:
   true`** on a source (OCM 2023, NCEI 2010, CGOA in the recipe) -- a compilation built from the surveys it overlaps
   yields wherever a measured source of its class has cells within the cross-check reach, and holds only beyond
   them. The multibeam area is the multibeam alone; one seam at its boundary replaces the patchwork (OCM's 6-8 m
   offset against the multibeam is still to be reported and understood).
6. **"The transition from lake to river at 59.61928, -151.12379 is a step in the bottom."** Lake bed 46.9 m where
   the river's channel bed begins at 48.2 m under water at 47.9 m. Left for the flow-aware river step (Hig: minor,
   likely cleaned up there); a feather is the fallback.

**Crop test before run 16** (`composites/joint_v3/recipe_crop.json` = the recipe with `work_dir` on Powder;
`croptest/out/` = run 15's build-2 rasters cut to rows 4600-7000, cols 2900-6100 by `gdal_translate -srcwin`;
`composite.py fill` on it runs ~12 min; probes `rv/probe_crop.py`, `probe_crop2.py`): the multibeam carries the
lake's level (`water level written over the measured bed inside flat bodies: 41.71 ha`); both bank sites hold the
river's level and the layer's bed (no 48.91, no pits); no positive spike >= 10 cells in the lake; pit 1 is -70 m (the
basin). What still stood above the water inside the polygon is Hig's mapped ISLANDS (three patches at the outlet arm,
0 m from an island polygon -- kept on purpose) and shore 8-10 m outside the line. What remained sharp was ICE 40-170
m OUTSIDE the shoreline at the face: the lake has grown past the 2017-22 line, and small ice patches out there (bergs
by Dixon 2023, 0.03-0.16 ha components) were "glacier" under the outside rule, pillars among water filled to -30..
-100 m. Refinement: ice outside the shoreline is the glacier only where its connected component reaches beyond the
near-zone; an ice patch on its own within the zone floats (`ice outside the shoreline not joined to the glacier`).
The second crop run confirmed it (`ice outside the shoreline not joined to the glacier: 22 patches (0.28 ha)
float`; ice dropped inside the lake 330 -> 1038 cells; no positive spike; the remaining "negative spikes" are the
lake's deep water against the kept glacier -- the face as a cliff, which it is). Run 16 (`run16.sh`, launched 21:22): `joint.py all` (the derived flag changes the fuse, 16 min) -> the chain
(build 1 3 min, water 10 min, build 2 2.5 min, fill 114 min) -> build 3 -> publish "run 16" -> `water16.sh`
(`--flood-to 4.8`).

**Run 16 landed 2026-10-24 00:01 and is in dev** (`/lidar/?catalog=dev`, "Joint stage v3, Grewingk box (run 16)",
water + sea surfaces, the five tide levels). **Build 3 failed at 23:47 for an outside reason**: at 23:17 macOS's
privacy daemon began refusing this terminal's removable-volume authorisation for Powder (`tccd` "Failed to create
Attribution Chain"; every `open`/`readdir` on `/Volumes/Powder` returns "Operation not permitted", `stat` still
works, Nunatak unaffected). Docker's file sharing still read it, so the build-3 inputs (`layer_z/class`,
`joint_nland`, `water_level`, `joint_src/cls/n/rej`, the scripts) were mirrored to
`/Volumes/Nunatak/lidar_build/composite/joint_v3/powder_mirror/`, the recipe copied with those paths
(`recipe_run16_mirror.json` beside it) and `run16b.sh` (build 3, publish, `water16b.sh`, work dirs on Nunatak)
finished the run at 00:01. Until Terminal is restarted or Powder re-plugged, anything that reads Powder from
this terminal (the fuse, the crop test, `run16.sh` itself) fails the same way; `docker run -v /Volumes/Powder:...`
is the workaround. The dev-viewer work dir for joint_v3 is now on Nunatak (847 MB; Nunatak 91 GB free).

**Verified on run 16's rasters** (`rv/probe_hig16.py`, `rv/cmp9.py`, `rv/poly16.py` in the session scratchpad):
- *Multibeam shown dry* (606470,6609356): bed 21.98 m, water level and water surface 48.91 over it. Fixed
  ("water level written over the measured bed inside flat bodies: 57.91 ha").
- *Hills of water on the banks* (604672,6611754 and 603798,6612401): ground 29.84 / 18.88 m above a water
  surface of 28.45 / 17.87 m; no water level higher than the ground anywhere in the 62 m window. Fixed.
- *Elevated dots on the flats* (602298,6613611): a 504 m2 perched pond at 3.29 m = its ground; the sea mask
  covers it, so it floods with the tide above 3.29 m. Fixed.
- *Ocean a step up from the river* (602078,6614061): the sea mask now covers the whole mouth (grown
  97.86 -> 106.22 km2 to the 4.8 m reach), so at any tide above the river's surface the viewer shows the
  tide; below it the river's own surface shows with a 0.3-0.5 m step where the river body (9) meets the
  tidal body (3) -- a flow-aware river surface (monotonic down to the tide) is what removes that step.
- *Multibeam seams* (600468,6610077): `joint_src` 16 (the 2009 multibeam) across the window, bed -66.9..-64.7
  over 62 m; the OCM yields. The `cmp9.py` seam transect's largest step moved from 1.5 to 4.2 m because it
  now sits at the multibeam's coverage edge, where the OCM (6-8 m off) alone remains -- expected, and the
  OCM/multibeam offset itself is still unexplained.
- *Pits and pillars in the upper lake*: positive spikes >= 10 cells 48 -> 5, negative 49 -> 14, and every
  one is now within 200 m of the polygon's NE (glacier) edge -- ice at 53-67 m beside a modelled bed at
  -46..-3 m under ice that floated (the "isolated ice outside the shoreline" rule, 22 patches, 0.28 ha).
  Raised land inside the lake polygon: every patch either touches one of Hig's island polygons (0.0 m) or is
  a +0.7-1.0 m shore sliver within 5 m of the polygon edge. The face zone is where the shoreline polygon
  decides; move the shoreline (sources.json edge_note) if the 2023 face is wrong there.
- *Lake -> river step* (605841,6610508): still a step at the outlet (water level 47.70-48.05 against the
  lake's 48.91); deferred to the flow-aware river step.
- `cmp9.py` 23 sites: all at run 14's values or better (pim1-3 ~0.7 m, pimL2 -53.7 m, glac1 503.9).
- Fill log: "trusted mapped shorelines: 1649 lake polygons", "3 raised patches inside judged in the lake
  (1.08 ha), 2 outside kept as shore/ice; 1301 small patches outside cut from the domain", both splines had
  boundary sites (2955 / 342) so the no-anchor guard did not fire; "lower bounds still violated by > 1 m after
  the pass: 26"; "tidal lens: bed held at or below the water surface on 113 ha (up to 537.79 m above)" -- that
  537 m is a void placeholder somewhere in the tidal lens, worth a look.
- Still owed to Hig: the OCM 2023 6-8 m offset from the multibeam (unexplained); Dixon ice rejected high
  1.45 km2 (unexamined).

### The shoreline stage (stage C, `topobathy/shore.py`, built 2026-10-24)

Hig, after run 16 (2026-10-24): "make the next run a cleaner expression of these foundational rules ... understand
how we are building on top of them rather than developing arbitrary patches." The five foundations as read back from
the code: (1) every source is observations with class, sigma, date and support, fused into one layer, the prior shape
only; (2) water is a level, not a height; (3) a level is an inequality on the bed; (4) a gap is filled within its
domain from its rim, its points and a prior's shape; (5) a trusted mapped shoreline arbitrates. The drift was in the
fill: seven rules, each a threshold, all answering one question -- when an observed height stands above a body's
level, is it bed, surface, something floating, or land -- plus rule 2 enforced in four places and no rule at all for
a river's level. Stage C answers that question once, between the water stage and the fill
(`composite.py shore`, in `topobathy_chain.sh` after build 2):

- **Level** (`shore_level.tif`): a flat body's consensus (one mapped lake, one level); a river's level a
  non-decreasing function of the geodesic distance down its channel (isotonic regression of its water returns),
  pinned to the water it enters and capped by the lake it leaves, smoothed along the channel (`river_smooth_m`);
  tidal water's observed surface that day; body-less water its own returns. Never a bed, a prior or a void.
- **Sure water / sure land / the band.** Sure water: a measured bed, a water return at the level inside a body, a
  solid return BELOW the level inside a body (the bed seen through shallow water), a hole inside a body. Sure land:
  a solid return `sure_dz_m` (1 m) above the nearest level or farther than the band from water; ice joined to the
  glacier (an ice component of `glacier_min_km2` or more, or of `glacier_touch_m2` touching the 2020 G10040 outline
  and not inside a trusted water polygon). The band: within `band_m` (40 m; `band_tidal_m` 100 m by tidal water)
  of the water's edge on both sides, the rest.
- **Two hypotheses per band cell** (costs in nats, capped at `cost_cap` 8): WATER predicts z = level
  (`sigma_w_m` 0.25); LAND predicts the surface continued from the nearest sure land at its own slope
  (z_rim - tan s d, `sigma_l_m` 0.3 + `sigma_l_per_m` 0.03 per metre out) and never below the level. A trusted
  mapped shoreline is a prior (`prior_cost` 1 nat, fading over `prior_edge_m` 6 m); the point class a weak hint
  (`class_cost` 0.5). An unobserved cell costs nothing either way; inside a body it leans to water.
- **One continuous line**: a graph cut (scipy maximum_flow) with one smoothness `smooth` (2 nats a label change),
  cheaper across a break in height (`break_m` 0.5): the line follows the bank; a thin embayment is cheap, a jigsaw
  dear. On the crop, 840 k band cells cut in 1.1 s.
- **Floating** (`shore_label.tif` 3): land the cut leaves enclosed by water (its patch never reaches past the band on
  the dry side), judged by the body's kind: corroborated by two surveys (`island_corr` of the patch, land_support) or
  a mapped island -> island; majority water-classed -> the surface at another stand; a proglacial body (ice contact
  or glacier within `glacier_near_m`) -> floating; the sea -> floating only if built; any other body -> island, or
  the surface under `island_min_m2`. Ice not joined to the glacier floats where bergs are expected, is land elsewhere.
  Unobserved cells enclosed by water, or inside a trusted water polygon, are water.
- **Bank slope** (`shore_slope.tif`, and per vertex in `shoreline.geojson`): the slope of the land behind each
  shoreline cell over `inland_m`.

What the fill does with it (`fill.py`, the `shore` branch, old rules kept for recipes without the stage): the
label cuts land and glacier out of the closed domain and keeps floating patches inside; wet domains include every
labelled water cell; the observed water surface it reads IS the stage's level (so the lake level, the channel surface
and the tidal lens all come from one raster); inside the water an observed cell labelled water or floating at or
above the level is the surface and no evidence, a prior's cell never evidence, a solid return below the level the bed;
the rim slope is the stage's bank slope; and the water-level raster the viewer gets is the stage's level over every
water cell -- flat per body, monotone down a river, no holes.

Crop test (2026-10-24, run 16's build-2 products, `composite/joint_v3_crop`): every flat body's water-level raster
flat to 5 cm; multibeam under the lake's level; banks land beside the river's level; the lake's spikes down to a few
cells at the face; raised land inside the lake = mapped islands and the shore slivers the cut assigned to land.

**Run 17 landed (2026-10-24 14:29, published as "(run 17)")** and its checks held where the crop had: every flat body's
water level flat, no holes, the multibeam under the lake, the banks land. What the full grid showed that the crop
could not -- each a place where the stage still took a NEAREST thing instead of a CONNECTED one, or trusted a class
where it should have asked for a level -- and what run 18 (launched 15:5x the same day) changes, all in the rules'
own terms:

- **A wet cell's level comes from the water it connects to, through water** (`_route_levels`: scipy dijkstra over
  the wet cells within `route_m` 60 m of the unlevelled ones, the source of each returned). Run 17 took the Euclidean
  nearest level, so a gap in the lake at the ice face took a pond on the moraine (a 13 m pillar in the water surface),
  and 7 ha of mapped lakes in the 3DEP-only country took a body kilometres away. A wet patch no level reaches stands
  at its prior where the prior is flat (`prior_flat_m` 1 m: a hydroflattened lake), else it is no water.
- **Body-less water returns are a level only where a patch of them is flat** (`nobody_flat_m` 0.5 m over
  `nobody_min_cells` 4; the median). Rough ones are returns off something standing there (wet ice, a bar, berg
  tops) and are judged as solid returns: 2,553 such patches (30 ha) on the crop had each been "water at its own
  height". A river fit rougher than `river_max_rms_m` 1 m is no water surface either (body 159 at the face: a
  12 m "drop" over 68 m, rms 3.4 m).
- **A water stamp is a ceiling** (`axes.py` raster step, `ceiling_m` 1 m within `ceiling_near_m` 200 m, on the
  2021-lake and 2023-retreat masks in the recipe): a hand mask that says "water here" is a later observation than a
  height an older survey pooled into the layer. The 2019 ice at 62 m inside the 2023 lake had become "water at 62 m"
  -- class water, height stale, its own level. Such cells are voided (no height, no class): a hole, which the water
  around it and the sonar under it fill. 30 ha at the face in the windowed test, Dixon 2023 ice included (the retreat
  mask is Hig's own observation that the glacier is gone there).
- **A small flat body standing above the water around it is no pond** (`berg_max_m2` 5 ha; its ring 60 % water
  `sure_dz_m` below its level): judged like an enclosed raised patch (proglacial -> floating, sea -> structure or
  land, else the surface at another stand), its level the water's. And the enclosed-patch judgement itself: a patch
  is enclosed when its ring is water (or void inside a body or a trusted polygon), anchored when it touches the
  glacier or the unsurveyed outside or exceeds `berg_max_m2` -- no longer "reaches 40 m from water", which anchored
  every berg wider than 80 m; proglacial is judged before the water-class speck rule.
- **The river meets the lake it leaves**: the isotonic fit is shifted to the lake's level at its head, by nothing at
  its mouth (one surface through the outlet; Grewingk Creek shifted -0.27 m, i.e. capped). A perched pool keeps its
  one level (the tidal smoothing skipped it before: 26 perched pools with steps up to 0.77 m).
- **The fill cuts every observed cell the stage labelled land from its wet domains, whatever class its return
  carried** (it cut land-classed cells only; water-classed returns at 67 m up the ice margin, labelled land, were dug
  to the sonar bed at -39 m).

Left for the fill: its own domain splitting by bodies/flats/rivers predates the stage and can disagree with the
level raster in places (50 cells at the face at 49.11 under a bed at 57.7); the clean form is domains = connected
wet cells split where the stage's level steps, rivers where it slopes. Checks for run 18: scratchpad `rv/regress17.py`
and `rv/regress18.py` (steps between adjacent flat-water cells, solid cells labelled water above their level, the
face, the outlet).

**Run 18 landed (2026-10-24 18:59, published as "(run 18)"; fill 2 h 16, chain 3 h 15).** The rules propagate: the
fill's water level equals the stage's level on every wet cell, no wet cell lacks a level, every flat body is flat, the
ice-face patch of run 17 is lake (surface 49.1, sonar bed -33 m), the lake's pits are down to one 18-cell blob. Hig's
review the same evening, each item traced to its cause in the rasters, and what run 19 changes (code 2026-10-24
~21:00; from the water stage onward via `composite/joint_v3/run19.sh`, the fuse and build 1 standing):

- **The ice face's east side (5.7 ha): the bed was 3DEP's 2012 ice at 110 m and the water surface stepped 48.9 ->
  65.1.** Two causes. The fill saw a PRIOR's cell inside the water as an observation (z finite = observed), so it
  was never a hole; and the void inside Hig's 2023 lake polygon was levelled from the nearest water through the void
  -- a marginal stream (body 127) at 65 m, not the lake. Run 19: a prior's cell inside the stage's water is a hole
  and no observation (fill `drop_`), and **a trusted polygon arbitrates the level too**: levelless water inside one
  takes the one level its flat bodies hold (`poly_level_cells` 100, `poly_level_tol_m` 0.1) before routing.
- **Water in implausible places** (59.62526/-151.07244, 59.64014/-151.09388, 59.57234/-151.05723): the fill gave dry
  void domains and class-water holes with no level the lake treatment (a "lake" at its rim, 512.5 m over ground at
  465-523) and wrote a level there, which `water_surface.py` drew; and a 31-cell speck of 3DEP hydroflattening on a
  hillside at 489 m was a body-less pool (4 flat cells suffice). Run 19: with the stage present, the fill's wet domains
  are the stage's water labels and nothing else, `lvl_` exists only on them (and a body's level is written only over
  its wet cells); an ISOLATED body-less pool -- no levelled water within `route_m` -- must have `nobody_isolated_cells`
  (100).
- **Emerald Lake (no lidar): shoreline lumpy, islands, 1.7 m deep.** The 2023 SfM (sfm2023bare, bit 128) reconstructs
  water as a noisy surface 1-3 m UNDER the 3DEP level (p5-p95 347.3-351.7 at 351.1, 48,000 cells, all classed ground):
  the stage read it as the shore and as 84 land patches 0.1-0.3 m above the level, the fill as the bed. Photogrammetry
  cannot observe a water surface or a bed, so run 19 adds **water-blind sources**: the joint layer declares
  `source_bits` (joint_src.tif, bit k = source k, names in joint_report.json) and `water_blind: ["sfm2023bare"]`;
  `shore.water_blind_mask` marks the cells that source alone won, and inside a body or a trusted polygon they are
  unobserved to the stage (z NaN) and holes to the fill (`_water_blind`). Dixon 2023 is NOT blind (the bergs are real).
  The Emerald hypsometry (Hig 2026-10-14: dmax 51.6 m, 21 quantiles) lived only in the Grewingk recipe's
  `fill.lake_dmax`; it is now the water edit `emerald_lake_hypsometry` (tools/lidar/water/edits.geojson, applied by
  location), so the fill's basin uses it.
- **Tiny islands in China Poot Lake** (59.54498/-151.21039): both lidars read the lake surface as ground 6 cm UNDER
  the level, land_n = 2 -> "corroborated" -> island. Run 19: an enclosed patch whose median height is not `sure_dz_m`
  above the water is the SURFACE, whoever saw it, judged before corroboration (mapped islands and void-ringed patches
  still land first).

The crop test (shore + fill, 2026-10-25 00:13) added three things: **a body-less flat pool BESIDE levelled water is that
water at another stand** and takes the level beside it (returns off Grewingk Lake at 49.3 against its 48.91 had stepped
0.4 m along the shore; only an isolated pool keeps its own median); **the fill's lake test reads the stage's label, not the
class axis** (`lake_water_frac` over the hole cells: a water-blind source's cells are classed ground, so Emerald's domain
had failed the test and got the plain harmonic surface from its SfM rim, 2.7 m ABOVE the water); and `shore.read_on_grid`
aligns the body raster to the composite by georeference (the crop test had sliced the box's corner out of a full-grid
water_bodies.tif). Crop result: Emerald a lake at 351.1 with the hypsometry basin (median depth 24 m, deepest 50), its
shoreline the polygon's, the islands gone; the face void at 48.91; Water A no level; the fill's level equals the stage's
on every wet cell; the only step left is between two body-less pools on the tide flat (the sea takes the tide levels).
Run 19 launched 2026-10-25 ~00:20 (`run19.sh`, from the water stage).

Also seen in run 18: the spline needed clamping on 12 ha of lake bed (a prior up to 200 m above the water: the same
prior-inside-water cause); body-less flat pools of the sea surface at several tide states (-2.8..-0.8 m) step against
each other in `fill_water_level` -- the viewer's sea takes the tide levels, so not visible, but the sea's level is the
remaining place where rule 2 is not one number. Checks: `rv/regress17.py`, `rv/regress18.py`, `rv/probe_crop19.py`
(Emerald, the face, Water A, steps), `rv/probe_sites19.py` (Hig's four sites).

**Run 19 landed (2026-10-25 02:20; water 11 min, shore 25, fill 1 h 22, build 3 3 min) and was verified overnight.** The
stage and the fill answer every item of Hig's review: Water A, B and C have no level and no water surface (A and B are
void inside the lidar's margin with 3DEP's hydroflattening specks, C a hillside); the China Poot islets are water at
50.46; Emerald is lake body 7 at 351.1 with the hypsometry basin (median depth 26 m, deepest 50), its 48,607 SfM cells
inside the polygon unobserved (79 ha of water-blind cells across the box), two land specks of 2 and 13 cells left
inside; the fill's level equals the stage's on every wet cell, no wet cell lacks one; 46 trusted polygons levelled
64 ha of levelless water; 2,231 flat body-less pools levelled, 20,965 rough patches not. **One defect, in build 3, not
the stages:** 28,000 cells of 3DEP's 2012 ice at 105-119 m stood as the lake bed at Grewingk's face, labelled water at
48.91, where the fill had modelled the bed at -60..+60 m. The prior's `fallback` class step yields inside the water
BODIES (`water_bodies.tif`) but the face cells are the stage's water beyond any body (unobserved cells inside the trusted
polygon), so the prior's ground stood and out-ranked the fill. The crop test had not run build 3. Fix (`axes.py`
fallback, 2026-10-25 ~03:20): the prior's ground is void wherever `shore_label.tif` (beside the bodies) is water or
floating -- the rule the fill already applies, carried to the composite. Tested by a full build 3 over the crop's stage
products (28,497 -> 162 raised cells at the face, the 162 the fill's own bed 2-12 m above the level on the lake's edge,
0.06 ha, open). Run 19b (`run19b.sh`: build 3, publish "(run 19)", water surface) landed 03:35: the prior gone from
the face, and under it **the fill's own bed 55-114 m above the lake's level on 6,237 cells (2.5 ha) of the face strip**
(fill_mask conditioned + channel, no body under them). Cause, by the rim probe (`rv/probe_face_rim.py`): the strip is
body-less water the stage levelled at the lake's 48.91 (routed through the water), but `split_by_level` sends a
body-less cell to the NEAREST decided body, and the nearest there is the marginal stream (body 127, a river at 65 m):
a river domain -- no lake level to hold the bed, no lake spline -- whose harmonic residual rose to the 2012 3DEP ice
labelled land/void beside it at 110-120 m. Fix (fill.py `split_by_level`, ~04:00): **the level decides first** -- a
body-less wet cell whose stage level is a flat group's level within 0.1 m (the stage's flatness; a routed level is the
body's to the cm, and a looser match would have pulled sea-surface pools on the tide flat into an ephemeral pool's
domain) joins that flat's domain; the rest go to the nearest evidence as before. Crop fill: 162 -> 98 raised cells at
the lake's edge, no regression (the crop's bodies never made the river-domain case, so the full fill is the test).
Run 19c (`run19c.sh`: fill, build 3, publish "(run 19)", water surface) launched ~04:10, ~1.5 h. Note for the crop
test: a build over `recipe_crop.json` is a FULL-grid build into the crop's out folder (its grid is the box); crop its
outputs back with `gdal_translate -srcwin 2900 4600 3200 2400` before the next crop fill.

Open after run 19: the fill's own bed above the level on a lake's edge (0.03-0.06 ha at the face in the crop; `lake
bed held below the water` covers the lake domain but these are interior cells of a fringe); body-less pools of the sea surface at several tide states step
against each other (not visible: the viewer's sea takes the tide levels); 684 joint ground cells (0.27 ha, tide flats)
labelled water 1-1.3 m above the level; Emerald's level is 3DEP's 2012 hydroflattening (sigma 3 m, nothing surveys its
surface); two land specks inside Emerald.

### Run 20 in hand (2026-10-25, midday) — Hig's review of run 19 while run 19c landed

Run 19c landed 10:56 (the fill's domain split fixed the face strip: 6,217 -> 845 bad cells, all in the marginal
stream). Hig reviewed run 19 the same morning; every item was probed in run 19c's rasters before coding, and every
fix is a general rule. **Fuse rerun included** (Powder readable again with the sandbox off; the run-16 fuse took
11 min). The products of run 19c are kept in `out_run19c/` for comparison.

- **Multibeam swaths not interpolated (59.56205, -151.25148).** A flat -3.0 m shelf between the passes was the OCM /
  NCEI compilation standing where the multibeam had no cell (bits 96, two sources agreeing on their own interpolation).
  First cut: the measured survey's presence CLOSED over `derived_gap_m` so the compilation yields in the gaps (kept,
  default 0 = off). **Hig's better idea (2026-10-25, built the same hour): calibrate the compilation to the measured
  surveys it interfingers with** -- where both have cells, measured - derived is a smooth field (normalised Gaussian
  over `derived_adjust_m` 150 m on a 4x coarse grid) that adjusts the compilation wherever it stands, and the residual
  spread about the field goes into its sigma; far from overlap the field settles to the global offset (`joint.py` step
  2b, `rep["derived_adjust"]`). Upper Kachemak Bay is OCM / NCEI between sparse multibeam lines, so this is the rule
  that matters there. Crop: OCM 1.2 m high against the 2009 multibeam (residual rms 1.3 m), NCEI 0.3 m (0.4 m); the
  -3.0 shelf cells 4,497 -> 63 and the gaps between the passes read near the passes' depth.
- **Halibut Cove Lagoon: spikes and an irregular surface (59.57247, -151.20033).** The lidar saw the lagoon at 1.0 and
  2.5 m on different lines; the water stage kinded every sheet "ephemeral" because the multibeam's bed 65 m under it
  counted as "dry" (`water.py`: now only ground or bed ABOVE the level is dry); the stage then judged the 2.5 m sheets
  ringed by 1.0 m water as raised patches in tidal water -> land -> holes with no level, filled from a lake-shaped
  prior at -105..-127 m with the lidar's +1 m returns as the rim. `shore.py`: a flat sheet of water returns standing
  above tidal water within the tide's range is the sea at another stand, at its own level.
- **The cove "seam" (59.60069, -151.17244) and the towers (59.60232, -151.18644).** Body-less water returns of the
  sea at 0.8-1.8 m beside a tidal body at 0.78 had no level, so they were "solid returns", sure land 1 m above the
  nearest level, and holes under the fill bridged from a 3.78 m prior to the -40 m multibeam. `shore.py` TIDAL STANDS:
  a water return within `band_tidal_m` of tidal water, between the lowest tidal level seen - `tide_stand_lo_m` (floored
  at low tide - 1.5 m: a tidal body's "level" can be a measured bed) and `tide_max_m`, is the surface at its own
  (smoothed) level and sure water. TRAP found on run 20's first pass: the build keeps no water return in its surface,
  so on these cells z is void and only the class says water -- the rule reads the water stage's `water_z`, never z
  (the first version read z and fired on nothing; a dry test against run 20's own products: 11,683 cells at the cove,
  9,863 in the lagoon). The towers were Grewingk 2019 "ground" at
  350-450 m at the shoreline (no spike test could see them: the others had only water there, and their shore ground
  within 10 m disabled the over-water test); the fill then raised every hole around them to that rim. Fuse: nothing
  stands `over_water_max_m` (30 m) above the others' water AND above their nearest ground within reach (a cliff's top
  is the others' ground). Crop: 1,788 -> 0 cells over 50 m.
- **The glacier seam (59.59751, -151.04613).** Exactly the 2020 NSIDC outline's edge: the fuse's Dixon ice raster on
  Powder (made 2026-10-23 over the whole box) lacked make_ice's "ice the outline missed" growth (2026-10-14, Grewingk
  grid only), so beyond the outline the 2021 lidar's "ground" stood alone on thinning ice (lidar - SfM 8-10 m within
  100 m of the outline, 81 % over 5 m) and cross-rejected the 2023 SfM cell by cell. Data: the two rasters merged
  (`obs/dixon_ice_2023.tif`, 0.35 km2 added; the pre-merge copy `*_pre1014.tif`). Rule: inside the glacier outline every
  source takes the ice epoch window (`joint.py`, `_glacier_raster` from the recipe's shore.glaciers). Crop: the seam
  gone, the 2023 ice 10 m under the 2021 surface. **Then the real find (run 20's first pass, 14:46): the whole-box
  Dixon ice raster on Powder (2026-10-23, no sidecar, generator unknown) stood a CONSTANT 10.7 m above the DSM
  (p10/p90 of the difference 10.45/10.85 over 85 km2), while the Oct-14 raster equals the DSM and the DSM agrees with the
  2021 lidar on bare ground (-1.0 m median) -- a datum slip, so the glacier had been 10.7 m too high in every run since
  run 7 ("Dixon rejected high 1.45 km2" against the 2021 lidar was this).** `obs/dixon_ice_2023.tif` rebuilt straight
  from `cog/dixon_2023.tif` over the merged extent (the shifted copy kept as `*_merged_shifted.tif`); the crop fuse
  then reads 146.0 -> 146.6 m across the outline edge. The 2021 lidar stands 10.9 m (median) above the 2023 ice on
  the lower glacier. OPEN: the whole-box Dixon generator should be a script again (make_ice's growth rule over the box,
  from the DSM, with a sidecar).
- **Emerald Lake still shallow in parts (59.62846, -151.06836).** The stage and the fill dropped the SfM's water-blind
  cells (run 19), but build 3 wrote the joint layer back over the fill's bed (source_index 5, class ground at
  349-352 m, fill_mask 5). `shore.py` now writes `water_blind.tif`; a `blind_water` class step (`axes.py`, path
  resolved in `composite.py`, added to the joint layer in both recipes) voids the layer there.
- **The low-tide river (Hig).** `_river_level`: a river entering tidal water, surveyed at that day's tide, has its
  tide-held reach (fit within `river_tide_tol_m` of the pin) rebuilt as a ramp from the first cell above it down to
  `low_tide_m` (the fill's MLLW) at the mouth, never above the fit; the viewer's tide floods it from below.
- **Face follow-ups.** `fill.py`: the modelled bed is held below the level in EVERY wet domain (the stage's per-cell
  level where no lake level), not only lakes -- the marginal stream's 845 cells. `shore.py`: a prior's cell inside a
  trusted polygon that the cut labelled land is water (rule 5 for the unobserved; 14 ha of 2012 prior inside Hig's
  polygons were labelled land, 325 cells of 2012 ice at the face).

**Run 20 landed in three passes (2026-10-25):** first pass 14:46 (the stands rule read the build's z, void on water
returns, and fired on nothing; Emerald right); second pass 19:15 after the Dixon datum fix and the water_z change
(stands fired on 25 ha; the face clean, 0 bad cells, no blob of bed above a level; swaths smooth, 0 spikes; the glacier
continuous; BUT `water_blind.tif` came out with 22 cells where the same products recomputed give 152,885, so build 3
wrote the SfM back over Emerald -- unexplained, not reproduced: run 20b's stage wrote 151,686); run 20b (shore -> fill
-> build 3 -> publish -> water, launched 19:17) adds: a water RETURN inside a body at the body's level is sure water
(the build keeps no height on water returns, so `obs` was false and 6,890 such cells at Halibut Cove went to the cut and
the shore's land). Verify run 20b with probe_sea19*.py, probe_towers.py, probe_glac_em.py, probe_face_rim.py,
probe_face19.py, regress17/18.py, probe_emerald.py, probe_sites19.py.

**Run 20b landed 21:22 and is verified (the one in the dev viewer as "(run 20)").** Face: 0 bad cells, no blob of bed above a
level; swaths: 0 spikes, the calibrated compilation between the passes; lagoon: water returns labelled land 4,626 ->
366, spikes > 3 m 991 -> 60, bed at the multibeam's -65 m; cove: 7,380 -> 160, spikes 460 -> 238; towers gone;
glacier continuous at the 2023 surface; Emerald modelled 9-48 m deep (SfM voided by the build); the low-tide river
ramps 757 m to MLLW; the three run-18 water sites dry, China Poot water at 50.46; regressions hold (no wet cell without
a level, fill level == stage level). Still at the cove: 3,553 fill cells labelled land with NO observation (voids
between the multibeam's edge and the shore; the cut gives them the shore's land, the fill bridges from the 3DEP prior
at 3.8 m to the multibeam at -21 m) -- beach or bed is undecidable without a return (the land hypothesis has a flat
form), so this is a design question for Hig: a void in the tidal band beyond where the shore's slope meets the level?
Also: `steps > 0.3 m between adjacent flat-water cells` rose 40k -> 123k with the tidal stands (sheets at their own
levels in the sea, where the viewer draws the tide); `observed solid cells labelled water > 1 m above the level` 684 ->
250 cells.

**Hig's second review of run 20 (2026-10-25 evening), all probed on run 20b, fixed as general rules, run 20 third pass
launched ~23:10 (fuse + chain, due ~02:15; viewer entry stays "(run 20)"):**
- *Face plateau (59.60123, -151.06493):* 2012 3DEP ice at 124-144 m on 3.8 ha where every 2023 source reads the lake (DSM
  51-57, water returns 49.3). The joint HAD Dixon's cells there; the retreat_2023 stamp's ceiling voided them (right) and
  the fallback then stood (wrong: `seen` counts only non-void cells, 2026-10-16's NCEI reason). Now a water stamp
  (recipe root `water_stamps`, and `stamps` on the fallback step) is mapped water of its own kind: the prior never
  stands inside one (`axes.py`), and the stage takes unobserved cells inside one as water, like a trusted polygon
  (`shore.py`: `inpoly`, the blind mask, the prior-in-polygon rule).
- *Ridge islands (59.59574, -151.16370; 59.60100, -151.16620) and the lagoon spike (59.57266, -151.20167):* the 2023 lidar's
  sea surface at 3.85 m (near high water) classed GROUND over the ridge, and two "ground" returns at 0.79 m in the
  lagoon: islands in the stage, the multibeam rejected low under them. `joint.py obs_lidar`: ground returns within
  `surf_near_m` (30 m) of the survey's own water returns and within over_water_tol_m of their height are its water
  surface (run 7's rule needed the cell's own water returns).
- *Ocean topography at low tide (59.59882, -151.16757):* the stands at 0.65-3.1 m drawn over MLLW because the viewer
  (ls_water.js, the other worktree) takes max(tide, surface) over the sea mask. `water_surface.py --keep --low-tide`:
  every sea-mask cell outside rivers, lakes, perched pools and channel cells (keep.tif, built in water20.sh) is
  floored to MLLW, so the sea reads as the chosen tide and only a river's low-tide surface rises above it.
- *The river extension "didn't take" and the basin cuts (59.64719, -151.17949):* the ramp WAS fitted (test: 1,600 cells
  at -1.5..0 m) but the stage then lost it -- the ramp put the level 1-5 m under the water returns, the cells fell
  into the band, the cut made them land, and `lev_out` dropped their levels (shore_level had 0 river cells between
  0 and 3 m). `shore.py`: a water return in a river body at or above its level, within the tide's range, is sure
  water. The cuts are the channel bed under the river's water-return holes in the 2021 lidar; with the river's
  surface kept inside the sea mask (keep) the water now draws over them.
- *Bumps in the 2023 lidar (59.62102, -151.19005):* 138 of 140 bumpy cells are 2012 3DEP specks in small lidar voids
  (0.1 ha, 1.3 m proud). The fallback step gets the joint layer's class raster as a `footprint` with `near_m` 60: a
  void within 60 m of the survey's cells is the fill's (dry holes under dry_max_km2 are interpolated), not the prior's.

**The third pass (launched 23:10) was killed at 00:45 in its fill:** the first own-water rule sat in `obs_lidar` and turned
the river banks' wet-ground returns into water returns, which bridged the river to the lake in the water stage, and the
merged body (356 ha) was kinded a lake at 48.91 m by the mapped polygon -- the river would have come out at the lake's
level. Rewritten in the fuse's cross-check, where the other sources' beds are known: (1) a BED is superseded only by a
newer bed (Grewingk 2019's "ground" had superseded the 2009 multibeam by date); (2) a lidar's ground at its own water
level nearby OVER A MEASURED BED is its water surface; (3) ground standing AT the others' water level over a measured
bed, with none of their dry ground near, is another survey's sea at the same stand (gk2019 at 3.85 m where KBay's water
read 0.73); (4) the dry-ground distance counts ground/ice sources only (a bed lies under every cell of the bay, and
with it counted the over-water test never fired where it mattered); (5) the cross-check runs TWICE -- a pre-pass finds
every source's over-water cells and takes their weight away, the real pass judges with them gone, so a bed is never
rejected low against a lidar's sea surface. Cove crop: ridge A bed cells 398 -> 13,017, bathy rejected low 0.228 ->
0.023 km2, lagoon spike gone, swaths and towers unchanged. Fourth pass launched ~02:05 (fuse + chain, due ~05:15).

Not changed, noted: the tidal lens is still the marine fill's prior (holes among measured beds interpolate through
the rim residuals; the lagoon's spikes should go with the stands fix, to be verified); 18 ha of 2012 prior inside
Hig's lake polygons where the prior is not flat stay "no water" (mapped lakes no survey saw); the viewer's tide
over the sea mask was not re-checked. Run 20: `run20.sh` (fuse -> mirror -> chain -> publish "(run 20)" -> water20.sh).

### To build next (Hig 2026-10-23, in order)

1. **The gap discriminant, with a water-polygon layer as an explicit output.** Priors: the 3DEP water polygons
   (already `water_3dep` in flat_water) and Hig's lakes (`~/Documents/Outreach/KBSP_Map/New Shapefiles/Lakes/Hig
   Lakes UTM5.shp` — macOS blocks this process from `~/Documents`; copy to Nunatak or grant access). Per void:
   water if (water returns at a level) or (level rim) or (inside a mapped polygon that the observations do not
   contradict); a seam between two surveys of different class (lidar edge beside multibeam edge) → interpolate
   across with the prior's shape, conditioned by both rims, no basin; else fallback. The decision per body
   (kind, level, why) written as GeoJSON the viewer can show and Hig can edit (`water/edits` pattern), read back
   as an override on the next run. The beach model (wave redistribution of sediment) Hig raised is deferred.
2. **The flow-aware river step** (above).
3. **Run 9** = the four joint rules + the SfM + the fill split, as `joint_v3`. Launch: `joint.py all
   composites/joint_v3/recipe.json`, then `topobathy_chain.sh` and the publish (as run8.sh, id joint_v3).

## Resume here (2026-10-22) — the joint observation stage is built; run 7 (`joint_v2`) waits for the drives

**Read first.** Hig (2026-10-22, after run 6): "Overall the run looks pretty good... build the code to support your
point-cloud based analytical skeleton and prepare for a test run." The skeleton and its code are below; the run is
`zsh tools/lidar/joint_chain.sh composites/joint_v2/recipe.json joint_v2` (needs both drives; ~2 h of units for the
whole box, then minutes of joint, then the usual ~4 h chain, then the dev viewer as `joint_v2`). Run 6 is in the dev
viewer as `joint_pilot_grewingk` "run 6"; its two new review sites are in the regression list below.

### The skeleton (what the review of 2026-10-22 settled)

The raster stack's founding fault is that it cannot tell a measured cell from an interpolated one, and every rule the
fill grew (floating ice, at_surface, "a measured bed is bed", 3DEP withdrawn where the fill judged surface) was a way of
recovering, after gridding, what the points knew: was there a return, off what, from which survey, how sure. Run 6's
three fixes answered one question -- is this cell evidence of the bed? -- in three files, because the joint pilot was
being dropped into the old 17-layer stack as one more raster and the stack's `class_now` still arbitrated between the
joint layer's "water" and the sonar's "bed". So:

**Point space is the evidence model; raster space is only for shapes.**
1. Every measurement is an observation with support: `(z, class, sigma, date, source)` on the working grid, and a cell
   with no observation is VOID. Lidar returns as they are (the pilot's units); a gridded product as one observation per
   source cell with a sigma for what gridding lost; sparse points binned. Classes: ground | bed | water surface | ice.
   Bed and ground are one surface family; a water surface is never bed.
2. The joint stage is the ONE place that decides what is evidence: vintage zones, alignment, the epoch rule (newest
   wins unless its contemporaries agree), the cross-check, the over-water rule, pooling by 1/sigma^2.
3. A regional DTM (3DEP) is a PRIOR, not evidence: the composite takes it only where the joint layer is void (the
   mountains, ground under forest) and the fill uses its slope to say whether a hole can be water.
4. Water bodies, fill and the water layer stay; the fill keeps its physics (body-kind priors, rim conditioning, sounding
   splines, channels, the tidal lens) and should lose its evidence-cleaning half, rule by rule, as the regression set
   allows (not done).
5. A new site is: point sources with datum fixes, gridded sources with their native cell size, a water-bodies source,
   the prior. No per-site class rules.

### Built (2026-10-22, offline, tested on two 2 km windows cropped to the internal disk before the drives left)

- **`topobathy/joint.py`** (538 lines): `fuse` + `layer` driven by a recipe `"joint"` block -- sources of kind
  `lidar` (the pilot's per-survey return grids), `grid` (sigma^2 = sigma_m^2 + (g*D/sqrt(12))^2 + (xy_sigma*g)^2 at the
  source's own resolution D; `valid_below_m` for a merged topobathy candidate whose bed holds only below MLLW) and
  `points`; a `prior`; `vintage` zones (raster mask, or the pilot's floodplain / beach derivations). Steps: vintage,
  alignment, epoch (`pool_years` 5: an older source yields wherever a newer one has cells within the cross-check reach),
  cross-check with a tolerance carrying slope and BOTH sigmas (so a fine survey is not rejected against coarse bathymetry
  it disagrees with by metres; lidar vs lidar ~ the pilot's 0.3 m + slope), over-water (plus: a bed above the water seen
  in its own cell), pooling. Outputs `joint_z/sigma/n/src(uint16 bits)/rej/dist/nland/cls`, `water_z`, `vintage`,
  `layer_z` + `layer_class` (codes ground 1 / water 2 / bed 3 / ice 4 / void 0; a cell with no return at all beside
  ground is a hole, not a gap -- pit 1's shore cells read 52.7 m ground before that, 49.2 m water after). Replaces
  `joint_fuse.py` for the chain (the pilot file stays as the record).
- **`joint_pilot.py`**: `JOINT_WIN=x0,y0,x1,y1` and `JOINT_OUT` from the environment, so the units stage extends to
  the whole box and the pilot's 80 finished units are copied in, not recomputed (km-aligned window).
- **`composites/joint_v2/recipe.json`**: the whole box (597.5-621 x 6602-6622 km, 2 m), joint sources = KBay 2023 +
  Grewingk 2021 + Grewingk 2019 returns, the multibeam, bathy 2009, H12146, OCM 2023, NCEI, CGOA, the SfM bare model,
  the Dixon ice; composite layers = ctx3dep (prior/fallback), Seldovia 2019, Kenai 2008, Homer 2018/2019 (raster lidar
  DTMs WITHOUT point clouds here: they keep their class rules until they are brought into the joint stage), joint, fill.
  Soundings stay with the fill (its per-survey offsets). `water.max_px` 12000 (2 m over the full box).
- **`joint_chain.sh`**: units -> units2019 -> mosaic -> joint all -> topobathy_chain.sh -> dev_viewer_add.
- **Three general fixes in the raster stages** (run 6's two new sites, Hig 2026-10-22):
  `water.py` -- a flat body (lake/ephemeral) within the tide's reach lying mostly inside the intertidal zone is TIDAL
  (body 62: a 2.9 ha pond at 2.99 m on the tide flat, kinded lake, took a basin prior and dug the "sharp-edged basin"
  at 59.64092,-151.20197 to -8 m); `flat_water.py` -- a hydroflattened patch's RIM (cells at its level) is the same
  water (3DEP shore cells at 41.3 m stood as ground at pit 1); `axes.py` fallback -- the prior's ground below a water
  surface observed within `water_near_m` (100) of it is void (`water_z.tif`; the ocean mesa at 59.65615,-151.18643 lay
  outside the intertidal zone: 3DEP at +0.3 m, water seen at +0.6 m forty metres away). Backups of the four modules in
  `~/Claude_projects/topobathy_scratch_20261021/`.

### Regression sites (probe every run: `scratchpad/rv/probe.py`, or the window crops' `recipe.json` for joint.py alone)

| site | lat, lon | what it tests | run 5 | run 6 |
|---|---|---|---|---|
| pit 1 | 59.61742,-151.12098 | 3DEP hydroflat rim in the lake shore | 41.3 (pit) | 44.9 + a diagonal of 41.3 cells |
| pit 2 | 59.61914,-151.10468 | same | 41.8 (pit) | 46.9 fixed |
| mesa 1 | 59.65427,-151.18952 | 3DEP plateau on the tide flat, inside the zone | +2.0 | -2.4 fixed |
| mesa 2 | 59.65615,-151.18643 | 3DEP plateau outside the zone | +0.37 | +0.37 (the under-water rule) |
| terrace 1 | 59.65285,-151.19058 | Grewingk 2021 ground at -3.5 m on the flat | -3.5 | -3.5 (unjudged) |
| terrace 2 | 59.67175,-151.12703 | OCM bed step | -2.0 | -2.0 (unjudged) |
| tidal transect | 59.66659,-151.15346 -> 59.66234,-151.15394 | the weld: must rise monotonically | dips to -6.2 | -5.7 -> +1.0 smooth |
| sonar seam | y 6609500, x 606300-606750 | multibeam joined to the lake bed | steps 2-6 m | smooth |
| basin | 59.64092,-151.20197 | tidal pond kinded lake | -- | -8.3 (the water.py fix) |
| river mouth | 59.65198,-151.18729 | 2021 DTM pimples over the channel (outside the pilot window) | -- | -1.2 (joint v2 covers it) |
| largest lake domain | fill log | no welding: Grewingk ~4.9 km2 | 7.09 | 4.96 |

### Open, in order
1. Run 7 and its review. 2. Soundings into the joint stage with their per-survey offsets (then the fill reads them as
measured bed cells, not sparse points). 3. The raster lidar DTMs (Seldovia, Kenai, Homer) as `grid` sources with the
facets test as a sigma modifier, and their point clouds where they exist. 4. Strip the fill's evidence rules against
the regression table. 5. Memory: the joint stage at 1 m over the full box needs ~4x the 2 m run (68 GB on this Mac;
2 m is the composite's own resolution, so nothing is lost today).

## Resume here (2026-10-14, evening) — Grewingk at v6, creek next

**2026-10-16, small hours (read first):** Hig's v6 review of the patches produced THE TIDAL LENS
(TOPOBATHY_REVIEW.md 8.4; the stage B section below): water the tide reaches is a flat at its observed
surface until measured -- `fill.py tidal_lens`, `water.py` kinds + `intertidal.tif`, `below_level
"intertidal": "void"` on the OCM/NCEI priors, the pool edit withdrawn. Patches in the dev viewer as
`*_topobathy_v7` (awaiting Hig). Grewingk's own out/ is the v6 state (restored from `out_v6/`); a lens
fill of it sits in `composite/grewingk/out_lens/`, the no-lens twin in `out_nolens/` -- the lens moves only
the 90 marine domains at the bay front (+2.8 m: the sheet instead of the -2.9 m floor); the clean A/B on identical inputs changes 2.9 km2 by > 0.05 m (mean -0.01 m): > 2 m on 9.5 ha inside the lens domains (+1.1 m, the sheet instead of the floor) and 4.1 ha outside, in the lake near the ice face, where the soundings spline + bounds pass respond to the changed evidence set (the sea domains now drop their at-level ground as evidence); one ice-face cell flips 127 m.
Grewingk v6 in the dev viewer is untouched.

**2026-10-18, evening -- PLAN after Hig's v5 review (read first).**  Hig: "the most critical thing is
1 to make sure we're using the best data wherever we can (e.g. Grewingk) and 2 to make sure we're using
point cloud classification to understand where water is", detection "relying not only on geometry but
also return intensity".  Findings and the plan, in order:

*A. Best data -- Grewingk (the template has none of the site's own layers).*  The whole-bay template
reads Grewingk from KBay 2023 + 3DEP + NCEI only.  Missing: grewingk2021 (the nohf 1 m DTM, shift
+0.36 m, its own class raster, `lowest` 0.3 m / 50 m), sonar2023 (Katie Hughes' multibeam -- GATED:
fine in dev, not in a public product without her permission), sfm2023bare, dixon2023 ice + retreat_2023,
water_3dep, and the fill's site facts (lake_dmax 150 m at the level 48.91 m, ice_face free, shore_grad,
rim_widen, points_dz 0.36: the Grewingk sounding levels are tied to the UNSHIFTED 2021 lidar, so in
the bay run every Grewingk sounding sits 0.36 m low).  Plan: (1) the site layers into the template as
ordinary layers (each has its own extent; nothing per-box), dated and classed as in the Grewingk recipe;
(2) lake_dmax -> `water/edits.geojson` (a Point with dmax_m and level_m -- the stage-B route, as planned
2026-10-15); (3) ice_face free / shore_grad / rim_widen as template defaults (they were built as
general rules at Grewingk; check on China Poot / Hazel, which have soundings near shore); (4) the
sounding offset as a property of the soundings, not a recipe knob: the export writes each Grewingk
level already shifted (or the survey's level rows get the +0.36 note), so every recipe agrees;
(5) regression: the bay's Grewingk box against Grewingk v6 (same cells, same soundings).
A broader audit in the same pass: every site recipe under composites/ (grewingk, seldovia_bay,
bluff_point, kachemak_lakes_s, ...) listed against the template -- layers, class steps, fill facts --
and each difference either merged or written down as deliberate.

*B. Point-cloud water detection (the core).*  State: KBay 2023 and Grewingk 2021 have point-level
observe products (obs/*/cellstats + class); Kenai 2008 (EPT mirror, 73 GB, 14 G points, vendor classes
2 / 7 / 10 / 11 / 13-16, NO water class, Intensity present), Homer 2018 (NOAA NCMP topobathy, 97 COPC,
complete, classes 1 / 2 / 29 = bathymetry, no water-surface class, intensity present) and Homer 2019
(85 of 157 DGGS tiles readable, 72 unfinalised on the server -- ask DGGS) enter only as vendor DTMs.
Steps: (1) `observe_points.py stats` readers for the three (EPT bounds read for Kenai 2008; COPC for
Homer 2018; LAZ for Homer 2019), same 16 cellstat bands at 2 m, per 16 km box, Kenai first (largest,
and the 2008 tide-flat sheet, Beluga, Woz delta and the Woz river all hang on it); (2) a WATER RULE
for a survey without a water class, from the per-cell evidence: return density against the local land
density (specular water returns little or nothing at off-nadir angles; lakes and wet flats read sparse),
flatness of the lowest surface (zmax_low - zmin_all), intensity (near-IR off water is low, off wet mud
low but higher; dry ground and vegetation high), and the level test (one flat level over a connected
patch, a local minimum).  Taught, not tuned: label cells from the surveys that HAVE water classes where
they overlap (KBay 2023's class 9 + Hig's mapped lakes + the tide on the flight day), fit the thresholds
per survey (intensity is uncalibrated across surveys), and report precision against held-out mapped
lakes; (3) class.tif per survey, read by the recipes' `raster` step exactly as KBay's is; water becomes
a CEILING (the rules already treat it so) and the body stage gets three more surveys' water
observations; (4) Homer 2018's class 29 = BED points (green lidar): a bed observation in its own right,
below the water, instead of the DTM's merged surface -- it also fixes the 2018 "lake as a data gap" at
Beluga; (5) Beluga's head speckle (lake-surface points read as ground, likely plants) falls to the same
evidence: sparse, flat at the level, low intensity, inside a body -> water, with `islets` as the
backstop.  Cost: stats ~1-2 h per survey on this Mac (Kenai's EPT reads are the slow part); the rule
fitting is the real work.

*C. Source defects found today.*  (1) The marine seam 59.530-59.585 N at 151.576 W (x 580.55 km) is
the edge column of one 2009 multibeam depth-band grid (`kachemak_bands/H12090_4m_MLLW_2of4`, its east
edge) reading -77 m where H11938 4 m and H12090 8 m agree on -98 m: the band extraction's edge
interpolation.  General fix: in the bathy2009 VRT build, a band grid's cells within 2 cells of its own
data edge are dropped where another band covers them, and any band cell more than 3 sigma off the
coarser overlapping band is flagged -- then a census of every band pair's overlap for more of the same.
(2) Outer coast south of the peninsula: no fine grid found (NCEI's Port Graham 8/15" DEM covers only
Port Graham, inside NCEI 1/3"; the AFSC Central Gulf 100 m grid's URL returns a web page, like the
Cook Inlet one).  Staged as the fallback: NCEI Southern Alaska Coastal Relief Model, 24" (~380 x 740 m),
MSL, `bathy_src/crm_southak/crm_southak_kenai.tif` -- a prior below the regional priors, conditioned
like them; keep looking for the AFSC grid (email AFSC GAP) and for NOS surveys off the outer coast.
(3) Bradley Lake: a pre-dam surface.  The natural lake (1,080 ft, raised ~100 ft by the 1991 dam) --
USGS OFR 56-62 (Johnson & Soward 1956) has a 1:24,000 plan and profile of the lake and dam sites; the
1950s 1:63,360 topographic sheets (Seldovia C/D-?) give the pre-dam valley; USGS's legacy Alaska
2 arc-second DEM was cut from those contours.  Plan: fetch the OFR plates and the legacy DEM, georef /
compare on the dry ground around the lake, and use the pre-dam surface as a prior inside the reservoir
(the flooded margin's shape), with the natural lake's depth still modelled below the 1,080 ft level.

*Order:* A (Grewingk + recipe audit) and C1 (the band edges) first -- both are "best data" and small;
then B on Kenai 2008 (the most fixes hang on it), then Homer 2018 (bed points), then Homer 2019;
C2 / C3 alongside as data turns up.  One box rerun per fix, the whole bay once B lands.

**2026-10-16, afternoon (read first):** Hig approved v7 ("they look really good"; one lump at Bluff
Point).  THE WHOLE-BAY RUN finished 16:01 (14 h, 12 boxes) and is in the dev viewer as
`kbay_2023_topobathy_v1` -- it reproduces the Seldovia v7 patch exactly, but 680 km2 of unflown mountains
in the southern and eastern boxes were filled as smooth blobs with 100-400 m box seams (the 3DEP fallback
suppressed by the KBay class VRT's void = "flown"), and the Bluff lump stands (ground below the sea
sheet's level anchoring the zone).  Fixed by evening, all general cases (TOPOBATHY_REVIEW.md 8.5, ten
of them): the fallback counts only non-void cells of other layers and is classed after them; large dry
voids are left to 3DEP (`dry_max_km2`, `fill_dryvoid.tif` -> the fallback's `dry_void`); zone anchors are
ground PATCHES (>= 400 m2) above any tidal level within 100 m; Kenai 2008 / Homer 2018 sea-surface returns
are water (`islets`, `flat_below` after `below_level`); a bed lies below every water surface seen; the fold
ignores NaN cells; a layer's non-surface cells are never shown; **the product is build 3's `<id>.tif`**.
Seldovia regression clean (Trena +8 cm from the fold fix); both patches published as `*_topobathy_v8`;
the whole bay ran as `kbay_2023_topobathy_v2` (18:48-01:13: blobs and box seams gone, 99.9 % valid) --
but v2 was not a clean run: the chain's first builds read the previous run's `out/` products (stale
`intertidal.tif`, bodies, and `fill.tif` as the optional fill layer -- 5 km2 of ridge-top nodata in r0c2),
and the boxes' water stage ran at 4 m (`max_px`), which put OCM "bed" at the Seldovia pool's rim (pool
0.06 vs the patch's 0.79).  Both fixed (chain deletes stage products before build 1; template water
`max_px` 9100), r1c0 then matched the v8 patch at all eight sites, and **the bay's clean run is v3
(2026-10-17 01:39-10:36, `kbay_2023_topobathy_v3` in the dev viewer)**: 100 % valid, no seams or
blobs, and it matches the v8 patches at all eleven review sites.  Review 8.6.  Hig's v3 review (evening): the
oceans good, THE LAKES confused -- diagnosed and fixed as general cases (review 8.7): a regional prior
is never bed inside a fresh body (`below_level` "fresh": "void" via `water.fresh_raster`); the fallback
never stands as ground inside a body and 3DEP's hydroflattened lakes are water (`flat_water.py` run by
the chain, `--min-level` 0.5 keeps the sea out); a mapped lake stays a lake whatever its level spread.
Hig reviewed a partial v4 (01:00): Beluga Lake's
corner (Homer 2019's vendor TIN across a gap -> `facets` class step, planar to 2 cm over 10 m =
"modelled") and Wosnesenski (the channel model inside an elongated lake -> a lake body never gets it,
and takes the lake depth); review 8.8.  **v5 launched 2026-10-18 01:25** (v4 stopped at 12 boxes)
over the larger extent Hig asked for (x 557-635, y 6566-6636 km: the
head of the bay and Anchor Point; 25 boxes of 12-16 km with 2 km margins, edges chosen to cut no mapped
lake over 5 ha; `KBAY_VER=v4 run_boxes.sh`, log `composite/kbay_run.log`; ~10 h).  Then: Hig's review of
v4; Gore Lake's bathymetry (not in the soundings export); the Gulf-side 3DEP sea; the 3DEP edge on the
snow ridges; committing the two days' work (nothing committed yet).

**2026-10-15 (read first):** a deep review of the whole chain is in **`TOPOBATHY_REVIEW.md`** (the pond
at 59.57943,-151.11156 as flat ground; findings L1-L12 / O1-O5; the proposed order of work; section 7 the
point-cloud queue incl. the Homer 2019 verdict, section 8 two new patches). Running or done that day:
the 65 missing Grewingk 2021 tiles fetching (`kbay_reclass_test/grewingk_2021/fetch5.log`); the KBay
full-survey `classify` (`obs/kbay_2023_full/class.vrt`, 23 blocks); patches `composites/seldovia_bay/`
and `composites/bluff_point/` in the dev viewer as `*_topobathy_v1`; Hig's review of them measured in
section 8.1 (lagoon depth = channel-by-shape + OCM under water; missed lakes = flat with no water returns;
NCEI/OCM own quality by depth) and 8.2 the fix built the same day: `topobathy/condition.py`, regional grids as
residual-conditioned PRIORS (`condition` block on a layer; half gate in the rules), in the dev viewer as
`*_topobathy_v2`. Grewingk 2021: fetch complete, whole-survey stats + level-rule classes in
`obs/grewingk_2021_full`; the recipe still points at the box classes and has no bathymetry layer.
Grewingk itself is unchanged (v6).

**Where things are.** Grewingk Lake is at **v6** in the dev viewer (`grewingk_topobathy_v6`), built from
`composites/grewingk/recipe.json` by `build -> fill -> build` (chain scripts were in the session scratchpad; the
recipe + `make_ice.py` + `make_nohf_dtm.py` + `topobathy/dsm_bare.py` + `topobathy/flat_water.py` are the
real record). Branch `topobathy-compositing`, last commit 640225d, **nothing pushed**; the re-levelled
soundings are in dev only (prod needs Hig's approval). All 194 Grewingk 2021 point tiles are on disk
(`kbay_reclass_test/grewingk_2021/laz`); the 2021 classes over the whole box are `obs/grewingk_2021_box`.
Sections "Grewingk Lake v3 / v4–v5 / v6" below are the change log; each v answered Hig's review of the one
before, always as a general rule rather than a Grewingk tune (Hig's standing instruction).

**v6 numbers.** Lake 4.90 km2, max 173 m, 0.249 km3, 0.001 km2 above water; Berg sounder median +0.01 but
NMAD 0.38 (was 0.22 at v4 — unexplained); Hig 2026 lead lines +1.3 m (were +0.15 at v4); one 2019/21
bound violated by 4.2 m; 43 ice-face bounds violated (p90 0.7 m, worst 57 m at the run's south end, which
sits on lidar rock). Emerald Lake 0.37 km2 / 50 m / 24 m mean by hypsometry.

**Open, in order.** (1) **Grewingk Creek** — roadmap steps (a)–(e) below (reclassify the corridor; one
non-increasing water surface from the water returns; link pools across bars; channel detection back on
with the lake excluded; sections per thread). Start with (a)+(b). (2) The ~4,500 m2 shallow strip below
the SE cliff (59.59976,-151.06609): no soundings within 150 m, basin prior 2–3 m; "shores keep the band"
was tried and reverted (v6 notes) — needs Hig's call on how a cliff shore should run. (3) Why the Berg
NMAD and the 2026 lead lines got worse between v4 and v6 (suspects: the KBay layer changed the shore
rim; the shore-kept-as-ground rule). (4) Radial streaks in the basin shape near shore (widening window;
Hig to decide). (5) Hig to look at the 3DEP flat patches at 840/448/364 m and the glacier "lakes" ≥ 550 m.

## State of play (2026-10-09) and how to resume

**The pipeline runs end to end on Portage.** Review of 2026-10-09 (two
weeks idle, 59 commits behind `main`, merged clean): the decision model
and the mask tool had outrun the plumbing — every recipe was a Portage
test window, nothing could write a deliverable DEM. Hig's call: finish
step 1 of the revised order before any more physical awareness (glacier
thinning, seasonal water levels — see "Deferred: physical awareness" at
the end of this section). Done the same day:

- **Streaming full-grid build** (`composite.py build recipe.json`, no
  `--window`): tiles of 2048² built with the existing halo, in `--jobs`
  forked workers (6 workers: 193 s for the 30000 × 20000 mask recipe,
  ~9 min for the axes recipe), written into tiled sparse GeoTIFFs under
  `<work_dir>/` — `out/<id>.tif` (archive COG, ZSTD, overviews,
  `-mo vertical_shift_m=0 composite_recipe=<sha> composite_paint=<sha>`),
  `out/source_index.tif`, `masks/<layer>.tif` + `<layer>_auto.tif`, and
  for axes recipes `out/{class_now,winner,date_now,sigma,fallback}.tif`
  (`--diag` adds per-layer class/σ). **Verified bit-identical** to a direct
  window build over the same cells; the legacy regression stays bit-exact.
- **Water levels are pinned once per grid.** A coarse pre-pass (1/12
  sample) estimates every `"auto"` level over the whole grid and writes it
  into the step, so tiles cannot disagree about where the water is.
  `--pin` gives a window build the same levels. Note the mask recipe's
  single level under the sonar came out **28.99 m** over the whole lake
  against 29.21 on the test window — the lidar surveys hold the lake at
  different levels (28.9–29.2) and the whole-footprint mode picks the
  commonest; the axes recipes detect each survey's own level, which is
  the better model.
- **`out/report.json`**: per layer valid/shown/feathered km², class
  fractions, tile-median info; composite area by source; **per overlap
  pair** (every layer against every lower layer, by class of the upper
  one) n, median, NMAD, p16/p84 from a 2 cm histogram over ±250 m, and
  **`rate_m_per_yr` = median dz / Δt** where both are dated. This is the
  glacier-thinning readout: measure before modelling.
- **Class / σ paint is read back at build time** (`axes.apply_class_paint`
  / `apply_sigma_paint`, shared with the tool) — before this, a saved class
  correction changed nothing in a build.
- **Paint files cover the full grid, sparse** (`topobathy/paint.py`):
  first save creates the file, later saves are windowed `r+` writes, old
  domain-box files migrate on first touch. Several windows accumulate into
  one record; the tool reloads all three kinds on restart.
- **`import-mask` / `export-mask`** (the Photoshop round trip) and
  **`publish`** (prints, or `--write`s, the `datasets.json` entry:
  `archive_mode: copy`, `dev_only: true`, zooms from the grid).

Next: the hosting side runs `build_lidar.py <id>` + the dev catalogue
for the two dev_only entries so they show on `/lidar/` (WORKSTREAMS
boundary). Hig redirected the second site from Lituya to **Kachemak Bay**
(next section). The rule set is frozen until a named site fails.

## Roadmap (2026-10-14, Hig's outline, refined)

Hig's sequence after Grewingk: lessons -> preliminary KBay -> broad dataset -> water surface/depth ->
hand edits -> publish -> explore (glacier vs ground, sediment vs bedrock).  Refinements, agreed or open:

**0. Grewingk "dialed" -- an acceptance list, so done is checkable.**  Soundings fit (median ~0, NMAD
< 0.3 m); lower bounds honoured or explained; no lake cell above the water; no seams or streaks in the
deep bed; the ice face vertical, no glacier as ground; the outlet CREEK a channel, not a string of ponds;
v-numbered in the dev viewer with the as-of date.  Open now: the creek (below), the basin shape's radial
streaks (widening window, Hig to decide), 30 ice-face bounds, 0.12 km2 above water.

**The creek is step 4 pulled forward.**  Grewingk Creek needs the water surface model anyway: it is ~45
wet domains (pools between bars), each given a flat level and a little basin.  Measured 2026-10-14: the
points' water returns slope continuously 48.8 -> 21 m over ~2.3 km; the vendor DTM's creek is NOT the
water (40.2 m where the returns are 42.5-43.4 m at x 605.4 km: stream hydroflattening drops each reach to a
downstream level); class 1 (unclassified) is as common as ground there.  So: (a) reclassify the creek
corridor (water / bar / ground) where class 1 hides it; (b) ONE water surface for the network from the
water returns, smoothed along the flow and non-increasing downstream; (c) link the pools across bars into
one channel system (threads of a braid share the surface); (d) channel detection back on with the named
lake (lake_dmax) excluded -- it read the lake's east end as a river because the retreat zone's water came
from the SfM; (e) sections per thread from width (fill.channel_beds), conditioned by any depths Hig has.
The water surface this builds IS the first piece of the water-surface DEM.

**1. Lessons -> defaults, and a regression set.**  Not a prose review alone: each lesson becomes a recipe
default or a rule with a test.  Candidates: SfM never over water; ice by multi-epoch lowering (and the
SfM's +3.4 m water bias); vendor DTMs hydroflattened (rebuild from points where a lake or river matters);
per-survey vertical shifts in ONE registry with their evidence (Grewingk +0.36, Seldovia xGEOID17B 1.42,
MLLW via bench marks); sounder sound speed; lower bounds; free ice faces; shore-gradient with widening
windows; bergs are not islands; lake levels from returns not shores.  The regression set: the windows we
have tuned (Portage, Peterson-China Poot-Halibut Cove, Grewingk, the KBay reclass tests), each with its
metrics (soundings fit, holdout, above-water lake cells, seam/curvature score), run after any rule change.
`composite.py holdout` exists; a `regress` over all windows is the missing piece.

**2. Preliminary KBay -- scale is the issue, plan for it.**  build is tiled and streams; FILL IS NOT (one
whole-grid pass: 13 min for Grewingk's 21 M cells; KBay at 2 m is ~30x larger, and domains such as the
bay itself span tiles).  Needed: fill by domain (each wet domain with its own bbox + halo, small ones
batched), coarse-to-fine for the big marine domain, and a cache so an unchanged domain is not refilled.
Iterations then cost minutes where something changed, not hours.  Also: which other surveys are
hydroflattened and need the point rebuild, and the point fetches that implies (the DGGS zip route, ~0.5 MB/s
here -- start early).

**3. Broad dataset.**  Decide the extent (Kenai Peninsula? every hosted survey? Kachemak + Cook Inlet
margin?), the resolution (DECIDED 2026-10-19, Hig: 2 m for now; where a scenario needs 1 m on dry ground, e.g. a landslide source slope, read the 1 m lidar directly), and the time model: one as-of date
per region, or the newest credible observation everywhere with a date raster (the build already writes
date_now).  The outer bay: NOAA OCM 2023 seamless 2 m, underwater only (reference_outer_kachemak_bathy).

**4. Water surface and water depth.**  Products: water surface elevation (lakes flat at a stated level and
date; rivers sloping, from returns; the sea at a stated tidal datum, MHW or MLLW -- not the flight's
tide), water depth = surface - bed, both with sigma.  Hydraulic awareness: a non-increasing-downstream
surface along each channel, sections that conserve area across braids, bars and pools; a flow network to
carry it.  Decide whether the surface is the flight-day stage (what was seen) or a reference stage (what
modellers want) -- probably both, the second a parameter.

**5. Hand edits.**  The paint/mask tool already edits classes, sigma and masks (Tier 3).  What Grewingk
needed and the tool cannot do: a lake-here seed (make_ice HAND_LAKE), an island keep (make_nohf
ISLANDS_KEEP), a lower-bound run of soundings, a lake level, a bed shape (a breakline / thalweg / contour
with depth and sigma -- the sounding_contours table is already that shape).  Proposal: edits as small
GeoJSON features in the repo, each with a kind, the values, who and why, applied every build, listed in
the report; drawn on /lidar/ with the landform-trace machinery.  The scattered hard-coded lists become
the first edits.

**6. Publish.**  Topobathy, its sigma, the source index (provenance per cell), water surface, water depth;
a methods page section; versioned ids.  LICENSING: a composite that includes a gated survey (Katie
Hughes' multibeam, gated pending attribution) cannot be public until that survey can be -- either
permission first, or a public build without it.

**7. Explore.**  Glacier vs ground (multi-epoch lowering generalised, the ortho, ITS_LIVE velocity, the
G10040 outlines; a dated ice-front layer -- the face retreats every year); sediment vs bedrock (roughness
and slope signatures, depth-to-bedrock priors, the lake beds as sediment traps); buoyancy constraints at
calving faces; uncertainty that a user can query; ADF&G contour maps and the Emerald Lake depth
distribution as soft constraints.

**Questions for Hig.**  (1) Which first application drives priority -- hazard modelling (landslide into a
lake, outburst), navigation/recreation, habitat?  It sets the water-surface datum and how much hydraulics
matters.  (2) Broad extent and resolution.  (3) Any depths or cross-sections on Grewingk Creek?  (4) The
gated multibeam in public products.

## Rivers and the water layer (agreed with Hig 2026-10-19, not built)

**Purpose.** Hazard modelling (tsunamis, GLOFs) on Grewingk first: the lake and the slopes above it, the outwash
plain and the nearshore bathymetry. The runs themselves happen in **Celeris** (maybe GeoClaw later), in another
context; **the bound of this project is the topobathy plus a seeded water layer**. KBay v7 (2026-10-19) is accepted
as the first-pass topobathy: remaining issues are at a hand-correctable level.

**The water layer.** Lakes: filled to their observed lidar level. Rivers: a steady flow at one consensus discharge.
Sea: **two outputs, MHW and MLW** (the tidal state is a scenario variable in Celeris; two states show the
variability). Rivers join the sea level of each output. Flood-stage river cases are deferred (Hig expects they
matter little; to be explored later).

**River beds by inversion against the observed water surface** (replaces the constant width/depth section on the
big reaches: Grewingk, Wosnesenski, a few others; everything else keeps `fill.py channel_beds`):
1. Observed WSE: the lidar's water-surface returns along the reach, smoothed into a downstream profile (interpolated
   along the channel where returns are sparse on turbid braids).
2. Discharge: three independent estimates, compared — USGS Alaska regional regression (ungauged basins: area,
   precipitation, glacier and lake cover; report to be confirmed), survey-day degree-day melt (Homer temperature on
   the flight dates x glacier area) + baseflow, and hydraulic (observed width + surface slope + Manning). Agreement
   within ~2x -> use the hydraulic one; disagreement is a diagnostic.
3. Loop: a local-inertial 2D steady solve (numpy, 2 m) over the trial bed -> modelled WSE; lower the bed where the
   modelled surface sits above the observed, raise it where below. Regularised by a quasi-Exner step that moves
   sediment from high-shear to low-shear cells, steering bed shear toward the gravel-mobility threshold
   (h ~ theta (s-1) D / S; Grewingk/Woz are pebble-to-boulder gravels, mostly < 50 cm with many small pebbles,
   ~1 m boulders on the steepest reaches -> D50 a few cm on the plain, coarser on steep reaches).
4. **Edits are minor and shape-preserving (Hig's emphasis).** The observed channel planform and banks stay; the
   algorithm may remove false islands and pimples (small, isolated, low patches inside the wetted channel, likely
   misclassified water-surface returns) and smooth the bed, not re-route or widen channels. Implement as bounds:
   only cells in the active braidplain (low height above the channel, no canopy returns) may change; a cap on the
   vertical change of lidar ground; a size cap on any dry patch the loop may submerge.
5. Output per reach: the bed, and WSE / depth / velocity at the consensus discharge for each sea state.

Open before building: MHW/MLW in NAVD88 from the Homer/Seldovia CO-OPS datums (not guessed); Celeris's input
format and the grid it will run on (a bay-scale Boussinesq grid is likely much coarser than 2 m, so sub-grid
braids smear; the topobathy stays 2 m and resamples).

## Water as a linked surface in the viewer (Hig 2026-10-20; first dev build the same evening)

Hig: water, and eventually ice, is a DEM of its own linked to the topobathy DEM, displayed with it, the water
see-through with its own opacity and styling (default a blue hillshade), and aimed at **true see-through water in
3D** rather than a drape on the bed.

**Contract.** A survey's catalogue entry carries `surfaces: [{kind: 'water'|'sea', url, encoding}]`, each a
terrain-RGB PMTiles on the survey's own tile grid and zoom range. `water` is the inland surface; `sea` a mask of
ground connected to the sea (the viewer floods it to the chosen tide). The shoreline is wherever the surface stands
above the bed, per pixel, at every zoom. When surfaces publish beyond dev they must be gated, pushed and audited
**with** their survey (same trap as orthomosaics). The first toggle's `water_url`/`sea_url` are still read.

**Build** (`topobathy/water_surface.py`, then `dev_viewer_add.py --water-surface ... --water-sea ... [--surfaces-only]`):
smoothed over water cells only (normalised Gaussian, 4 m; a lake stays exactly flat), padded 24 m past the
shore at the nearest water level and held 5 cm below the bed wherever the bed is lower (so the padding never shows
at full resolution, but averaged zooms and the 3D mesh have a surface on both sides of the shore), baked
**terrarium** (1/256 m; `build_lidar.build_web(..., encoding='terrarium')`) because 0.1 m steps stripe a shaded
river. Grewingk (from the creek prototype's wse/sea): 6.55 km2 water, 2.63 km2 padding, 0.88 km2 of it held below
the bed; the smoothing moved the surface p50 0.00 / p99 1.04 m (the p99 is at steep reach transitions -- look).

**Viewer** (`inventory/static/inventory/js/ls_water.js`, wired in `lidar_preview.html`): 2D is a raster drape
(`lswater://`) over the bed's shading: blue hillshade of the water surface (shared sun, its own relief factor),
depth tint, or flat. 3D is a **custom WebGL layer**: one mesh per water tile in view (a vertex every 2 px, seams
closed from the neighbours), z = level x exaggeration on the same vertical scale as MapLibre's terrain (verified
against maplibre-gl 5.24.0: `defaultProjectionData.mainMatrix` takes z in mercator units at the centre latitude),
depth-tested against the terrain and blended premultiplied over it; the shoreline is the interpolated zero of
depth (discarded below). MapLibre draws custom layers outside its render-to-texture drape, so the water is a real
second surface and the bed's own shading shows through it.

Open: ice the same way (needs a bed under the ice first; today the composite's glacier surface IS ground); the
profile tool drawing the water line and the Alt-click probe reporting depth; joint-pilot water as a surface (the
layer stage's z_w + hole-rule levels; inferred water styled apart from observed); a mesh LOD for pitched far fields
(today the tile zoom drops until <= 64 tiles cover the view); fog does not reach the custom layer.

**Bergs on Grewingk Lake (Hig's review of the see-through water, 2026-10-20; rules agreed and built the same night).**
Seeing the water and the bed together showed lumps in the water surface with needles in the bed under them.
Measured on the dev surface: 345 lumps standing 0.3-15 m above the 48.91 m lake; 123 needles rising a median 14 m
(max 30) off the lake floor with tops a median 1.4 m under the water, 113 of them beside a lump; 35 deeper bumps,
17 on a 2017 sounding 5-20 m shallower than its own track. Cause: Grewingk 2021's returns off icebergs (mostly
classed water, some ground) passed the fill's island rules ("above level + 0.2 m = island") and the bed was held
just under the water at each; the water layer took the same returns as the surface. Bergs drift: 891 of the 920 m2
of Grewingk 2019's "ground" above the lake is open water at lake level in 2021. Three general rules:
1. **A lake is flat** (`water_surface.py`): raised patches enclosed (>= 80 % of their rim) by a lake at its level
   are set to the level. Same rule wanted in the point-first water stage (water returns above a lake's level are ice).
2. **Floating ice is not bed** (`fill.py`, lakes only, `floating_ice`): water-classed observations standing above a
   lake are dropped as evidence; a raised land patch counts as an island only if a second survey layer saw land on
   >= 25 % of it (`_land_n`, from the per-layer class rasters, passed by `composite.py fill`) or a hand-mapped
   island touches it.
3. **Soundings despiked against their own track** (`fill.py`, `points_despike_m` 5 m): a sounding more than
   max(5 m, 3 x robust scatter) shallower than a plane through its own survey's soundings 5-40 m away is dropped
   (a keel, a fish, a side lobe). Dry run on the whole export: 29 of 5,735 berg_grewingk_2017, none elsewhere.
Applied to v7 boxes r3c2 + r3c3 (fill + build 3; v7 outputs backed up to Powder lidar_build/v7_box_backup) and the
dev survey `grewingk_topobathy_water` rebuilt from them; the other 23 boxes still have the old fill.

**Hig's review of the joint pilot run 5 (2026-10-21; general fixes built the same day, run 6 under way).** Bergs gone,
river good. Five faults, three causes:
1. **The berg rule judged ground that is not on a lake** (`fill.py` domain step). "Uncorroborated raised land in the
   closed water" was tested for survey count and size only. With one pooled lidar layer, and KBay 2023 absent from the
   outwash (96 % of it seen by one survey), 108 patches -- the creek's gravel bars and banks, tide-flat ground near 0 m,
   mountain ground at 100-900 m ringed by void holes -- were pulled into water domains; Grewingk Lake's domain ran down
   the creek to the tide flats (7.1 km2 against the box's 4.9) and took the lake's 150 m basin as its prior (19-45 m on
   the flats), so the intertidal between the multibeam and the lidar bent to -6..-14 m ("deep water assumed",
   "terraces"). Rule now: a berg floats -- the patch must be ringed mostly (>= 50 %) by a body the water stage calls a
   LAKE and its median must stand above that body's level. Dry run on run 5's inputs: 2 of 76 candidates qualify (2 m
   over the 65.7 and 96.4 m lakes). Also `land_support` (recipe key on a pooled layer; `joint_fuse.support` ->
   `joint_nland.tif`, surveys with a surviving ground return per cell): `_land_n` counts a pooled layer as that many
   surveys, not one. The v7 boxes ran the old test too (r3c3 35 patches, r3c2 91) and want a refill.
2. **The multibeam was not in the fill** (`composite.py fill`). `class_now` is the newest layer's class; over Grewingk
   Lake the joint lidar (2023-08) says water above the May 2023 multibeam, so the multibeam's cells read as
   water-classed observations 10-25 m under the 48.9 m level -- "returns at the water level" -- and were dropped
   (270,000 cells); the lake bed was modelled without them and the build pasted them back over it in steps of metres.
   Rule: at an observed cell the fill takes the WINNING layer's class when that is bed (15.7 km2 in the joint pilot).
3. **3DEP stood where the fill had judged** (`axes.py` fallback). Two new places a fallback is never ground, read beside
   the bodies raster: cells the fill judged the water surface (`fill_surface.tif`; 3DEP's 2012 lake at 41.3 m stood as
   single-cell pits along the 48.9 m shore: Hig's 59.61742,-151.12098 and 59.61914,-151.10468), and the intertidal zone
   below `intertidal_max_m` (4.5 m): every gridded prior is void there and a fallback is one (0-3 m 3DEP tables between
   the 2009 multibeam and the 2021 lidar: Hig's mesas at 59.65427,-151.18952 and 59.65615,-151.18643).
Viewer, same review: green unshaded tiles were a STALE SLOPE PYRAMID -- `build_lidar.build_slope_native` reused any
existing slope raster (the joint pilot's from run 1, a smaller extent: 5,841 of 6,841 z16 slope tiles missing), and
`build_slope_web` judged its tiles against the archive rather than the slope raster, so `--resume` kept stale tiles
even after a recompute. Both now go by mtime against their real source. Soundings in 3D (`soundings.js`) are markers
at their own elevation (a lower bound a down-pointing arrow), no longer dots draped on the modelled bed.
Still open from the review: the creek's flow-based tuning is not in the joint pilot (the inversion is in the v7 dev
surface only).

## A point-first build (agreed with Hig 2026-10-20, not built)

Why: the raster stack cannot tell a measured cell from an interpolated one. A vendor TIN across a gap "has a value",
so it counts as data -- the recurring "gap read as flat data" fault. Measured on Grewingk Creek (2026-10-20): the
mesas Hig flagged at 59.6304,-151.1437 and nearby have ground or water returns in only 3-19 % of their 1 m cells;
the rest is the vendor's interpolation carried into our no-HF rebuild, 1.5-2.5 m above the water. The pimples
(59.62161,-151.13252) are different: about half their cells have returns (water-surface/spray read as ground).

The design (Hig's, refined):
1. ONE POINT CLOUD. Every source in one CRS (NAD83(2011) / UTM 5N, NAVD88 GEOID12B), per-survey vertical fixes
   applied per point (Grewingk 2021 +0.36 m, Seldovia's geoid model), co-registered on stable ground. Each point
   carries survey, date, original class, return number, scan angle, intensity, sigma.
2. GRIDS AS POINTS. Multibeam, BAGs, OCM, NCEI, CGOA: one point per cell, a class of its own. Soundings and the
   Grewingk sonar as points. A grid point's sigma must carry what gridding lost (Hig): source error, PLUS the
   representativeness of a cell centre for a cell of size D on a slope g (planar term g*D/sqrt(12), more with
   curvature and sub-cell roughness), PLUS horizontal position error times slope -- calibrated empirically where a
   finer source overlaps a coarser one (multibeam vs NCEI/CGOA, as a function of slope and depth). Steep, coarse
   bathymetry is then weak evidence, and sparse soundings can amend it.
3. SURFACES WITHOUT POINTS taken at face value, with sigma: the Chugachmiut lidar mosaic (Powder, Alaska GIS/
   Raster/Chugachmiut_lidar_GeoTiff_Mosaic/be_mosaic.tif, 1 m, NAD83(2011) UTM 5N, x 558783-592412, y 6559945-
   6588985, file date 2018-12; vertical datum not declared; no point cloud found on the portal, 3DEP or the web,
   2026-10-20). Interpolated areas detected by their TIN signature (planar facets) get a larger sigma; the base
   sigma calibrated against KBay 2023 where they overlap.
4. VINTAGE MAP (polygons, edited like the water edits). Default: surveys POOL after alignment -- this is the
   payoff: a few true ground returns from a weaker survey anchor a stronger one's classification (Hig's cases: bad
   2021 ground near 59.62233,-151.18928; bad 2023 ground near 59.58211,-151.17475). SINGLE VINTAGE where the
   surface moves: a river's active floodplain takes one survey (Grewingk Creek 2021, the Wosnesenski 2023 -- the
   most recent, by coincidence); glaciers the most recent; beaches and active bluffs single vintage too, but like
   glaciers (no water drains through them) steps are allowed at the boundary -- feathering or a simple beach model
   like the rivers' is a later layer on top.
5. JOINT GROUND CLASSIFICATION per 1 km unit: the KBay reclass chain (model/apply.py) extended to several surveys.
   A return's vote rests on cross-survey agreement, not only on being low (pooling must not import one survey's
   errors). Water-class returns stay a separate class: water-surface observations for the water layer, never bed.
6. GRIDDING WITH SUPPORT: a cell with no supporting point is VOID, never interpolated; uncertainty grows with
   distance to the nearest supporting point. The voids go to the fill, which knows lakes, channels and the tidal
   lens. Everything downstream (fill, water bodies, tidal lens, sounding conditioning, the water layer) stays.

Uncertainty is the hardest and most important part (Hig, 2026-10-20): per-point sigma by source and class
confidence, grid-point sigma from gridding loss, sigma with distance from support, and the fill's sigma, all on
one scale so the joint solve weighs them honestly.

Point holdings (2026-10-20):
| survey | points | where |
|---|---|---|
| KBay 2023 (NOAA OCM 10418) | 28.4 B, COPC, 175 GB | /Volumes/Powder/lidar_src/kbay_2023_laz/tiles |
| Grewingk 2021 (DGGS) | 261 LAZ, 22 GB | lidar_build/kbay_reclass_test/grewingk_2021/laz |
| Kenai 2008, reclassified (PTD) | 3,130 LAZ (2 km tiles) | /Volumes/Powder/lidar_build/kenai_2008/classified |
| Kenai 2008, raw EPT | 73 GB | lidar_src/kenai_2008 |
| Homer 2018 NCMP (OCM 8686) | 89 COPC, 2.5 GB (complete) | lidar_src/homer_2018_ncmp |
| Seldovia 2019 NCMP (OCM 8837, Blk_528_Seldovia) | 31 COPC, 1.4 GB (fetched 2026-10-20); OCM's copy is on GEOID12B, NOT the vendor grid's xGEOID17B: ground minus KBay 2023 +0.13 m (NMAD 0.30, 2,052 pts above 3 m), against -1.42 m for the vendor grid | lidar_src/seldovia_2019_ncmp |
| Homer 2019 (DGGS RDF 2021-2) | 85 readable + 72 unfinalised tiles (DGGS re-export needed) | lidar_build/homer_2019_pc |
| Chugachmiut | none found | raster only |

PILOT 1 (2026-10-20, built): `topobathy/joint_pilot.py` (units -> mosaic -> fuse) + `topobathy/joint_fuse.py`, window
x 599-607 km, y 6604-6614 km, KBay 2023 + Grewingk 2021, outputs on POWDER (`/Volumes/Powder/lidar_build/joint_pilot/`;
Nunatak filled to 0 bytes during the first unit run and stalled v7's r0c2 -- the pilot's temp points are GBs a unit).
Per unit (48 s, 4 GB): 2023 ground = points within 0.25 m of our reclass v2 (v2's decision carried back to points),
2021 ground = vendor class 2 + 0.36 m; 1 m grids of count/median/min ground, water, max return. Fuse: alignment on
1.33 M stable cells, 2023 - 2021 = +0.005 m (NMAD 0.11) -- the +0.36 m holds; vintage zones: creek floodplain (2021)
2.4 km2, beaches (2023) 4.1 km2 (first try made a 52 km2 'floodplain': no returns had counted as bare -- evidence
must be returns, never their absence); rejected as standing above the other survey's own returns: 2023 0.46 km2,
2021 0.32 km2; supported 57 % of the window (2023 only 12 %, 2021 only 29 %, both 15 %), void 34.7 km2 (lake, bay,
channels, dense forest). KBay 2023 does not cover the window's north strip (y >= ~6609-6611 km; most of the creek).
At Hig's sites: bad-2023 blobs removed (joint up to 0.5 m below v7, 2021 carries it); bad-2021 unchanged (v7 already
took 2023 there); the creek mesas and pimples are VOIDS now (they were interpolation), for the water/bed step.
Dev viewer: `joint_pilot_grewingk` (gaps <= 4 m filled, larger voids open, 2 m).
Into the chain (recipe `composites/joint_pilot`, the r3c3 recipe + a `joint` layer whose class raster says ground /
water / void; KBay 2023 and Grewingk 2021 voided inside the window so the joint layer is their only voice there).
RUN 1: the joint layer won 55 km2 of the 80; lake beds from bathy2009, the fill 3.6 km2 -- but the CREEK CHANNEL fell
to 3DEP (1.2 km2 window-wide, interleaved with the fill: the pimple texture again, 3DEP 2+ m high in the channel).
Cause: water returns over a river are sparse (specular / turbid dropout), so a channel is a strip of water returns
among voids, and a void was left to the layers below. Exactly Hig's warning about 3DEP filling lidar holes over water.
Fix, general: `joint_pilot.py layer` -- a cell with no ground return takes the NEAREST evidence: nearer water returns
(within 15 m) = water at the returns' smoothed level; within 2 m of ground and nearer ground = a gap, interpolated;
else void (placeholder, never shown). Water 2.5 -> 9.9 km2 (creek channel + the voids between the lakes' water-return
swaths); no water spread into forest voids. Run 1's layer and composite kept as `*_run1.tif` on Powder.
LAYER v3 (Hig 2026-10-20: "a hole within a lidar survey is much more likely to be water than an oversight" -- do not
fill holes in good data with poorer data unless the hole cannot be water because it is steep). A cell with NO RETURN
AT ALL from either survey (water absorbs; a forest hole still has its canopy -- a non-geometric test from the points
themselves) inside the surveys' tile footprint and not open to its edge is WATER, at the nearest water returns' level
if it touches them, else its rim's 10th-percentile ground -- unless 3DEP slope (~20 m) > 20 deg: then void (poorer
data may fill). Returns-but-no-ground = ground under vegetation, void. Window: water 17.95 km2 (8.1 from holes), steep
holes 0.03 km2, void 7.3 km2 (1.2 under vegetation, 6.0 holes open to the footprint edge -- the bay). Hig's 2021 'oops'
hole (59.58293, -151.11700) is a no-return hole where 2023 has ground -> pooled ground, never reached by this rule.
IfSAR CHECK (`/Volumes/Powder/lidar_src/ifsar_grewingk/`, cells N5930W15115P/15130P, flown 2014-08-29..09-12; ORI
2.5 m, DTM 5 m, hydro polygons): the ORI is MASKED to 1 inside every hydro polygon, so over water it carries no
backscatter of its own -- it is the hydro mask again. v3 water lies 98 % inside the hydro polygons (17.58 of 17.95 km2);
the 0.37 km2 outside is narrow creek and ponds, radar-dark there (ORI median 51 vs 115 on lidar ground); v3's voids
outside hydro read like land (median 117). So IfSAR corroborates the rule and its job is BEYOND the lidar: hydro
polygons say where 3DEP is water; ORI's dark tail can flag small water the hydro missed.
GREWINGK 2019 is a separate survey (portal project 295, Classified Points dataset 1388, 35 LAZ 2.3 GB, DGGS RDF 2021-6,
flown 2019-06-03 (the metadata; 'Nov 2019' in kbay_reclass/README.md is wrong), NAVD88 GEOID12B, NO CRS in the LAS
headers (NAD83 UTM 5N by its coordinates), 427 M points over x 602.3-611 km, y 6605.8-6609.8 km,
Hig: a nice point cloud for a small area) -- our gk2021 is DGGS RDF 2023-2 (flown 2021-10-12). On Powder at
`/Volumes/Powder/lidar_src/grewingk_2019/` (tiles.txt = extents); `joint_pilot.py units2019` grids it; to be brought into the joint
cloud as a third survey (alignment check first).

The river inversion prototype (Grewingk Creek, 2026-10-20) is ON HOLD behind this: most of what it was
compensating for (mesas) should vanish once interpolated cells are not data. Its pieces (the flow solver, the water
layer, the discharge-identifiability lesson: survey-day WSE alone cannot fix Q; the high-water inundation extent
against the active floodplain can) carry over.

## Water bodies (stage B) — design agreed 2026-10-15, first implementation the same day

Hig's prompt (2026-10-15, after the Seldovia lagoon came out 5 m deep where it is ~1 m): "start building
vector water body maps to support this topobathy creation ... helpful for drawing clear maps, as well as
for doing this topobathy stitching, and it would provide a simple basis for manual review — e.g.
assigning depths to lakes that are known to be different from the algorithmic inference."  Three cases
set the shape: a small-outlet lake (nearly one polygon, tens of cm between surveys, but two lidars never
agree exactly — a quality- and season-aware consensus, used by the bathymetric inference AND by the
point filter); a large river (rises, falls, moves; the outline follows the newest survey, but a rapid's
rock exposed in an earlier survey must not be buried under a later water surface — the topobathy
discriminates a static rock from an eroding bank where a DTM would not); the intertidal (several
surveys, several shorelines, all of them context for the point cloud, the lowest the winner for
topobathy).  The consequence: one polygon per body cannot carry these.  What each survey saw is kept as
its own record; "consensus" is a derived view whose rule depends on the kind of body and on who asks.

**Three tiers, one schema** (`topobathy/water.py`; outputs in the composite's `out/`):

1. *Observations* — `water_obs.geojson`, one feature per connected water body PER SURVEY, never merged:
   survey, date, season, sigma; `level_m` (median), p10/p90, `level_kind` flat | sloped (sloped: p2/p98 as
   the up/down levels; the per-survey surface itself stays in `water_z.tif`); `method` (vendor class,
   level-flat, flat-below, vs-reference); area; touches the grid edge.  The survey's `sea_level_m`: its
   largest edge-touching body below `tide_max_m` — the tide that day.
2. *Bodies* — `water_bodies.geojson` + `water_bodies.tif` (id raster on the grid), one feature per body,
   the union of every survey's water, with a STABLE id (matched to the previous build's bodies by
   overlap, so edits stay attached).  `kind`: sea | tidal (at that day's sea level in every survey) |
   perched (above the sea that day, below `tide_max_m` — a lagoon the tide can reach) | lake | river (a
   sloped observation) | ephemeral (dry ground or bed observed in another survey, `dry_frac`); flag
   `ice_contact`.  Per-survey levels and dry fractions; `level_lo/hi`, `spread_m`, `n_surveys`;
   `consensus_level_m` + `consensus_rule` (flat kinds: 1/sigma^2-weighted mean, spread reported — season
   explains spread, it does not correct it; sea/tidal: per date, none; river: per survey, none);
   `latest_level_m`; `dmax_m` + `dmax_method` when an edit sets one.
3. *Edits* — `tools/lidar/water/edits.geojson` in the repo, hand facts matched BY LOCATION with a kind
   guard, not by id: Point or Polygon with `kind`, `level_m`, `dmax_m`, `hypsometry`, `who`, `date`,
   `why`.  The build reports every edit used and every edit that matched nothing (`water.json`).  The
   recipes' `lake_dmax` lists become rows here (Grewingk's not yet moved: v6 is to stay as built).

**The rule that unifies the cases: a water observation is a ceiling, not a surface.**  Among bed/ground
observations at a cell the newest wins (an eroded bank, a grown bar: real change); a water surface never
supersedes an exposed bed, it only says the bed is at or below that level.  The composite already does
this per cell (TARGETS: water never stands for the bed; water -> bed admits any bed).  The tiers add the
per-body level per date that makes the ceiling exact and the kind that picks the fill model and the bed
cutoff.  The lagoon fault was that cutoff being one global MLLW (`below_level` at -1.38) rather than the
body's own level: OCM's -0.3 m values under water ponded at +0.8 were thrown away as "not bed", the
hole went to the fill as a MARINE domain, and the channel-by-width model carved 5 m.

**Consensus by kind and by use:** stable lake — DTM: one flat level, hydroflattened; topobathy: the
same level as ceiling, shore-slope/hypsometric bed, soundings and edits override.  River — DTM: the
newest surface; topobathy: newest bed per cell, each survey's sloped surface its ceiling.  Tidal — DTM:
the newest shoreline (or MHW for a map); topobathy: THE TIDAL LENS (Hig, 2026-10-16, after v6: "low-relief
intertidal areas tend to have a lot of sediment that moves around and makes for quite flat surfaces. I
would rather back off and make conservatively flat assumptions to start with, and then measure or
otherwise generate data to describe the depth of sites that are overly flat") — the observed water surface
IS the surface until a measurement says deeper; no low-tide floor, no channel by width; a gridded prior
(OCM, NCEI) is void in the intertidal zone.  Perched (above the sea that day, below `tide_max_m`) — the
same lens: its level is ceiling, sill and, until measured, bed (the 1 m cap and basin of the first cut are
gone); a mapped lake at that height is a lake.  Ephemeral —
a bed/ground observation from the dry date wins outright.  Glacier-contact — latest level, ice rim free,
retreat decided per body (not built yet).  Also to hold: a frozen lake (method ice: a level, not a water
class); kelp (an observation quality flag on the sea, not a body); braided outwash (reaches along a
centreline, "shallow sheet" fill, not channel-by-width); an estuary (a sloped reach whose downstream
level is the tidal body it drains to); a lake with soundings (`dmax_method: soundings`).

**Consumers wired 2026-10-15** (`tools/lidar/topobathy_chain.sh recipe` = `build --diag` -> `water` ->
`build` -> `fill` -> `build`; the Seldovia and Bluff Point patches are `*_topobathy_v3` in the dev viewer):

- `class_raster` `below_level` takes `"bodies": "$out/"`: inside a lake / perched / ephemeral body the bed
  cutoff is the body's consensus level, elsewhere the step's `level` (MLLW for the sea).  A body with a depth
  (an edit's `dmax_m`) is also a FLOOR, level - dmax - max(0.5, dmax/2): a layer with sigma >=
  `floor_sigma_m` (1 m: OCM, NCEI) below it is void there; a better survey stands.  The fill holds its
  modelled bed above the same floor (the outlet whose rim is the channel's bed is a sill), lowered to any
  measured bed inside the lake (Trena Lake, v6: the floor stood 2 m above the green lidar's bed).
- `fill_holes` reads the id raster + table: a lake / ephemeral domain is NOT marine, takes the body's
  level and the edit's depth; a sea / tidal / perched domain IS marine whatever its rim, and marine means
  THE TIDAL LENS (below).
- **The tidal lens (2026-10-16, Hig's v6 review, TOPOBATHY_REVIEW 8.4).**  `fill.py` `tidal_lens`
  (default): a marine domain's bed is its observed water surface (`water_z` per cell smoothed over
  `channel_ws_smooth_m`, else its level), sigma `tidal_sigma_m` (1 m) at the shore + `sigma_rim_m` per
  100 m; no low-tide floor, no channel by width (`channel_beds` skips them), no perched cap; measured beds
  under it condition it like a lake's, the surface is a ceiling the conditioning never lifts the bed above
  (`fill_mask` 7); an edit's `dmax_m` / `hypsometry` still makes a basin (`tidal_lens: false` restores the
  first conception).  `water.py`: a sloped surface below `tide_max_m` is "tidal", not "river" (a creek
  draining the flat; the river model at 75 m width had dug 5.8 m); a perched body Hig's map calls a lake is
  a lake; `intertidal.tif` = the tidal-kind bodies plus everything within `intertidal_near_m` (100) of
  ground or water any survey observed between the fill's `low_tide_m` and `tide_max_m` (the return holes
  on a mudflat that OCM's -3.3 m showed through, the sheet along a beach).  `below_level` `"intertidal":
  "void"` on a prior layer: no bed there.  `edits.geojson` `withdrawn`: the record stays, the fact is not
  applied (the pool's 1 m).
- **A water observation is a ceiling, in the rules themselves** (`state_rules_sequential`, `CEIL_TOL_M`
  0.2): where the newest class is water, a running non-bed value within 0.2 m of its surface or above is
  evicted and the earlier masks cleared, so the lake is a hole for the fill.  Found at Bluff Point: Kenai
  2008's "ground" (its water surface) at 22.2 held over Homer 2019's water at 22.03, so the lake had never
  been filled.
- **`flat_lakes` class step** (the lakes with no water returns, review 8.1): a patch flat to `rough_m`
  (0.03) against the 14 m local MEDIAN (the std about the mean was fragmented by stray returns), with the
  twice-smoothed median's local slope under `slope_max` (1 %; a windowed range failed every cell within
  14 m of a shore, most of a 60 m lake), of class ground or VOID (a lake with no returns is void in the
  point classes, its DTM the vendor's flat TIN), opened 1 / closed 2, >= `min_area_m2`, one level
  (p90 - p10 < `range_m` 0.3), and a local minimum: of the ring cells that DIFFER from its level, >=
  `ring_frac` (0.65) stand `rim_dz_m` above it (a bench or a tide flat drops away on a side).  All four of
  Hig's lakes found; on Homer 2018/2019 and KBay 2023.  Plus (Hig's v3 review): `density` -- the
  observe_points `cellstats` n_all band, a patch must be mostly sparse (<= 8 returns per 2 m cell in >= 50 %
  of it); a wet mudflat with 0.2 m of relief is the twin of a small lake on every surface measure and
  returns like dry ground.  VOID cells skip the flatness/slope tests (a dammed reservoir's TIN slopes 1 %),
  a void-dominated patch gets the loose range (1 m) and its level from its p10 (the TIN bows up).
- Bodies: a hole inside a body that no survey classed as land is water (no-return gaps let the regional
  grid through); the SEA SET (cells at or under the sea's range that day + `perched_tol_m`, the range from
  the sea body's level histogram extended from the mode while bins stay populated) has its own
  connectivity and is never split (a lidar holds the sea at several tides on different lines: a step test
  shattered the bay into 400 bodies); above it, bodies split at 3x3 steps > `level_split_m` (0.3), a barrier
  cell joining a body within 3 cells; specks under `min_m2` dropped; kind sea/tidal by sea-set membership
  (the vendor class joins a lagoon pool to the sea across a TIN ramp, and the pool is not the sea).
- Edits: matched only inside the grid; an edit whose `kind` contradicts the observations is REPORTED and not
  applied unless `force: true` (the pool's Point, in a body merged with the sea, would have re-kinded 105
  ha of sea as a 1 m lagoon).

- **The mask feather never ramps into a layer's own class holes** (`post_masks`, Hig's v4 review): measured
  from the mask union its water/void/canopy holes, so a water hole keeps a hard edge and a seam between
  surveys still blends (a 30 m ramp into the lagoon pool had blended KBay's rim with OCM's -2.3 m).
- **Hig's hand-mapped lakes** (`water/sources.json`, `mapped_hig_lakes.geojson`): the detector's prior
  (`flat_lakes` "mapped": 100 m2 minimum, no ring test inside a polygon), islands for the hole rule, the
  mapped-vs-bodies report; next, mapped-only bodies with the level from the DTM.

Still open: Grewingk's 390 "marine" domains under the lens (regression run 2026-10-16, see the resume
block); the sea domains inherit the lake rule that drops ground within 0.2 m of the water as evidence
(48,542 cells at Seldovia, refilled at the surface -- harmless by construction, to watch); mapped lake at
+2.11 (body 571) now gets a basin; the fill pins raster bed observations inside a domain exactly (OCM at
1 m sigma reads as truth; the soundings spline weights by sigma and the rasters should too); the point filter reading the
observations (two passes); retreat by body; the observation file as a `/lidar/` overlay; season weights
once a lake has three surveys; Grewingk's `lake_dmax` as edits.

### Grewingk Lake, first attempt (2026-10-13)

Hig: build a Grewingk topobathy with the ice as Mark Laker's SfM shows it (19 Sep 2023), the ice face
VERTICAL and putting no constraint on the depth, the bed 150 m deep in the middle. Recipe
`composites/grewingk/recipe.json` (2 m, x 602-614 km, y 6605-6612 km), ice from `make_ice.py` (the 2020
G10040 outline minus what the lake had taken by 2023; face at x 609.19-609.50 km, matching the ortho).
In the dev viewer as `grewingk_topobathy_v1` (`tools/lidar/dev_viewer_add.py`, new, generic).

- **SfM is used for the glacier only** (Hig: SfM is bad over water and at the lake shore; measured here
  NMAD 2.9 m over the lake, +3.4 m above the lidar on land beside it): the dixon2023 layer is a copy
  holding only the 2023 ice and the retreat zone. Given everywhere with a canopy default it became the
  newest class over the lake and the lake got no basin. Lake level pinned to the 2021 lidar's 48.55 m.
- **Grewingk 2021's point classes are ~60% void over this box**: only their water is used; the lake is
  also taken from the DTM itself (flat at 48.55 +- 0.35 m, connected to the class lake), else its surface
  read ground along the face and the fill met a "shore" at water level instead of the ice.
- **fill.py, new**: `ice_face: "free"` -- ice rim beside a WET domain is neither shore nor boundary value
  (residual and spline free there; the solver skips such neighbours = no flux); ice inside a lake
  (bergs, the face fringe the closing wraps in) is dropped as evidence (as an "island" it pinned the bed
  20-30 m above the water); and the lake shape's distance-to-shore is to any non-ice land, as if the
  lake ran on under the glacier (to the land rim only, the shape tapered to the face). `lake_dmax:
  [{x, y, dmax_m, level_m}]` overrides one lake's deepest depth and level. **Lower bounds** get one
  active-set pass: violated bounds join the points at their bound and the domain is refitted.
  `channel_min_m2` huge in this recipe: channel detection read the deep east end as a river.
- **Result**: lake 4.8 km2, max depth 170 m by the face, mean 50 m, 0.26 km3; soundings honoured (Berg
  median 0.00 m, NMAD 0.29; lead line +0.15/0.56); bounds within 1.8 m (sigma 2.4). Face: middle
  42-72 m deep, its ends shallow where they meet the valley walls.
- **Open**: lake cells above the water (0.13 km2, to check); the TPS correction extrapolates toward
  the free face from soundings ~100-300 m off it; berg / glacier buoyancy constraints (Hig: later);
  the ice front moves (Hig: our face data is already out of date) -- a dated ice-front layer.


### Grewingk Lake v2 (2026-10-14)

Hig's review of v1: (1) Ed Berg's sounder track reads a bit high where it overlaps dry 2021 lidar -- drop it
wholesale; (2) one obvious bad point at 59.60800,-151.13920; (3) the track's run along the 2017 ice face
(59.60011,-151.07073 to 59.60665,-151.08217) reads unrealistically shallow, probably off-nadir -- make it
lower bounds; (4) the outlet bay is shallower than the fill made it -- use measured near-shore gradients
to inform the assumed one. Done:

- (1)-(3) in the soundings ingest (main, `tools/soundings/ingest_grewingk.py`): the level is 50.05 m, not
  50.5. On the RAW track, rows over 2021 lidar > 49.0 m (dry; the 2021 water is 48.55, 48.7-48.8 near the
  inflows) on ground < 5 deg: 152 rows, ~0.6 m deep, +0.46 m (NMAD 0.32) above the lidar, and all three
  overlap areas agree (west end +0.40, outlet +0.33, +0.67). Raw rows 21131-21135 (24-43 m after a 1.34 km
  position jump, on a shoal dry in 2021) rejected; the ice-face run (raw rows 41712-44273, 316 thinned
  points) loaded as lower bounds.
- KATIE'S DATUM in that light: on the multibeam's flat floor (~23 m, 122 raw rows, NMAD 0.06) Ed now reads
  1.30 m DEEPER than the multibeam (0.85 m at the old level); at 6-10 m they agree (+0.2, NMAD 1.0). A
  constant datum error in the multibeam would show at every depth; a ratio of 1.063 (Ed's depth / the
  multibeam's) is what a sounder reading ~6 % deep gives -- e.g. one assuming ~1500 m/s in ~2-4 C fresh
  water (~1420 m/s). Not applied: one depth band cannot tell a scale from an offset, and a 6 % scale would
  take ~5 m off Ed's 87 m and ~9 m off its 149 m readings. Settles with the sounder's speed setting, or a
  lead line dropped on the multibeam's floor.
- (4) `shore_grad_m` in fill.py (docstring SHORE GRADIENT): every depth within 200 m of shore (soundings,
  multibeam bed one per 10 m) gives rho = measured / prior depth; log rho is smoothed along the lake
  (sigma 150 m, 3 sites' weight on rho = 1) and multiplies the lake prior's depth within 200 m of shore,
  fading out by 400 m. 4,137 depths, rho p10/50/90 0.17/0.71/1.73. The outlet bay north of the
  cross-outlet line: bed 9.5 m (39 m deep) in v1, 40.8 m (8 m deep) now.
- Result (`grewingk_topobathy_v2` in the dev viewer): lake 5.08 km2, max depth 168 m, mean 50.8 m,
  0.258 km3; soundings fit median 0.00 / NMAD 0.23 (Berg), +0.05 / 0.28 (Higman 2019/21). Lower bounds:
  22 of 316 ice-face bounds still above the surface after the one active-set pass, most by ~1 m; worst
  30.8 m at the run's south end, where two ordinary 0.9 m readings sit 2 m from a 61 m bound -- the same
  off-nadir problem just outside Hig's endpoint. Above-water lake cells 0.151 km2 (0.13 in v1).

### Grewingk Lake v3 (2026-10-14)

Hig's review of v2, all in `grewingk_topobathy_v3` (dev viewer; an interim from the tiles then on disk was
`..._v3_interim`):
- Berg sounder depths x 1421.6/1500 (seawater speed of sound assumed, cold fresh water): now agrees with
  the multibeam floor to 0.2 m (was 1.3 m deep); level re-tied to dry 2021 lidar, 50.01 m.
- Whole composite +0.36 m to the other DEMs: grewingk2021 and sonar2023 layers shift_m 0.36, lake level
  48.91, fill `points_dz_m` 0.36 (the soundings database stays in the published lidar's datum).
- 2021 DTM rebuilt from the classified points without hydroflattening (make_nohf_dtm.py; all 194 tiles
  over the box range-fetched from the DGGS zip). Water-classed returns > 0.65 m above the lake in patches
  >= 50 m2: 13 found, only Hig's island (59.60571,-151.11924) kept -- the rest sit where Ed reads 8-74 m,
  i.e. October 2021 icebergs; listed as candidates in obs/nohf/islands_2021.json.
- Ice where the 2021 lidar stands > 5 m above the 2023 SfM, connected to the glacier (rock near it reads
  -0.9 m median, outlined ice +11 m): +0.32 km2 ice on the south edge, and the lake's SE corner (2023
  surface at the water) as retreat.
- Deep-bed seams: the shore-gradient factor is smoothstep-blended and smoothed over the lake; and its
  shoreline window WIDENS with distance from shore (Hig: one cliff patch had big effects down-slope), as
  does the land rim slope in shore bands (`rim_widen`).
- Result: lake 5.10 km2, max 170 m, 0.248 km3; Berg fit 0.00 / NMAD 0.22, lead lines within ~0.1 m;
  above-water lake cells 0.12 km2; 30 ice-face bounds still violated (most < 1 m, worst 31 m at the run's
  south end). Open: radial streaks in the basin shape's first few hundred metres (distance to the nearest
  shore cell -- the widening window would apply there too, Hig to decide).

### Grewingk Lake v4–v5 (2026-10-14)

v4: the SE-corner patch Hig marked (HAND_LAKE seed, low-drop retreat) and the rim residual capped at the lake
level. v5, Hig's review of v4 -- with the instruction to solve each thing as the general case, not tune one basin:
- **Stale class raster (general trap).** The 2021 point classes were built (2026-10-11) before the tiles north
  of y 6610.5 km were fetched; "no returns + flat DTM = water" turned the outwash gravels there into ponds.
  observe_points.py now records unfetched tiles' footprints and `classify` voids them; rebuilt over the whole
  box (obs/grewingk_2021_box). Outwash water 22 % -> 1.6 %.
- **A hydroflattened lake is exactly flat.** make_ice's lake_2021 was "within 0.35 m of 48.55 and connected",
  which took gravel at 48.2-48.9 m; now also a 3x3 range < 2 cm. Same idea, generalised: flat_water.py finds
  hydroflattened water in any bare-earth model (3DEP: Emerald Lake, nine other patches), used as a fallback's
  water class so a lake where 3DEP is the only data gets a basin.
- **Agreement means detail, not level.** The last of the SE patch: 2021 ice barely above the water, where the
  2023 SfM over water (smooth, a few metres high) matched it in level. A later survey confirms an earlier
  surface only if it reproduces its detail (surface minus an 8 m gaussian, correlated over 18 m): shore rock
  0.9, 2021 ice now under water 0.05. Retreat where < 0.4 in the 2023 water band, connected to retreat or ice.
- **Feather underlay (composite.post_masks).** A feathered edge had nothing to blend with where the layer
  below is a fallback shown only where its rules took it, so the lidar edge was a hard step. The nearest lower
  layer with data, within reach of its own take, is now laid under any partial edge; feather 15 -> 30 m.
  Gradient p99 at the lidar edge 4.2 -> 2.7.
- **A DSM as bare ground (dsm_bare.py).** Beyond the lidar the 2023 SfM rather than 3DEP -- but it is a DSM:
  0-0.5 m off the lidar on bare ground, 3-7 m high in forest and alder; the ortho's greenness does not find
  the canopy (dark spruce, shadow). The DSM's 30 m median against 3DEP does: < 1 m above = bare (matches the
  lidar, -0.3 m), > 2 m mostly canopy. SfM where bare (8.2 km2 beyond the lidar, levelled -0.24 m, NMAD
  0.78), 3DEP under canopy (5.3 km2), never over water or ice.
- **Hypsometry as a lake constraint (fill lake_dmax `hypsometry`).** Emerald Lake's sounder data (NAU,
  via Ed Berg 2017) is likely corrupted (Hig): not conditioning, only its deepest depth and area-depth
  distribution (nearest-sounding area per 10 m track cell). The lake profile's depths are remapped by rank
  onto it -- shape from the profile, the share at each depth from the data. Result: 0.373 km2, max 50 m,
  mean 24 m, 9.1 Mm3.
- **A lake's modelled bed never stands above its water.** The shore residual + spline overshoot put a
  10-30 m fringe below the SE cliff 0.5-5.6 m above the water; clamped (0.18 km2 box-wide, incl. an upland
  pond at 59.58333,-151.11495 whose whole bed sat above its level -- worth a look).
- v5: lake 4.90 km2, max 175 m, 0.246 km3, above water 0.001 km2; Berg fit +0.01 / NMAD 0.37 (was 0.22 --
  look at), lead lines +0.2 to +0.6; ice-face bounds: 45 violated (most < 1 m, worst 57 m at the run's
  south end, which sits on rock in the lidar). Open: creek channel work; glacier "lakes" at 550 m and
  higher; whether the 3DEP flat patches at 840/448/364 m are all water.

### Grewingk Lake v6 (2026-10-14)

Hig's review of v5:
- **KBay 2023 (kbay_2023_reclass_v2) added; the LOWER lidar wins where they differ.** It covers the SW half of
  the box. On ground it agrees with the shifted 2021 (median -0.05 m), but 2021 stands > 1 m high on 4 %: bad
  low-density returns 11-13 m high at 59.62226,-151.18886 and in a 2021 data hole at 59.58333,-151.11495
  (the v5 "upland pond"). kbay2023 sits below grewingk2021, which has the opt-in `lowest` rule (takes only
  where lower by > 0.3 m over 50 m). Both spots now read KBay.
- **Ponds carved into the outwash: a feedback loop in the fill.** Ground within 0.2 m of a pond's level was
  judged "water surface" -> fill_surface.tif -> water class at the next build -> a larger pond. That rule
  is for stray returns off calm water, which lie out on the water: a patch whose edge borders more dry land
  (not ice) than water is now shore and stays ground. Outwash water 0.156 -> 0.052 km2; what remains is
  where class 9 outnumbers ground (the creek itself).
- **Low "islands" are the water.** Inside a lake, a patch whose MEDIAN stands < 0.65 m above the water
  (make_nohf_dtm's island threshold) is surface. Removed the last SE sliver (112 m2, 0.2-1.1 m above the
  water; the maximum let it through once) that had pinned a 9,000 m2 flat bed below the cliff.
- **Tried and reverted: shores keep the band** (the deeper of the basin shape and the land slope carried on
  under the water, as the fill's design note says but never coded). It made the cliffs plunge, but max depth
  went 173 -> 245 m and the 2026 lead lines +1.3 -> +4.8 m. The ~4,500 m2 shallow strip below the SE cliff
  (59.59976,-151.06609, no soundings within 150 m) remains; its prior is only 2-3 m deep there.

### Kachemak test: Peterson Bay – China Poot Bay – Halibut Cove (2026-10-10)

`composites/kachemak_pedersen/recipe.json`, Hig's choice of a setting with
several datasets, partial overlap and real gaps, centred on
59.57368 N, −151.30133 W. **First recipe from `datasets.json` ids on a
stated grid**: `source` is a dataset id (→ archive COG, already NAVD88
with its `vertical_shift_m`), `grid` is `{"epsg": 6334, "res_m": 1,
"bounds": [...]}` snapped outward to whole metres (x 595–606 km, y
6602–6610 km, 88 Mcells; a full build is **54 s**). Layers in fold
order: `kachemak_bathy_2009` (Hydropalooza, class `bed`, no TIN hull to
reject), `grewingk_2021`, `kbay_2023_reclass` (the reclass, not the
vendor DEM: that one's compound NAVD88 CRS trips the gdalwarp geoid
shift). The three-way overlap is around Halibut Cove; China Poot Bay is
lidar + bathymetry only. Outputs and 1/8-scale previews in
`$LIDAR_BUILD/composite/kachemak_pedersen/out/`; entry
`kachemak_pedersen_topobathy` (dev_only) in `datasets.json`.

What the site taught, in order of importance:

1. **Grewingk 2021 sits ~0.36 m LOW in the collection's frame.** On bare
   smooth flat ground (both DTMs < 0.05 m rough, slope < 3°, 2–20 m)
   KBay − Grewingk = **+0.36 m, NMAD 0.06** (n 108k at 2 m); KBay −
   Seldovia 2019 NCMP topobathy (the survey the bathymetry's datum was
   tied to) = +0.014 m, NMAD 0.048; the reclass equals the vendor DEM to
   1 cm. Two independent products against one; no third overlaps it
   (Homer 2019, Lower Kenai have no data over Grewingk). Both say
   NAVD88 (GEOID12B) — the [[reference_navd88_geoid_model_trap]] again.
   Applied in the recipe as an explicit, reported per-layer **`shift_m`
   +0.36** (new; the stop-gap the plan allows: "applied only if the recipe
   says so"); **the real fix is `vertical_shift_m` on `grewingk_2021` in
   `datasets.json` + an archive rebuild, which is hosting's call after
   its own check.** With it, KBay − Grewingk on all ground overlap falls
   from +0.51 to +0.15 m (NMAD 0.41 — canopy and change, `lowest`'s job).
2. **A lidar's sea surface comes at several tide levels.** KBay holds the
   water at −3.0…−1.9 m NAVD88 on different lines (exactly flat,
   roughness 0.00–0.02 over the deep bay) and the reclass's own harmonic
   fill of the lagoons at +0.8 m (flagged in its `fill_mask.tif`, 2 =
   filled). The single-mode `water_level` detector cannot see that, so
   two class steps were added, each because this site failed without it:
   **`flat_below`** (flat below a level cap, opened, min area — low land
   at 1–4 m is ~0.6 m rough, water ~0.02, so they separate) and
   **`raster`** (class from a categorical raster that travels with the
   observation: the fill → `modelled`). The existing `snow_vs` detector
   gained an `as` option so it also classes a lidar surface that is
   > 1.5 m above a known bed as water. Result: KBay 86 % ground, 9 %
   water, 5 % modelled; the bay is bed, the channels and shore band
   water, the lagoons modelled with the Hydropalooza lines showing
   through as bed.
3. **Equal-quality surveys need `margin_m`** or the newer one wins
   everywhere by its 0.001 m smaller σ_t and `lowest` never decides.
   `margin_m: 0.05` in the recipe; with it Grewingk keeps ~30 km² and
   KBay replaces it only where lower by > 0.3 m at 50 m scale.
4. **The two things still visibly wrong are the two things the plan
   has not built yet**, now with a real site to build them on: the
   **intertidal gap** (the composite shows the lidar's water surface as a
   flat shelf at the flight tide, stepping down to the 2009 bed — the
   Seward "ghost shoreline", here with 1.98 km² flagged `fallback`), and
   the **lagoon fill** (a flat modelled surface at +0.8 m cut by sparse
   sounding lines — the sparse-point interpolation's case, paused). Both
   are honest in the diagnostics (`fallback.tif`, `class_now`), neither is
   hidden by a blend.

**Point-cloud classes (2026-10-10, later).** Hig: the DEM-derived
inference is the wrong level — classify from the point cloud (ground,
water, snow/ice, others if useful), and the reclass's flat lagoon fill is
meaningless: mask it out and interpolate the Hydropalooza instead.
`tools/lidar/observe_points.py` is the observe stage from the point
cloud: `stats` bins a survey's points (PDAL per tile → uncompressed LAS
1.4 → numpy; KBay from the 2,298 COPC tiles on `/Volumes/Powder/
lidar_src/kbay_2023_laz/` — all local — Grewingk from the DGGS LAZ tiles
on Nunatak) into `obs/<id>_pts/cellstats.tif`, 16 evidence bands at 2 m
(returns by class, lowest-return z per class, intensity per class, the
flatness of the lowest surface); `classify` applies a per-survey rule
set (`RULES`, printed with its outcome) to write `class.tif` in topobathy
codes, which the recipe reads with `{"raster": {"path": …, "codes":
true}}`. What the points said:

- **KBay 2023 (NIR, 1 Aug, leaf-on): no returns IS the water signal.**
  Over 7.5 km² of lagoon and bay the vendor's class 9 holds only 44 k
  points; the laser simply gets nothing back, and the reclass filled
  those holes. Rules: ground where the reclass DTM has a value and the
  cell has returns; **void** where there are no returns (opened, blobs
  ≥ 50 m²) and wherever the reclass's `fill_mask` is 2; water where
  class-9 returns outnumber ground — checked against the evidence
  bands: those cells are flat (0.05 m vs 0.23 for ground), sparse (14
  vs 48 returns) and sit at −2.9…−2.3 m (sea, 1.18 km²) or 50 m (the
  lake, 0.77 km²), so NV5's over-reaching water polygons are not biting
  here (0.07 km² in the suspect 0.5–5 m band); built where class 6
  outnumbers ground (the structures the reclass kept as ground); snow
  from class 21 (none on the 2023-08-01 lines). Result 96.5 % ground /
  3.4 % water / 0.1 % built of classified cells, 27 km² void. In the
  composite the lagoons are now **holes with only the sounding lines in
  them** ("none" 0.27 → 2.39 km²), the shore band is bathymetry up to
  where the lidar has ground, and `fallback` all but vanishes. The
  DTM-side `flat_below` / `snow_vs` steps are no longer used on KBay;
  they stay in the engine for surveys without points.
- **Grewingk 2021 (NIR, 12 Oct): no snow signal in the classes.** With
  36 of 119 tiles local the vendor's ground class seemed to stop at
  ~830 m, and `rules_grewingk` carried a snow hypothesis for DTM cells
  with only unclassified returns. With all 119 tiles (the other 83
  fetched 2026-10-10; `fetch_one.sh` compared the CRC against the wrong
  column of `ranges3.txt`, the inflated `.part` files were verified
  against the right one and renamed) the ground class covers 93–99 % of
  DTM cells in every 100 m band to 1100 m, and the rule finds nothing.
  Result 94.9 % ground / 5.1 % water (DGGS class 9). Snow on
  2021-10-12, if any, is inside the vendor's ground class and only a
  reference surface (the `snow_vs` step) or Hig's knowledge of the
  season can find it; intensity drops ~20 % above 900 m, which is
  ambiguous. The Grewingk layer now reads its `class.tif` like KBay's.
- A free measurement from the class-by-class report: **the lake at
  Halibut Cove sits 1.07 m higher in KBay (Aug 2023) than in Grewingk
  (Oct 2021), NMAD 0.02 m** — the seasonal-level reading, which the
  water class now isolates from ground.

### The reclass as the classification (2026-10-10, evening)

Hig: use *our* reclassified points, not the vendor's classes, and tweak
the reclass where it is wrong — (1) structures should be their own
ground class so the DTM can be planed under them; (2) bad returns over
water survive as topography (six sites given), but do not over-filter
the water surface: real water returns matter in running water and for
shorelines that hydroflattening moves.

The reclass (`kbay_reclass_test/model/`, hosting's product
`kbay_2023_reclass`) never wrote relabelled points — it writes surfaces
per 1 km unit — so "our points" means the labels its chain assigns, and
the observe stage reads them through the surfaces and masks the chain
now writes. What the six sites held, probed at full density
(`observe_points`-style pulls from the COPC tiles):

- **Three of the six "bad return" blobs contain no returns at all.**
  They are the chain bridging return-free water: TIN facets up to 30 m
  (A, a 413 m tarn), the zone solver extrapolating over a pond with no
  water class at all (D: +5 m on the rim, then the harmonic fill
  inherited the rim, 22–40 m over a 17 m pond), a facet reaching
  **592 m in a 528 m lake** (C).
- **Real floating returns**: single returns of ground-like intensity
  0.5–5 m above the water in vendor classes 1 *and* 2 (the vendor's own
  ground holds them), over tarns (A: 427 class-1 returns at +1.4 m over
  600 m² with 5 water returns), in bays (602361,6608610: 530 returns
  1.2 m over the sea), a boat (E, 15 m, +1–2 m), the shrub fringe of a
  lake (C's north shore: both flights see it, 1 m apart in height).
- **F is the other flight**: the 2024 water at +0.23 m admitted as
  water in the 2023 water's holes (RULE 2) while the 2023 water sat at
  −2.76 — 3 m islands of the higher tide.
- **An islet at C is real**: both flights put it at the same height
  (83 % of cells within 0.3 m). The vendor flattened it away.
- **Structures**: at Seldovia 108 of the 118 blobs the reclass raised
  over the vendor are vendor class 6, and 65 of 551 class-6 buildings
  were lifted — the larger roofs passing the rock rule, as the README
  guessed. Only 3 raised smooth blobs lack a class-6 label, so the
  vendor's building class is a good structure source; at Halibut Cove
  the TIN already passes under every class-6 building.

**Changes to the chain** (live copy on Nunatak; backup
`model_backup_20261010/`; the repo copy `tools/lidar/kbay_reclass/model/`
on `main` is hosting's to sync):

- `EXCL` gains **6 and 17** in all 17 modules: a roof is never a ground
  candidate, the TIN passes under it. `apply.py` writes `struct_mask`
  (class 6/17 cells, closed) and `fullrun.py` keeps it.
- **`model/water.py`** (new): a local water **level** W from classes
  9/20 (sigma-clipped mean, nearest-neighbour to 20 m; a median needed
  most of its window to be water and gave the tarn no level);
  **floating** returns → class 7 by two rules — (a) 0.5 m above the
  lowest return *in its own cell* when that return is at the water
  (a 3×3 reach stripped a 3–5 m ring off every bank steeper than
  ~10°), (b) 0.5 m above W with ≥ 60 % of the 9×9 water or return-free,
  iterated so a field of floating returns peels inward; **persistent**
  returns (class 22 within 0.3 m in the cell: the other flight saw it)
  are never flagged — that is what keeps the islet; **void**: a water
  cell (W defined, 5×5 floor at the water or empty) with no kept return
  within 3×3 gets no surface, so nothing bridges it, and the enclosed
  fill then fills it from a rim at the water level (`fill_mask` 2).
  `water_mask.tif` and `water_level.tif` are written per unit.
- `admit_low_tide` RULE 2 takes W: class-22 returns are admitted only
  at or below W + 0.3 — the other tide stays excluded (F).
- **A ceiling under the solver** (`sitekit.return_ceiling`): ground is
  never above a return in its own cell (+0.3), nor 2 m above the 5×5
  floor over a return-free cell; the mirror of the 2026-09-26 floor
  (D).
- `mosaic.py` mosaics the three new rasters beside the DTM (units from
  before read as 0 / nodata).
- What is **not** filtered, by design: dense objects over water whose
  own cells show no water — boats and docks (Seldovia harbour) stay,
  as a structure would; rocks under ~4 m in open water are lost unless
  the other flight saw them; the one cell straddling a steep bank
  loses its upper returns.

**Observe stage**: `observe_points.py classify` takes `--struct`,
`--water`, `--level` — built from the reclass's structure mask, water
where its DTM sits at its water level — so the composite's classes come
from our chain, with the vendor's class 6/9 counts as the fallback.

Verified on the seven test units (before/after against `full/out`, the
pictures in the session scratchpad): A, C, D, F fixed; E's dome gone and
the boat left as a bump; Seldovia's roofs planed under (struct mask 4 %
of the unit); 0.1–1 % of each unit changed, nearly all of it lowered and
in water cells. **Hig, 2026-10-10 13:10: rerun the whole survey.** Every
old unit output is in `full/out_v1/` (never deleted); `fullrun.py
--workers 3` with `FULLRUN_FIRST="595 6602 606 6610"` (a new ordering
hook: those units first) is running, log `full/run_v2.log`, resumable
as before. Then `mosaic.py --bbox 595 6602 606 6610 --out
full/mosaic_kachemak` for the composite (a waiter does it), and the
whole-survey `mosaic.py` + republish of `kbay_2023_reclass` on the
hosting side when all 1,350 are done. The repo copy of the model under
`tools/lidar/kbay_reclass/model/` is stale until hosting syncs it.

**Composite rebuilt on the new reclass (2026-10-10, evening).** The
box mosaic (`full/mosaic_kachemak/`: 74 units, 9 enclosed holes — the
lagoons — filled from their water-level rims, 3.1 km²) is the KBay
layer's source until the republish (recipe `source` is its path, with
a note to switch back to the id). Observe rules, both surveys: a fill
or a return-free DTM cell that sits flat at the water level is classed
**water**, not void — it is the water surface, a stand-in the bed
outranks (KBay from the reclass's `water_mask`/`water_level`; Grewingk
from DTM flatness, since DGGS hydroflattened the lake and the laser saw
13 % of its cells). KBay now 94.2 % ground / 5.7 % water / 0.1 % built;
Grewingk 91.0 / 9.0. Composite: bathy 25.7, Grewingk 30.2, KBay 30.2,
none 1.9 km²; no fallback cells; water-class overlap 0.84 km² with
KBay − Grewingk **+0.97 m** (the lake's level, Aug 2023 vs Oct 2021);
ground overlap +0.15 m (canopy residual on top of the 0.36 m shift).
Previews in `out/preview/`.

**Water is not a surface (Hig, 2026-10-10, night).** "A concept we want
in the first version is that water is not a real surface — it's
topobathy after all. Eventually we'll want a way of also generating a
water surface, but that would be more in line with glacier surface
evolution." So in `ground_current` a water observation never stands in
for the bed (the `water: 0.5` gate entries are gone), and both folds
lost their *last resort* (the newest observation of any class, flagged,
where nothing compatible existed — written for exactly this case). Where
only water was seen the composite is a hole whose class is `water`: bed
unknown, the place the bathymetry interpolation fills next. Kachemak now:
bathy 25.6, Grewingk 28.6, KBay 29.3, **none 4.4 km²** (was 1.9), no
fallback cells; the lake, the lagoon interiors between sounding lines,
the tidal channels and the ponds are the holes. A water-surface product
(level per water body per date, from the water class) is a later, separate
layer.

### Hole interpolation: a context-aware first guess (2026-10-10, night)

Hig: "where we're aiming here is a context-aware first-guess ... first
create a synthetic DEM for the interpolation — in many cases it might be
a simple smooth interpolation, but for shorelines we want a shore-like
transition: smooth shore-parallel contours where there are beaches, and
steep cliffs extending down to low-tide where there is bedrock."

`topobathy/fill.py` + `composite.py fill <recipe>` (module docstring is
the specification; recipe block `fill`, every parameter overridable
with `--param k=v`):

1. **Domain** — the composite's holes, closed over the sounding lines
   that cross them (60 m, done as two distance transforms — a
   structuring element that size on 88 M cells never finished), so a
   lagoon is one domain with its lines as observations *inside* it.
   Holes touching the grid edge are margin and stay empty (1.34 km²
   here, mostly the bay edge and the lake in the corner).
2. **Rim character** — each land-rim cell's slope over the 10 m behind
   it (p10/50/90 here 1.8 / 8.7 / 36°): beach or cliff is that number.
3. **Prior, the synthetic DEM** — nearest-rim extension z = z_rim −
   tan(slope)·d along distance-to-shore (contours are iso-distance, so
   shore-parallel), smoothed with a distance-growing kernel, down to a
   **floor**: the low-tide level (`low_tide_m`, −2.9 here = the lowest
   sea surface the KBay water class shows, standing in for MLLW) for
   marine domains (land-rim median < 6 m), rim − `lake_depth_m` for
   lakes. That band is fixed; the interior is harmonic between the
   band's inner edge and any bed rim, *with the sounding cells as
   unknowns* — the prior ignores soundings by design, and leaving them
   out of the system made pockets ringed by lines boundary-less (the
   solve wandered to +30 m before that was found).
4. **Residual** — r = sounding − prior at sounding cells, 0 on the rim,
   harmonic between; fill = prior + r. Soundings are honoured exactly.
5. **Sigma** — grows from the rim (1 m + 2 m per 100 m, + 5 m for
   lakes) and relaxes toward 0.3 m within ~30 m of a sounding.

Solver: geometric multilevel — direct at 16 m, conjugate gradients at
8/4/2/1 m warm-started from the level above; 2 M unknowns in 8 s,
harmonic to machine precision (a cold CG at 1 m sat at its mean after
300 iterations). Kachemak: 652 domains, 3.08 km² filled in 2.5 min
(band 0.64, interior 2.44, 2.85 conditioned), σ p50 0.95 m. What it
looks like: Halibut Cove Lagoon's −60 m basin between the lines with
the beach band grading off the west shore and a steep face off the east
rock; the Peterson Bay lagoon as a bowl from its beach rims. Outputs
`out/fill.tif`, `fill_prior.tif`, `fill_mask.tif` (1 band, 2 interior,
3 conditioned, 4 margin left), `fill_sigma.tif`, `<id>_filled.tif`
(COG), `fill.json`.

Open: the sounding lines' own gridded width still reads as a faint
texture in the filled hillshade; roughness is not yet used to separate
rock from beach (slope does it here); lakes get a guessed floor; the
filled surface is a sibling product, not yet a layer in the gate (it
could enter as class `modelled` with `fill_sigma` as its σ).

**The interpolation is a layer (2026-10-10, late).** Hig: "there should
be a layer representing the interpolations" — and the black on the
composite hillshade was the holes themselves. The recipe now carries
`{"name": "fill", "optional": true, "source": "$out/fill.tif", "class":
"modelled", "sigma": [{"raster": {"path": "$out/fill_sigma.tif"}}]}`:
`$out/` resolves to the composite's out_dir, an optional layer is
skipped (with a note) until its file exists, and `composite.py fill`
treats the cells the fill layer won as holes again, so build → fill →
build is idempotent. Two gate changes were needed: a `modelled`
observation never sets `class_now` (it is derived, not an observation
of what is there — with it on the class axis the fill blocked itself,
since the table has no `modelled` row), and a `void` row admits
`modelled` at half rank (where the laser got no returns at all, only a
modelled surface may stand in). Kachemak: fill 1.63 km², bathy's own
hull TIN 26.7 (it outranks the fill between the lines, gate 1 vs 0.5),
holes 1.70 km² of which 1.34 is margin. The mask tool shows `fill` as a
layer like any other, plus the two fill views. Open question: between
sounding lines the bathy layer's linear TIN beats the context-aware
fill because it is class `bed`; whether that is the right ranking is
Hig's call (the fill could be given gate 1 under `water`/`void` where
the bed observation's σ is the hull's).

### Default lake-basin guess: the literature (2026-10-10, late)

Hig: "one thing we should start building is a default lake-basin
guess — research the literature on lake basin geometry inference."
What exists, and what it gives us:

- **Hollister, Milstead & Urrutia 2011** (PLoS ONE 6:e25764; R package
  `lakemorpho`): depth at a point = distance from shore × median slope
  of the surrounding topography, in a buffer as wide as the lake's
  greatest in-lake distance to shore; max depth = the farthest point.
  Raw predictions run high — the fitted corrections are **0.46–0.55**
  (basins are shallower than their shores extrapolated, by erosion and
  sedimentation) — and after correction RMSE 5–6 m, r 0.7–0.8,
  explaining 50–60 % of max-depth variance over ~28,000 NE-US lakes.
  This is exactly our shore-band extension, with the factor.
- **Sobek, Nisell & Fölster 2011** (Can. J. Fish. Aquat. Sci.): 6,943
  Swedish lakes; lake area and the **maximum slope in a 50 m zone
  outside the shoreline** are the best predictors — 92 % of volume
  variance but < 40 % of max or mean depth; regional sub-models alike,
  "probably applicable for glacially formed lakes in general".
- **Heathcote et al. 2015**: elevation change in a buffer scaled by lake
  area explains 95 % of volume variance for 433 Quebec (glacial) lakes.
- **Messager et al. 2016** (HydroLAKES, Nat. Commun.): a geostatistical
  volume model from surrounding terrain for every lake > 10 ha.
- **Khazaei et al. 2022** (GLOBathy, Sci. Data): max depth by random
  forest on shoreline length, area, volume, watershed area and
  elevation (test NSE 0.86, n 1,503); the bathymetry itself is
  **D = l·Dmax / L** — depth linear in distance to shore, Dmax at the
  farthest point (a cone, Hollister's shape); head–area–volume as power
  laws A = a·hᵇ, V = c·hᵈ.
- **Håkanson 1981** (A Manual of Lake Morphometry): basin shape in one
  number, the **volume development Vd = z̄/z_max** (a cone is 1/3); a
  hypsographic form parameter Hd fits area–depth curves to r² ≈ 0.99.
  For a basin whose depth goes as (l/L)ᵖ, Vd = 2/((p+1)(p+2)): p = 1 is
  the cone, p ≈ 0.56 gives Vd = 0.5 (flatter-bottomed, the glacial
  norm), p → 0 a box.
- **Cook & Quincey 2015** (Earth Surf. Dynam. 3:559): Alpine glacial
  lakes — volume–area r² 0.91 (power law, Huggel et al. 2002's
  V = 0.104·A^1.42 is the usual form), depth–area only r² 0.38; the
  outliers are overdeepened, complex basins — our case.
- **Haeberli et al. 2016** (ESPL 41:1980): overdeepenings sit where
  glacier surface slope was < 5–10°, are hundreds of metres long, about
  half as wide, tens of metres deep, with the adverse slope on the
  down-flow side.

**Design that falls out of it**, as the lake branch of `fill.py` (not
built yet): (1) **Dmax** two ways — Hollister: (max distance to shore)
× (median rim slope) × ~0.5; glacial scaling: mean depth from area
(Huggel/Cook & Quincey), Dmax ≈ mean / Vd — and the spread between
them is the depth σ; (2) **shape**: depth = Dmax·(l/L)ᵖ with p from a
target Vd (0.5 default for glacial basins, 1 for a cone), Dmax at the
farthest-from-shore point — or, where the rim says which way the ice
came (highest rim side = headwall), shifted toward it, per Haeberli;
(3) **shores** keep our band: the deeper of the rim-slope extension
(capped at Dmax) and the shape model, so rock drops steeply and beaches
grade; (4) soundings condition it exactly as in marine domains; (5) σ
of order 5 m plus the Dmax spread — honest about how little the shore
knows. The Halibut Cove lake lies in the box's corner (a margin hole);
testing it needs a window that encloses it.

**Continuous coverage, and the lake branch roughed out (2026-10-11).**
Hig: "our target should be entirely continuous coverage"; some holes
"look more like a bug". They were three bugs: (1) the `rules_post`
despeckle/feather applied to the fill's take (ponds < 2000 m² and
channels narrower than the feather vanished) — a layer may now set its
own `rules_post`, the fill's is `[]`; (2) domains touching the box edge
were "margin" and never filled — `fill_margin` (default true) fills
them, the grid edge a no-flux boundary; (3) two surveys' takes over a
pond were complementary speckle, both despeckled, and the hole admitted
no fill under a `ground` class-now — post-processing may now never
leave a cell uncovered that the rules had covered (it reverts to the
rules' winner). Also the `void` gate row admits `bed` (the bathy's own
hull TIN is still the best where the newest survey saw nothing; without
it the fill displaced the hull between passes and the hole set never
converged). Kachemak: **fill 4.35 km², none 0.00 km²**, stable under
fill → build → fill → build.

Lake branch (`fill.py`, rough for review): per lake domain ≥ 2000 m²,
L (greatest distance to shore), A, median rim slope; Dmax = geometric
mean of Hollister's 0.5·L·tan(s) and the glacial scaling (Huggel
V = 0.104·A^1.42 → mean depth ÷ Vd 0.5); shape Dmax·(l/L)^p with
p from Vd (0.56); shores keep the band (deeper of band and shape);
σ = half the Dmax spread + 5 m, scaled by the shape; observations inside
a lake within 1 m of the water level are dropped as evidence (a boat
track or seam of returns off calm water, classed ground, had pulled the
fill up to the surface), and islets of such cells < `island_max_m2`
are swallowed into the domain (each had been a rim throwing a cone).
**Sparse point depths**: `fill.points` = a CSV with x,y and z (absolute)
or depth (below the lake's rim level) — they condition the prior exactly
like soundings. `out/fill_lakes.json` is the per-lake table. Halibut
Cove lake: A 0.89 km² (cut by the box edge), rim 5.4°, Dmax Hollister
25 / glacial 66 → 41 m; the south lake at 601301,6602221 (also cut):
rim 34.5°, Hollister 144 / glacial 47 → 82 m — Hollister's factor is
not a cap, and the geometric mean only tempers it. Both big lakes are
truncated by the box; a review area that encloses whole lakes (extend
NE and S, or a lake-rich box elsewhere in the Grewingk footprint, at
2 m) is the next step, with a few hand depths in the CSV to exercise
the conditioning.

### Deferred: physical awareness (decided 2026-10-09)

Hig asked how far to go toward a physically aware merge — glacier
thinning rates from overlapping DEMs of different age to put a glacier
at one epoch; seasonal water levels. Decision: **not now, and nothing
needs to change in the engine for later.**

- Thinning is a **derived layer** (shift an ice observation to `as_of` by
  a rate raster, σ growing with the gap) — the slot already exists
  (`plane_shift`, "local dh/dt from the glacier apps" reserved). Current
  behaviour on ice — newest observation wins, a feathered step at each
  seam of ~rate × age gap — is an honest picture of what the data knows,
  and `date_now.tif` makes it legible. The trigger to build the layer is
  a product that fails without it (the ice-dam flotation analysis, where
  a 10 m surface step is ~9 m of water level); the first implementation
  should use a **published** rate field (Hugonnet et al. 2021 100 m dh/dt
  grids, 2000–2019, include Alaska — not verified in session) or a dated
  ArcticDEM strip, not our own estimator. Self-estimating from overlaps
  mixes thinning with season, snow, IfSAR penetration and co-registration,
  and needs rates where there is no overlap — a geodetic mass-balance
  pipeline. `report.json`'s `rate_m_per_yr` tells whether the seam step
  at a site is 2 m or 30 m before anything is modelled.
- Seasonal water: each survey's water is detected at its own level and a
  ground observation outranks a water one, so the lowest-water survey
  already wins the shore band. What remains is one product level per
  waterbody (to flatten stand-in water) and the tide level at sea sites —
  recipe scalars, not a model.

(Previous state of play, 2026-09-20/21, for the record:) This document
is the agreed design, reviewed by Hig over four drafts on 2026-09-20;
the decisions below are his, not proposals. **Revision 2** (next
section) and the dated sections at the end supersede the original where
they conflict; the "Revised order of work" in Revision 2 is current. The
Portage regression (its step 1) is done and bit-exact. The sparse-point
interpolation is paused with its own state-of-play section below.

Decisions taken, in the order they were made:

1. The composite is a **layer stack × mask stack, folded bottom-up** as
   `out = out·(1−m) + new·m`. This is Hig's own Portage method generalised
   (see "The Portage example"). Everything else is a way to make a layer or
   a mask; nothing edits the output.
2. **Masks are the hand interface.** 8-bit greyscale GeoTIFFs, auto and
   paint kept separate so regeneration never loses paint. **The paint
   raster is the record**; brush strokes are transport and session undo,
   never a versioned log (Hig: a gesture log becomes a second format to
   keep compatible as tools multiply).
3. The painting/selection UI must be **smoothly responsive**, so it runs
   local and offline against the dev server with the stack mounted, syncing
   to the repo at whatever cadence. Prod never hosts it. It is tier 3; the
   first composites use Photoshop/QGIS round trips through
   `import-mask`/`export-mask`.
4. `lowest` is the headline automatic generator (several good DEMs → the
   lower surface sees under canopy and snow), with **real change** handled
   as candidate blobs a human accepts or rejects.
5. The hand UI is a **Photoshop-idiom mask editor**: magic wand on any
   raster (especially the difference), feather by a distance, push the
   selection to 0 or 1. "Put the user in intimate interaction with the mask
   image." The brush is secondary.

Environment facts a fresh session needs (all verified 2026-09-20):

- Repo: `~/Claude_projects/landslidescience`. Pipeline: `tools/lidar/`
  (`build_lidar.py`, `make_catalog.py`, `datasets.json`); state of play in
  `tools/lidar/RESUME.md`. Archive COGs live on
  `/Volumes/Nunatak/lidar_build/cog/`, never on the droplet.
- Python: `/opt/anaconda3/bin/python3` (numpy 2.3, scipy 1.15, rasterio
  1.4; no pyproj/osgeo). GDAL: `/opt/homebrew/bin` 3.13 with
  `PROJ_NETWORK=ON` — **not** the QGIS GDAL 3.3 that is first on PATH.
  PDAL is on PATH (Homebrew).
- The viewer's **Difference** control on `/lidar/` is the review loop for
  tier 1: any dev-only composite can be differenced against any source.
- Bathymetry already in the collection, all NAVD88 via `vertical_shift_m`:
  lituya_2023, lituya_2025, resurrection_2016, resurrection_2024,
  kachemak_bathy_2009, seldovia_2019 (topobathy), grewingk_sonar_2023
  (gated), pedersen_2025. MLLW→NAVD88 routes are in `RESUME.md`.

Prior art read for this plan (all four reduce to numpy/scipy; none runs
outside its host):

- USGS DEMFusion — https://code.usgs.gov/spcmsc/DEMFusion/ (ArcGIS Pro
  notebook; adaptive weight surface).
- Cushing & Tyler 2024, *Mitigating disparate elevation differences between
  adjacent topobathymetric data models using binary code*, Remote Sensing
  16(18):3418 — https://www.mdpi.com/2072-4292/16/18/3418 (CoNED per-pixel
  state rules, weighted slope interpolation).
- GRASS r.mblend — https://grass.osgeo.org/grass-stable/manuals/addons/r.mblend.html
  (delta-surface blend).
- DEM_Smooth_Blend — https://github.com/RobWilsonTas/DEM_Smooth_Blend
  (PyQGIS; proximity → cosine feather).


## Revision 2 (2026-09-20, evening): three axes — time, surface type, data quality

This section supersedes the model below where they conflict. It comes
from one day of building the mockup (sections at the end of this file
record the details) and from one failure that matters: the
first-principles bathy rule found Portage Lake's level on its own and
traced Hig's shoreline, then rejected the 2026 sonar where Portage
Glacier had retreated, because the 2015 lidar there is ice at ~105 m,
not flat water. Elevation and flatness cannot decide that cell. Three
separate things can, and the model has to keep them separate.

### The three axes

Every source layer is an **observation** of a surface. Three independent
questions are asked of it at every cell, and they are stored, generated,
edited and displayed as three separate things:

| axis | question | varies | stored as | unit |
|---|---|---|---|---|
| **time** | when was this surface observed, and how much has it changed since? | date: one per layer (a raster for mosaics like 3DEP/IfSAR). Change: per cell, by surface type and process | `date` scalar on the layer; change *rates* in the recipe, per class | years; m/yr |
| **surface type** | what physical surface did the observation measure? | per cell, complexly | `class.tif`, 8-bit categorical | ground, water, bed, ice, subglacial, canopy, built, void |
| **data quality** | how well does it measure the surface it measured? | per cell, complexly | `sigma.tif`, expected vertical error | metres |

None of the three is a mask. The mask (weight) is the *decision*, kept
in its own rasters as today (auto + paint), and it is the only thing
the fold ever reads. The three axes are the *evidence* the automatic
decision is made from, and each can be corrected by hand on its own.

**Why age relevance varies.** A survey has one date, but the *cost* of
that age differs by cell: a glacier surface moves metres a year, a delta
or beach decimetres, a bedrock highland effectively nothing. So time
enters the decision as **staleness**, a derived per-cell error:

    sigma_t = rate(class at the cell) × (as_of − date)

with rates in the recipe per class (defaults: ground 0.01 m/yr, beach /
delta / channel 0.3, ice 3, water n/a — and, where the glacier apps have a
local dh/dt, that instead). Staleness is never stored, it is computed for
a product date. This is what makes a 2015 lidar at σ = 0.1 m still beat a
2024 IfSAR at σ = 2 m on bedrock, and lose to it on the glacier, with no
hand rule.

**Class changes, too.** An observation's class is what the surface *was*
then. The class *now* at a cell is the class of the newest observation
that has one. That is the Portage retreat: the 2026 sonar (bed, so water
now) makes the 2015 ice observation an observation of a surface that no
longer exists. For `terrain_as_of(2015)` the newest observation on or
before 2015 says ice, and the ice surface is the terrain of that product.

**Quality is where `lowest` lives.** Two ground DTMs of unchanged ground
that differ do so because one failed to reach the ground (canopy, snow),
a one-sided error. "Prefer the lower within the same class and no
expected change" is a quality tie-break, not a rule of its own. Other
quality generators: distance from real soundings for a sonar TIN
(extrapolation), distance from the footprint edge, slope, canopy height
from a DSM, point density from LAS, a datum-confidence scalar per layer
(the xGEOID17B trap). Start with a scalar σ per layer from its metadata
and add rasterised modifiers as sites need them.

### One combination, at the end

For a product with target surface T and date `as_of`:

1. **gate** — compatibility of the observation's class with T at that
   cell, using the class-now rule above: 1, 0, or a fallback rank (ice
   surface stands in for ground where no bed exists, flagged);
2. **expected error** — `sigma = sqrt(sigma_q² + sigma_t²)` per compatible
   observation;
3. **winner** — the smallest expected error, with the recipe's fold order
   as the tie-break and `lowest` as the within-class tie-break; then
   `despeckle` and `feather` as today.

The output of `state_rules` is the winner map turned into one auto mask
per layer, exactly as the plan already wants, so the editor, the paint
round trip and the fold do not change. Winner-take-all with a feather,
not inverse-variance blending: a blend everywhere smears and hides
disagreement; a choice with a seam is what a human can see and overrule.

Every product emits `source_index`, `source_date`, `class`, and
`sigma_total` rasters: what, when, which kind, how sure. The tool gets a
**"why" readout**: hover a cell and see, per layer, z, class, gate,
σ_q, σ_t, total — the whole decision in one line.

### Where hand edits go

Attach a correction to the axis it is about, so it is reused by every
product built from that observation:

- "that is not water, it is ice" → paint the **class** raster;
- "this patch of the sonar is extrapolated junk" → paint **sigma** high
  (a quality veto), not weight 0;
- "I want this seam here" → paint the **weight**, per recipe, as today.

Class and quality corrections are properties of the observation and are
stored beside it; weight paint is a property of the composite. The mask
tool edits all three with the same wand, airbrush and paint files; the
stack panel gains a class and a quality thumbnail per row.

### Data layout

An **observation package** beside each archive COG, product-independent,
built once and cached:

    obs/<dataset_id>/
      date.json            {"date": "2020-10-05", "sigma_m": 0.15, "class_default": "ground"}
      class.tif            8-bit, from LAS classes / product default / detectors / polygons
      class_paint.tif      hand corrections, versioned
      sigma.tif            metres, from sigma_m + modifiers
      sigma_paint.tif      hand vetoes, versioned

Composite recipes add `target`, `as_of`, `rates` and per-layer overrides;
the Portage recipes keep working with class defaults from product type.

### Three serial stages

1. **observe** — `tools/lidar/observe.py`: build the observation package
   for a source (date, class, sigma). Usable alone; its outputs are
   viewable on `/lidar/` as overlays.
2. **composite** — this tool: stack × masks × fold, `state_rules` from
   the three axes. Everything built today stays valid.
3. **view / edit** — `/lidar/` shows products and their diagnostics; the
   mask tool edits weight, class and quality.

Reserved, not built: canopy (no DSMs organised), buildings and bridges
(footprints only when a risk product needs them), local dh/dt rates from
the glacier apps, a date raster for mosaics.

### Revised order of work

1. Finish the phase-1 engine: streaming full-grid builds, `datasets.json`
   sources, snapped grid, import/export-mask, COG write + catalogue entry,
   `report.json`. **Done 2026-10-09 except `datasets.json` sources and the
   snapped grid**, which belong to the first non-Portage recipe (Lituya).
2. Observation packages, minimal: date and σ from `datasets.json`, class
   from product default + flat-water detector + glacier-outline polygons;
   `state_rules` with gate → expected error → winner; the "why" readout.
   **Acceptance test: `portage_auto` resolves the retreat with no paint** —
   agreement with Hig's merge rises from 86 % and the south-west
   difference disappears — and a bedrock cell keeps the 2015 lidar over
   a newer coarse survey.
3. Lituya (offset report vs 0.13 m), then Seward (real intertidal gap,
   tide-derived level) as before.
4. Intertidal profile fill + Kachemak hold-out, as before.
5. Tier 2 on `/lidar/`; tier 3 continues locally with class and quality
   painting.
6. Products beyond `ground_current` when a site needs them.

### Scope guardrails (Hig's worry, 2026-09-20: data quantity and interface depth)

What is actually recorded per observation is small. Time is one date
that `datasets.json` already carries. Class and quality are one scalar
each (`class_default`, `sigma_m`) that every dataset gets from its
product type and method with no work. Rasters exist only where a rule or
a hand edit makes them non-uniform, and an 8-bit raster that is uniform
almost everywhere compresses to a few kilobytes. The derived
`class.tif` / `sigma.tif` are caches like `mask_auto`, regenerable; the
versioned things are the scalars, the paint and the polygons.

Interface depth is bounded by one rule: **there is one editing idiom,
paint**, and class and quality are two more paintable rasters on a layer.
The stack panel shows their thumbnails only when they are non-uniform,
the way the paint column appears only when there is paint. A site with
plain lidar over IfSAR never sees any of it.

Rule proliferation is the real risk, not storage. Guardrail: a quality
or class modifier enters the code only when a named site fails without
it, and the hand veto is always the fallback. Initial set, and no more
until a site demands it:

- quality: `sigma_m` per dataset; `density` from LAS where we have the
  points (Grewingk 2021 and Kachemak 2023 are DTM deliveries, so there a
  **facet detector** — local planarity of the DTM, which is what low
  point density looks like in a delivered raster — or the hand veto);
  `extrapolation` for sonar TINs (distance from soundings). IfSAR in
  narrow canyons: hand veto for now.
- class: product default; flat-at-level water; glacier-outline polygons.
- time: date; three rate numbers (ground, beach/delta, ice).

What the three axes buy that weight paint cannot: a veto or a class fix
on an observation is reused by every product made from it (the two bad
patches in Grewingk 2021 are fixed once, not once per recipe), a rule
can find such patches without a person, and a dated series
(`terrain_as_of` animations) stays stable because an old good survey is
not displaced by a new poor one. If a site only needs "prefer A here",
weight paint remains the right tool and nothing else is touched.

### Open questions for Hig (revision 2)

- Change rates per class: the defaults above, or per-site values, or the
  glacier apps' dh/dt where available?
- Glacier outlines for the flight year: RGI 7, the inventory's own
  polygons, or drawn per site?
- Class and quality corrections as polygons (categorical, re-rasterisable
  at any resolution) rather than paint rasters, or both?
- First release `ground_current` only, or `terrain_as_of` too?
- Subglacial bed inside composites (IceBoost as a modelled observation with
  a large σ) or kept as the separate product it is today?


## State of play: sparse-point interpolation (PAUSED 2026-09-21)

Hig's call: pull back, work on the topo DEM merge and surface
classification first, then return to the interpolation tool. Everything
needed to resume is here.

**What exists.** `tools/lidar/grid_bathy.py`, the observe-stage script
that turns soundings into a bathymetric observation package
(`bathy.tif`, `distance.tif`, `sigma.tif`, `domain.tif`,
`constraints.geojson`, `report.json`). Working command for Portage:

    W=/Volumes/Nunatak/lidar_build/composite/portage_subaerial
    P=/Volumes/Nunatak/Landslides/Portage/Portage_topobathy
    /opt/anaconda3/bin/python3 grid_bathy.py --method wtps --smoothing 1 --res 4 \
        --shore-step 15 --ext-step 20 \
        --points $P/Bathy_points/bathy_points_all_updated.gpkg --subaerial $W/subaerial.tif \
        --level 29.29 --edge $P/251018_10m_contours/Portage_bathy_edge.gpkg \
        --out-dir /Volumes/Nunatak/lidar_build/obs/portage_bathy_wtps --holdout 4

(~1 min; the subaerial merge comes from
`composite.py build composites/portage_subaerial/recipe.json --window
9500,13750,19650,23450 --out $W/subaerial.tif --diag-dir $W/diag`.)
The current output is in `obs/portage_bathy_wtps/` and is what
`composites/portage_axes_regrid` and the port-8767 tool instance use.

**What was settled** (details in the dated sections above):
- Interpolator: weighted thin-plate smoothing spline, global solve,
  per-site σ² on the diagonal, coordinates in km. Sounder hold-out 6.7 m
  within 50 m of data, 14 m at 50–100 m; the error is data-limited
  (~0.15–0.2 m per m of distance from a sounding), and no interpolator
  beats another by more than the hold-out noise (n = 24 / 12).
- Constraints: soundings (σ by type), waterline at the level where the
  subaerial merge crosses it (σ 1 m), one-sided slope extension from the
  land slope 5–25 m above the waterline (σ 3 m + 0.1 m/m, kept only where
  it is deeper than the unconstrained fit), topobathy bed cells from the
  merge where a topobathy layer wins (none at Portage).
- Outliers: soundings > 3 MAD and > 8 m from their six nearest
  neighbours get σ × 4 and are listed in `report.json` (9 at Portage; the
  pair at the north end differs by exactly ±20 m and looks like a slip).
- Rejected: linear-kernel RBF (cones at data), local-neighbour RBF (seam
  streaks), hard extension (bed ridge), trend + Gaussian process
  (shore-parallel terraces), home-made spline in tension (slow, worse).

**What is open** (resume here):
1. Domain from the lidar water class beyond the polygon (`--water-class`)
   scored worse on the near-data bin and took 7 min; not conclusive.
2. Hig's guide contours (`251018_Hig_interpolation_contours.gpkg`) as a
   constraint class — the human knowledge his TIN had and this does not.
3. A proper spline in tension (GMT `surface`, `brew install gmt`) if the
   bowing between sparse points matters at a site.
4. σ from the spline itself (the GP posterior, or a leave-one-out
   estimate) instead of the distance calibration.
5. The retreat basin: 1972 estimates dominate there and the waterline
   against the 2015 ice edge is the polygon edge; a dated glacier outline
   would place it.
6. Sea sites: level from tide/MSL, not the histogram mode.

## The Portage example

`/Volumes/Nunatak/Landslides/Portage/Portage_topobathy/Crops/Topobathy_merge/`
holds Hig's rigid first version of this tool, and is the regression target
for step 1:

- `merge_settings.py`: a 30000 × 20000 grid (EPSG:32606, 1 m), a list of
  five `{'DEM': path, 'mask': path}` entries in fold order — IfSAR base with
  no mask, then Anchorage 2015, Whittier topobathy, Portage 2020, Portage
  bathy 2026-01-04 — each mask an 8-bit GeoTIFF painted in Photoshop
  (`Merge.psb` beside the crops), 0 = black = 0.0 and 255 = white = 1.0.
- `topobathy_merge.py`: initialises the output to NaN, copies the base,
  then for each entry streams 1024-row blocks and does
  `out = out·(1−m) + new·m` in place. No nodata guard, so the masks had to
  be painted to avoid NaN leaks. Output `Portage_topobathy_merge.tif`
  (float32, DEFLATE, BigTIFF).
- Some masks were re-georeferenced by hand with `.tfw` sidecars after
  Photoshop stripped the tags — the thing `import-mask` does in code.

The plan below generalises this: the same fold, with masks that are
generated and then edited rather than painted from scratch, a nodata guard,
derived layers for blends and fills, and a build that is reproducible from a
recipe.

## The model: a layer stack and a mask stack

A composite is an ordered list of **layers** on one common grid, each with a
**mask** in [0, 1], folded from the bottom up:

```
out = layer[0]
for i in 1..n:  out = out·(1 − m_i) + layer[i]·m_i
```

Everything else in the tool is a way of producing a layer or a mask:

| Thing Hig wants | How it appears in the model |
|---|---|
| Rote combination | Layer = a survey's archive COG warped to the grid. Mask = its valid footprint with the seam feathered (auto-generated). |
| Fade between two surveys | The seam feather *is* the mask ramp. Width, shape and adaptivity are parameters of the mask generator. |
| "Prefer X here, Y there" | Painted into X's mask (hard or soft brush) or a polygon rasterised into it. |
| Void an artifact | Paint 0 into that layer's mask. Lower layers show through. |
| Hand-corrected DEM patch | A layer (the patch raster) with a mask (its footprint, feathered). |
| Delta-surface blend (r.mblend) | A **derived layer**: the lower survey plus the interpolated seam offset, masked over the transition zone. |
| Intertidal profile fill | A derived layer (the fill surface) with a mask covering the void, feathered into both neighbours. |
| CoNED state rules | A mask generator: per-pixel category state → which layer's mask gets 1 here. |

So the two kinds of hand intervention Hig named (masks and correction DEMs)
and the algorithmic fixes all reduce to *(layer, mask)* pairs, and the
composite is only ever the fold. Nothing touches the output directly. That
keeps the composite reproducible when a source is rebuilt, and it means a
painted mask survives regenerating the automatic one (see Masks below).

Nodata is a mask concern, not a fold concern: the fold runs on float32 with
NaN, and the *effective* mask is `m_i · valid_i`, so a brush stroke of 1
over a layer's hole cannot punch NaN into the composite. The Portage script
has no such guard, which is why its masks had to be painted carefully.

## Masks

Per layer, three rasters on the common grid, all 8-bit greyscale GeoTIFFs
(0 = black = 0.0, 255 = white = 1.0) so they open in QGIS, Photoshop and
GIMP unchanged:

- `mask_auto.tif` — regenerated from the recipe (footprint, feather, state
  rules, fills). Never hand-edited.
- `mask_paint.tif` — two bands, value and alpha. Hand edits only. Empty
  alpha means "no opinion", so regenerating `mask_auto` does not lose paint.
- `mask.tif` — the effective mask, `auto·(1−α) + value·α`, times the valid
  footprint. This is what the fold reads and what the viewer shows.

**The paint raster is the record.** Brush strokes are not stored; they are
the message a painting tool sends to whatever splats them into
`mask_paint`, and a per-session undo stack, nothing more. (An earlier draft
proposed a versioned stroke log. Hig's objection stands: as the set of
tools grows, a log of every tool's gestures is a second format to keep
compatible forever, for a benefit — replay at another resolution — that
resampling the paint raster gives well enough.) What is versioned is the
`mask_paint` raster, which is small when compressed because most of it is
untouched alpha. Polygon edits ("prefer here", "exclude") are rasterised
into `mask_paint` too; the polygon itself may be kept as a convenience for
re-drawing, but the raster is authoritative.

Photoshop, GIMP and QGIS stay first-class painting tools:
`composite.py import-mask <layer> edited.tif` diffs an externally edited
mask against `mask_auto` and folds the difference into `mask_paint` (alpha
1 where they differ). Photoshop strips geotags, so the importer takes the
georeference from the recipe grid and ignores the file's own, with a
size-and-shape check. That is the `.tfw` sidecar dance from Portage done
once, in code. `composite.py export-mask <layer>` writes the effective mask
back out for the next round.

Mask operations are recipe steps, not hand work: blur (the Portage feather),
erode/dilate, threshold, invert, clip-to-polygon. Applied to `mask_auto` in
order, so a recipe can say "footprint, erode 5 m, blur 20 m" and get the
Portage-style soft edge without a brush.

### Mask generators

Per layer (`footprint`, `feather`, `waterline`) or across a set of layers
(`lowest`, `state_rules`). The set-level ones write into several layers'
`mask_auto` at once.

**`lowest`** is the common case Hig named: several good DEMs of the same
ground, and the lower surface is the better bare earth wherever they differ,
because it is the one whose ground filter got under the vegetation or that
was flown snow-free. Kachemak 2023 against Grewingk 2021 around Halibut
Cove is the picture: two sound lidars whose difference is mostly canopy
penetration and snow, not ground change. The generator, over a candidate
set in fold order:

- where only one candidate is valid, its mask is 1 (footprint rule);
- where several are valid and the lowest is lower than the highest-priority
  candidate by more than `tol_m` (the pair's noise, ~0.1–0.2 m for two
  lidars), the lowest gets 1 and the others 0 at that pixel; within
  tolerance the priority layer keeps it, so the mask is not per-pixel
  speckle in the noise band;
- `smooth_m` majority-filters the winner map before rasterising, then the
  usual `feather` op softens the edges so the fold does not step between
  filters at every canopy edge;
- a `max_drop_m` cap: a candidate lower by more than that is not adopted
  but written to `report.json` and `attention.tif` — it is a pit, a missing
  bridge deck, a water surface that one survey had and the other did not,
  or real change (a landslide scar, a cut bank), and which of those it is
  is a human call. Run `despike_dtm.py` on inputs first; the minimum is a
  magnet for negative spikes.

**Real change** is where `lowest` is wrong by construction, and it is
common: glacier thinning is the extreme, but landslide deposits, cut and
fill, delta growth, a rebuilt road, a beach after a storm all move the
ground between surveys. Where the newer surface is *lower* (thinning, a
scar) the minimum happens to pick the newer one and a "current ground"
composite is right by accident. Where the newer surface is *higher*
(deposition, advance, construction) the minimum keeps the old ground and
is simply wrong, and nothing in the elevations alone says whether a rise
is canopy, snow, or a new landslide toe. That is a human call, and the tool
has to make the call fast:

- the generator writes `change_candidates` — connected blobs of
  `|newer − older| > change_m` with area above `min_area_m2`, as polygons
  with area, mean dz, sign, and which layer `lowest` chose — alongside the
  masks, and `attention.tif` marks the same cells;
- in the editor (below) each blob is one click: **accept** pushes the newer
  layer's mask to 1 over the blob, feathered by the current feather
  distance, so the newer surface wins there whatever `lowest` said;
  **reject** leaves the auto mask alone; both are ordinary paint;
- `prefer_year` is the bulk form of accept: over a drawn or wanded region,
  the newest valid layer wins, for a glacier where every blob is real.

For anything historical (a composite meant to show the ground as of a
date) `lowest` is the wrong generator and `prefer_year` with a cutoff is
the right one. Where the rule is wrong locally, the fix is a selection and
a fill on the loser's mask, which is why this is a mask generator and not
a derived "min of N" layer — the two are equivalent in the fold, but one
mask per layer is what an editor can reach.

`highest` and `median` come free with the same code. `median` over three or
more renderings of one point cloud is a cheap despiker in its own right.

## Layers

`layers[i].source` is one of:

- a `datasets.json` id — the archive COG, already NAVD88 with its
  `vertical_shift_m` stamped, warped to the grid with `-novshift` and the
  `retag_generic_nad83()` / `apply_vertical_shift()` helpers from
  `build_lidar.py`; bilinear for BAG-derived layers per `RESUME.md`;
- a local path — for crops, patches, and things not yet in the manifest
  (this is how the Portage inputs come in);
- a **derived layer** spec — `{"derive": "delta", "over": "B", "from": "A"}`,
  `{"derive": "intertidal_profile", ...}`, `{"derive": "smallhole_fill",
  "of": "A"}`, `{"derive": "plane_shift", "of": "A", "dz": -0.4}`. Derived
  layers are computed from other layers in the stack and cached as rasters
  like any other; their auto masks come from the same generator.

Per-layer options: `waterline` (void topographic lidar below an elevation
or inside a drawn polygon — sea-surface returns and hydro-flattened fill at
flight-time tide would otherwise beat the bathy), `category` (topo,
topobathy, bathy_hr, bathy_mr, patch — for the state rules), `resample`.

## What the references contribute

All four reduce to mask generators or derived layers, and all are a few
hundred lines of numpy/scipy. None runs outside its host (ArcGIS Pro, GRASS,
QGIS console), so none is run as-is.

| Source | Becomes | Note |
|---|---|---|
| DEM_Smooth_Blend | Mask generator `feather`: distance-from-edge (`scipy.ndimage.distance_transform_edt`) → cosine ramp over `width_m`. | The default seam. Two dozen lines. |
| r.mblend | Derived layer `delta`: difference sampled in a ring along the seam, tapered to zero (or to the mean, r.mblend's `-a`) across the transition zone, added to the lower layer. | Right for offset-dominated seams; the upper survey is untouched and the lower's morphology survives. |
| DEMFusion | Adaptive `width_m` per seam pixel from `clamp(|Δz| / tan(slope), w_min, w_max)`, smoothed along the seam; polygon ceilings; diagnostic rasters. | Skip the Euclidean→contour→natural-neighbour weight surface, 40× storage and day-long runs. |
| CoNED (Cushing & Tyler 2024) | Mask generator `state_rules`: per pixel, per category, has-data and below-MSL; rules set masks. Receded shoreline (old topo above MSL, new bathy below → fill, not ghost beach) and topobathy-above-MSL-where-everything-else-is-below (→ topo wins) fall out directly. Their weighted slope interpolation is a `delta` variant boosted by the coarse layer's slope. | Keep a uint8 per category, not a 16-bit pack. Their 15 m / 50 m zones are Florida numbers. |

## Architecture

Package `tools/lidar/topobathy/`, CLI `tools/lidar/composite.py`, working
directory `$LIDAR_BUILD/composite/<id>/`. Runtime as the rest of the
pipeline: `/opt/anaconda3/bin/python3` (numpy 2.3, scipy 1.15, rasterio
1.4), `/opt/homebrew/bin` GDAL 3.13 for warps, `PROJ_NETWORK=ON`. Runs on
the Mac where the archive COGs live.

```
composite/<id>/
  recipe.json            committed to the repo
  masks/<name>_paint.tif hand; committed (the record of every hand edit)
  edits.geojson          committed: polygons and notes from the web tool
  patches/*.tif          committed if small, else listed with a hash
  grid.json              derived: lattice, dims, transform
  layers/<name>.tif      derived: float32 on the grid (+ valid band)
  masks/<name>_auto.tif  derived
  masks/<name>.tif       derived: effective
  attention.tif          derived: where a generator declined to decide
  out/<id>.tif           archive COG
  out/source_index.tif   argmax of effective masks (which layer won)
  out/delta.tif          composite minus naive priority mosaic
  out/state.tif          CoNED state, if used
  out/report.json        offsets per overlap, seam lengths, fill areas, hold-out RMSE
```

Recipe sketch:

```json
{
  "id": "resurrection_topobathy_2024",
  "grid": {"epsg": 6335, "res_m": 1.0, "extent": "union"},
  "msl_navd88_m": 0.93,
  "layers": [
    {"name": "ctx",   "source": "ctx_3dep",          "category": "topo_mr"},
    {"name": "topo",  "source": "seward_2019_topo",  "category": "topo",
     "waterline": {"below_m": 1.6}},
    {"name": "b2016", "source": "resurrection_2016", "category": "bathy_hr",
     "resample": "bilinear"},
    {"name": "b2024", "source": "resurrection_2024", "category": "bathy_hr",
     "resample": "bilinear"},
    {"name": "b2024_ext", "source": {"derive": "delta", "from": "b2024", "over": "b2016"},
     "mask": {"auto": "transition"}},
    {"name": "beach", "source": {"derive": "intertidal_profile",
     "between": ["topo", "b2024"], "library": "auto", "max_gap_m": 300}}
  ],
  "mask_defaults": {"auto": ["footprint", {"feather": {"width_m": [10, 80], "adaptive": true}}]},
  "mask_rules": [{"lowest": ["topo", "topo_2021"], "tol_m": 0.15, "smooth_m": 3, "max_drop_m": 3}],
  "state_rules": "coned_default",
  "auto_offset": {"measure": true, "apply": false}
}
```

Layer order is fold order: first entry is the base and has no mask, exactly
as in `merge_settings.py`. The Portage recipe is the same file with five
local paths and `"mask": {"file": "../Portage_2020_mask.tif"}` on each.

Stages, each cached on disk and windowable with `--window bbox`:

1. **grid** — target lattice snapped the way `kenai_ptd_run.py` pins tile
   bounds (unsnapped bounds gave 51 cm seams once already). For Portage the
   grid is simply the base crop's.
2. **stack** — each layer warped to the grid, float32 + valid band.
3. **derive** — derived layers, in dependency order.
4. **masks** — auto masks from the generators; effective masks from auto,
   paint and footprint; measured overlap offsets into the report (applied only if
   the recipe says so — datum errors belong in the source's
   `vertical_shift_m`, not hidden inside a composite).
5. **fold** — the multiply-add, block by block, as in `topobathy_merge.py`
   but NaN-aware and with the effective mask. This is the cheap stage.
6. **write** — archive COG in house format (float32, ZSTD, −9999, overviews
   rebuilt, `-mo vertical_shift_m=0`, `-mo composite_recipe=<sha>`,
   `-mo composite_paint=<sha of the paint rasters>`), diagnostics, then a `datasets.json` entry
   with `archive_mode: copy`, `product: "Topobathy composite"`, `dev_only`
   until Hig lifts it. `source_index` and `delta` are baked to PMTiles like
   the slope layer, so the viewer can tint "who won" and "what changed".

Because stages 1–4 are cached and stage 5 is trivial, a mask edit costs one
fold over its window, not a rebuild. That is what makes live painting
possible later.

### The intertidal fill

The gap between the lidar waterline and the sonar's shallowest return is
the piece none of the references solve. As a derived layer:

- Shore-normal frame from the void's own distance transforms: distance from
  the topo edge and from the bathy edge give each void pixel a position
  `t ∈ [0, 1]` across the gap and a gap width; edge elevations `z_topo`,
  `z_bathy` are known along the perimeter.
- **Profile library.** Where the gap is closed by real data (topobathy
  coverage, or narrow voids the delta layer handles) cast transects normal
  to the shoreline every ~25 m, normalise to `t` and to
  `(z − z_bathy)/(z_topo − z_bathy)`, keep the ensemble. In the void, apply
  the median normalised profile of the nearest N transects, scaled to the
  local edge elevations. Fallback where no library is near: parametric
  (Dean `h = A·x^(2/3)` for beaches, linear for rock), selected per polygon.
- Its auto mask is the void, feathered into both neighbours; the fold does
  the rest.
- Reject where the gap exceeds `max_gap_m` or the edges invert (bathy edge
  above topo edge means the waterline is wrong, not the beach); report
  those as voids for hand attention. A painted mask can also simply turn
  the fill off where it looks wrong.

**Validation by a different route than the fit:** Kachemak Bay has
`seldovia_2019` topobathy spanning the intertidal. Void its intertidal band,
fill from `kachemak_bathy_2009` + topo, score against the withheld truth.
Do this before the fill is trusted at Resurrection Bay, where there is no
truth.

## Interface

Three tiers, in order. Painting tools are tier 3 and are not needed for the
first composites; the file-based route (QGIS/Photoshop → `import-mask`)
covers hand edits until then.

### Tier 1 — recipe + CLI, review in the existing viewer

```
composite.py build       recipe.json [--stage grid|stack|derive|masks|fold|all] [--window bbox]
composite.py masks       recipe.json [--layer NAME]      # regenerate auto masks only
composite.py import-mask recipe.json --layer NAME edited.tif
composite.py export-mask recipe.json --layer NAME [--effective|--auto|--paint]
composite.py export-ref  recipe.json --diff A B | --layer NAME   # 8-bit images on the grid, for wanding in Photoshop
composite.py report      recipe.json
composite.py holdout     recipe.json --truth seldovia_2019 --band intertidal
composite.py publish     recipe.json                     # datasets.json entry + build_lidar --stage all
```

Review loop: the composite is a dev-only survey, and the viewer's
**Difference** control shows it minus any source; `source_index` and
`delta` PMTiles show who won and what the blend changed. First deliverable
is a regression: rebuild `Portage_topobathy_merge.tif` from its five crops
and masks and match it.

### Tier 2 — polygons and parameters on `/lidar/`, editor-gated

The scarp-trace pattern (`inventory/scarps.py`, `scarps.js`, and the pencil
in `map.js`): Terra Draw (`pointerDistance: 8`, `doubleClickZoom` off), CSRF
header, JSON-vs-HTML response check, writes editor-only, a PostGIS table
`composite_edits(id, recipe_id, layer, kind, params jsonb, geom, edited_by
text, timestamps)` from an idempotent management command. Unlike scarps,
reads are editor-only too: a half-made mask polygon is not something the
public map should show. `kind` is `prefer` / `exclude` (polygons rasterised into a layer's
`mask_paint` at build), `waterline`, `seam_width`, `slope`,
`profile_shape`, `attention` (a note, no effect). A "Composite" panel
lists recipes from `/lidar/composites.json`, shows layers in fold order,
and shows the current effective masks as a tinted overlay (PMTiles baked
at build time). `composite.py build` exports the table to `edits.geojson`
and stamps the hash into the COG, so a build is reproducible from the repo
alone while drawing happens wherever the gated layers are visible,
including prod.

### Tier 3 — the mask editor, local and offline-first

The user works on the mask image directly, in the Photoshop idiom Hig
described: **select** a region, **modify** the selection, **fill** it.
The mask is the document; the DEMs, hillshades and difference rasters are
reference layers you can see through and select on. A brush is just one
selection tool among several, and every tool ends in the same place: a
region of `mask_paint` set to 0, 1, or a value in between, with a feather.
That is what makes the editor generic across generators — `lowest`,
`feather`, the state rules and the intertidal fill all just produce a
starting mask and, where they are unsure, a set of candidate selections.

Selection tools:

- **Magic wand** on any visible raster: click a seed, take the contiguous
  region within `±range` of the seed's value. On the *difference* raster
  this selects a thinning glacier or a landslide deposit in one click; on
  the *mask* it selects a blotch the generator made; on a *DEM* it
  selects a flat (a lake surface, a hydro-flattened fill). Tolerance,
  contiguous or global, and 4/8-connectivity as in Photoshop.
- **Candidate blobs** from the generators (`change_candidates`,
  `attention`): listed and outlined, click to select, keyboard to accept
  or reject, next. This is the efficient path through a glacier with fifty
  real changes and three canopy artifacts.
- **Polygon / lasso**, and the **brush** (radius in metres, hardness) as a
  selection brush or a direct paint.
- **By value range** on a raster (a threshold, no seed) for bulk work.

Selection modifiers: grow / shrink by a distance, **feather** by a
distance (the one Hig named — the selection edge becomes a ramp so the fold
does not step), invert, add / subtract / intersect with the current
selection, and "select same on the other layer" (a `lowest` decision is
a pair of complementary masks, and pushing one up should pull the other
down).

Fills: set to 0 or 1, set to a value, push toward 0 or 1 by an amount,
restore auto (clear paint alpha in the selection), blur within. Undo is
per operation, a session stack; the paint raster on disk is the record.

Implementation:

- Runs where the stack is: on the Mac, against the dev server with the
  composite working directory mounted, no network in the loop. Sync to the
  repository (paint rasters, `edits.geojson`, the built composite) at
  whatever cadence Hig chooses, by the usual commit and publish steps;
  prod never hosts the editor.
- **Client** draws selections and brush strokes on a canvas over the map
  at once, for feel; nothing is authoritative until the server answers. A
  wand click is posted with the seed and tolerance, and the server floods
  at grid resolution (`scipy.ndimage.label` on the thresholded window,
  bounded by a maximum region) and returns the selection as a small
  raster tile set or a polygon, whichever is smaller.
- **Server, local only**: holds the session selection as a raster, applies
  modifiers and fills to `mask_paint`, re-folds the cached stack over the
  dirty window, invalidates tiles, and serves mask, selection and composite
  as tiles from `/lidar/composite/<id>/{mask,sel,dem}/{z}/{x}/{y}`. A fill
  over a glacier-sized blob at 1 m is a few million cells: a second, not a
  frame, but it is one click, not a stroke. Latency for strokes is the
  fold over the dirty window, which is why stages 1–4 are cached.
- The same code path serves `import-mask` (an external edit is a fill with
  a raster as the selection), so a Photoshop edit and an editor edit land
  in the same place and the build sees no difference.

The plain image-editor route stays valid for anything the editor cannot do
yet: `export-mask` the effective mask and the difference raster as
matching 8-bit images, wand and feather in Photoshop, `import-mask` the
result. Getting this round trip right in tier 1 means the editor is a
convenience, not a prerequisite.

If a browser canvas over MapLibre cannot be made responsive enough under
the demshade worker, the fallback is a small native window (Qt + numpy)
over the same local endpoints; the model does not change. Two checks
before committing either way: whether the demshade package accepts a tile
URL template rather than a PMTiles archive, and how a MapLibre `canvas`
source behaves under the demshade compositor.

## Order of work

1. **Model + fold + masks as files.** grid, stack, `footprint`,
   `feather` and `lowest` generators, mask ops, effective-mask rule, fold,
   write, `import-mask` / `export-mask`. Regression: reproduce the Portage merge. Then Lituya
   (`lituya_2025` over `lituya_2023` over topo): two surveys of the same
   seabed with a known 0.13 m residual, so the offset report can be checked
   against an established number.
2. **Derived layers + state rules.** `delta` with adaptive width, CoNED
   state rules, `waterline`. Seward: `resurrection_2024` + `_2016` + topo,
   the real intertidal gap and ghost-shoreline case.
3. **Intertidal profile fill** + the Kachemak hold-out. Stop and read the
   RMSE before going further.
4. **Tier 2** polygons and panel; migration dev-only first as with fault
   scarps.
5. **Tier 3** the mask editor, local only: wand + candidate blobs +
   feather + fill first (that is the real-change workflow), brush second.
6. Publish the first composite on Hig's call, then the auxiliary PMTiles.

## Open questions for Hig

- Which site first after Portage: Seward (the real problem) or Lituya (the
  easy check)? The plan says Lituya for the engine, Seward for the rules.
- MSL in NAVD88 per site — from the CO-OPS bench mark sheets, or already on
  hand for Seward, Seldovia and Lituya? (CO-OPS prints feet there.)
- Are the topographic lidars over these bays hydro-flattened, or do they
  carry water-surface returns? That decides whether `waterline` is an
  elevation threshold or has to be a drawn polygon.
- Should measured overlap offsets ever be applied inside a composite, or
  always pushed back into the source's `vertical_shift_m`? Report-only by
  default in the plan.
- Paint rasters in the repo, or in `data/` shipped by rsync like the
  pyramids? Compressed they are small (mostly untouched alpha), but the
  Portage masks are full-frame paintings with no auto/paint split, and
  would be committed whole unless `import-mask` is run against a
  regenerated auto mask first.
- For `lowest`: should big *drops* (newer lower by more than `max_drop_m`)
  be adopted by default, since a current-ground composite wants them, or
  go to the candidate list like rises do? The plan currently lists both
  signs and lets the human accept.
- Do composites get their own region in the catalogue or sit beside their
  sources with a `composite` product tag?

## Disk and compute

At 1 m a bay-scale composite is 10⁷–10⁸ cells: 40–400 MB per float32
layer, three masks per layer at 8-bit, three to six layers, plus derived
layers and diagnostics. Distance transforms run on seam-zone bounding boxes
only. Expect single-digit GB per composite on Nunatak, minutes for a full
build, seconds for a windowed fold. Portage (30000 × 20000, five layers) is
the upper end of what is in hand today and the Portage script already
streams it in 1024-row blocks; the fold keeps that block discipline.

## Development phases and mockup status (2026-09-20, second session)

A phase-1 mockup exists and passes the Portage regression. Code:
`tools/lidar/topobathy/` (grid, recipe, stack, masks, fold) and the CLI
`tools/lidar/composite.py` (`build`, `regress`, `masks`, all windowed);
recipe at `tools/lidar/composites/portage_legacy/recipe.json`. Recipes
live in the repo under `tools/lidar/composites/<id>/`; the working
directory with derived rasters stays on `$LIDAR_BUILD/composite/<id>/`.

Facts learned from the Portage inputs that the plan above did not know:

- The five crops are **not on one grid**: origins differ by up to 0.9 m
  (IfSAR 379999.63/6750001.05, Portage bathy 380000.00/6750000.00) and
  the painted masks carry **no georeferencing at all**. The legacy script
  stacks by row/column index. So the regression contract is pixel-index
  alignment (`"align": "pixel"` per layer), and a properly gridded rebuild
  will differ from the legacy output by sub-pixel resampling. That is
  expected, not a bug.
- Regression on a 2048 x 2048 window with all five layers active (rows
  12096-14144, cols 21440-23488): **bit-exact** on 4 194 179 of 4 194 304
  cells. The other 125 are cells where a mask of 1-2/255 sat over
  Anchorage 2015 nodata; the legacy output there is wrong by up to 80 m
  (the -9999 leak the plan predicted). The guarded fold gets them right.
- A bare `footprint` mask is the wrong auto mask for a TIN-interpolated
  bathy: the Portage sonar raster extends over land, and footprint +
  feather differs from Hig's painted mask over 30 % of the window, by up
  to 416 m. Bathy layers need a `waterline` / polygon / `state_rules`
  mask, or the paint. Footprint + feather is right for lidar tiles.

Phases (each ends in something Hig can look at in the viewer):

1. **Engine, done in mockup form.** Fold, file masks, `footprint`,
   `feather`, mask ops, paint + valid guard, geo and pixel readers.
   Remaining for a real phase 1: blockwise streaming for full-grid builds
   (the mockup holds one window in memory), `datasets.json` sources via
   `build_lidar.py` helpers, a snapped grid spec, `import-mask` /
   `export-mask`, COG write + `datasets.json` entry, `report.json`. Then
   Lituya 2025 over 2023 to check the offset report against 0.13 m.
2. **Generators: `lowest`, `state_rules`, `waterline`, `change_candidates`.**
   Seward (`resurrection_2024` + `_2016` + topo). First place a generated
   mask has to beat a painted one.
3. **Derived layers: `delta` (adaptive width), `intertidal_profile`.**
   Kachemak hold-out before the fill is used anywhere without truth.
4. **Tier 2 viewer panel**: composites list, source-index and delta
   PMTiles, polygons through the scarp-trace pattern, editor-gated.
5. **Tier 3 mask editor**, local only: wand + feather + fill on the
   difference raster, candidate blobs, brush last.
6. Publish on Hig's call.

### Mask domains and the local mask tool (2026-09-20, later)

**Domains.** A mask no longer has to cover the grid. Per layer, `domain`
is `"overlap"` (default: the rectangle that exactly encloses where the
layer overlaps any layer below it, plus `domain_pad_m`, default 50 m),
`"full"`, or an explicit box. Outside the domain the mask is the footprint
(the layer is either the only data or absent, so there is nothing to
decide). Mask files and paint rasters are written on the domain box and
say nothing outside the cells they cover (`covered`, not 0). The Portage
recipe says `"domain": "full"` because its masks are full-frame paintings;
the regression is bit-exact under both settings. Synthetic test in the
session log: a mask file covering only the overlap rectangle hides the
layer there and leaves it as footprint where it is the only data.

**Local mask tool** (tier 3 brought forward in a low-flexibility form):
`tools/lidar/mask_tool/server.py recipe.json --window r0,r1,c0,c1`, Flask
on 127.0.0.1:8765, `index.html` is one canvas page. Server holds one
window in memory (layers, auto masks, paint, selection, undo); client
shows PNGs (composite or layer hillshade, layer-minus-below difference,
effective mask, paint, footprint, selection) on stacked canvases with
wheel zoom and drag pan. Tools: wand on any reference raster with a
tolerance (scipy label flood), rectangle, grow/shrink/fill-holes/invert/
∩footprint, fill selection to 1 or 0 with an inward cosine feather, restore
auto, undo, save. Save writes `masks/<layer>_paint.tif` (value, alpha) on
the domain box into the recipe work dir (`work_dir` key, else
`$LIDAR_BUILD/composite/<id>`), which `layer_mask` reads by convention.
Measured on the 2048² Portage window: wand a few ms, fill + refold 0.28 s,
composite hillshade PNG 0.26 s. Known limits: one window per session
(save overwrites, no merge with an existing paint file); no MapLibre, no
tiles; brush not implemented. `/lidar/` exposure of the result is phase 1's
`write` stage (COG + datasets.json dev_only + source_index/delta PMTiles),
not this tool.

**Stack panel** (added the same day): the tool's left panel is a thumbnail
stack, composite on top then layers in fold order down to the base. Two
thumbnails per row: elevation on one grey ramp shared across the stack
(1st–99th percentile of all layers, so the rows compare), nodata in one
purple, the orange "shown" tint of the effective mask over it; and the
effective mask itself as a plain grey ramp. Thumbnails are strided
downsamples served from `/thumb/{composite,dem,mask}` in a few ms and
refresh on every refold. Hover gives data %, shown %, z range, paint %.
Clicking a row's elevation thumbnail opens that layer in the hillshade
view with the orange overlay; clicking its mask thumbnail opens the mask
itself in the main view as a grey ramp (nodata purple), with the orange
overlay suppressed. Background options "this layer's mask" and "this
layer's auto mask" do the same from the menu. All selection and fill
tools work identically in either view.

**Paint column and airbrush** (same day). The stack has a third column,
paint, shown only for layers that carry hand paint: the paint value as a
grey ramp where alpha > 0 over a checker where there is no opinion.
Clicking it opens the paint in the main view (also a background option).
The airbrush is a raster-editor airbrush over the paint raster: size (m),
hardness (fraction of the radius at full strength before a cosine
falloff), opacity (cap on what one stroke can lay down), flow (how fast
each dab builds), and three "colours": show (value 1), hide (value 0),
erase (removes alpha). The client previews dabs on a canvas at once and
streams points to `/api/stroke_pts`; the server dabs at 12 % of the
diameter, accumulates a per-stroke buffer capped at opacity, and
recomposites the paint from the pre-stroke copy ("over" compositing of
(value, stroke alpha) on (value, alpha)) so crossing a spot twice cannot
exceed the cap. `stroke_end` pushes undo and refolds (0.27 s on the 2048²
window, ~0.9 s to the refreshed view). `[` and `]` change size.
Clicking the composite row (name or thumbnail) shows the final result
clean: composite hillshade, all overlays off; the current layer stays
selected so the tools keep working.

**Component toggles** (same day). Under each stack thumbnail a small
switch, on by default, session-only (never saved, never in the recipe):
DEM = the whole layer in or out of the fold (the base too: off empties
it); auto = the generated or file mask, off means a plain footprint so the
view shows what the auto mask removes; paint = the hand paint, off means
auto only. Each toggle refolds (~0.25 s) and refreshes the composite,
overlays and thumbnails, so in the composite view you can pull components
out one at a time to see what each contributes. Rows with DEM off are
dimmed.

### First-principles composite: `portage_auto` (2026-09-20, evening)

`tools/lidar/composites/portage_auto/recipe.json`: the five Portage inputs
with **no hand masks**. Generators now in `topobathy/masks.py`, each a
few lines of numpy/scipy taken from the prior art:

- `footprint` → `erode` → `despeckle` → `feather` (DEM_Smooth_Blend: EDT
  distance inward, cosine ramp) for the lidar and topobathy layers.
- `water` for the bathy (CoNED state rule, generalised): the layer counts
  where the surface below it is water — flat at the water level within
  `tol_m` — and the layer is beneath that level. The level is `"auto"`:
  the mode of the fold-below elevation under the layer's footprint (a
  lake has no tide gauge; the lidar over water is flat, so the histogram
  peak is the level). Portage Lake came out at **29.21 m** with no input.
  A TIN extrapolated over land is above the level and drops out.
- `lowest` (ours): layer wins where lower than the fold below by > tol.
- `despeckle` (drop blobs and fill holes below `min_area_m2`), `smooth`
  (majority filter).

Generators that compare with the fold below (`water`, `lowest`) get `z`
and the running fold from `build_window`, which now folds as it goes.
Windowed builds compute masks on a halo (`halo_px`, default 128) and
crop, so the window edge is never mistaken for a layer edge.

Result on the 2048² window against Hig's painted merge: 86 % of cells
identical, 6.7 % differ by more than 1 m, and 90 % of those are places
the rule kept lidar where Hig painted bathy in — the SW shallows where
the lidar surface is not flat at lake level. The generated bathy mask
otherwise traces his painted shoreline. That is the intended workflow:
generate, then fix by hand in the mask tool (`server.py` runs on this
recipe like any other; paint saves to its work dir).

Stack panel no longer rebuilds its DOM on refresh: thumbnails are
preloaded and swapped, classes patched in place.

Next generators from the plan, not built: adaptive feather width
(DEMFusion), `delta` seam-offset layer (r.mblend), `state_rules` with a
category table, `change_candidates` for `lowest`, seam offset report.

**Side-by-side examples**: `mask_tool/run_examples.sh` starts
`portage_legacy` on 8765 and `portage_auto` on 8766 on the same window
(`WIN=` to change). A recipe's `reference` (or `legacy_output`) raster
is loaded into the session and exposed as the "composite − reference"
background and wand raster, so a first-principles build can be compared
with, and hand-corrected toward, an existing merge. `mask_tool/README.md`
documents the tools and the paint round trip (save → restart / build).

### Revision 2 built: `topobathy/axes.py` and `portage_axes` (2026-09-20, night)

- `axes.py`: `CLASSES` (void, ground, water, bed, ice, subglacial, canopy,
  built, modelled), `TARGETS` (compatibility table by class-now for
  `ground_current` and `top_surface`), `DEFAULT_RATES`, `years()`,
  `class_raster()` (steps: `default`, `water_level` on the layer's own z
  with a modal-bin guard, `below_level` against the fold below for
  sonar), `sigma_raster()` (`sigma_m` + `facets` + `edge`),
  `state_rules()` (credible = valid, classified, σ < `sigma_max`, dated
  ≤ `as_of`; class-now = newest credible; gate from the table; σ_total =
  √(σ_q² + (rate·Δt)²); winner = min σ_total at the highest gate level,
  later layer on ties, `lowest` tie-break among ground observations).
- `composite.py`: a recipe with `target` runs the two-pass build (read
  all, axes per layer against a provisional footprint fold, rules,
  `rules_post` despeckle + feather, paint, fold). Recipes without
  `target` are unchanged.
- **Facet size as a sonar quality proxy is real but needs a coarse
  threshold.** Inside the painted lake 96 % of cells sit in TIN facets
  < 50 000 m² (survey lines are far apart, and that is legitimate
  interpolation); outside, 94 % sit in one 690 000 m² hull facet. A cut
  at 100 000 m² separates them; `k·√area` with k = 0.05 beyond that puts
  extrapolated cells above `sigma_max`. With the small default cut the
  whole sonar went non-credible and the lidar water surface won the lake.
- **Water detection must use the layer's own elevations** (a topo lidar
  is flat at its own lake level), not the fold below; the fold below is
  right only for the sonar, which has no water surface of its own.
- `portage_axes` vs Hig's merge on the window: the retreat basin
  resolves with no paint (cells differing > 1 m in the south-west box:
  55 % → 23 %, the rest is the feather ring); overall 11 % of cells
  differ > 1 m, three quarters of them the sonar adopted along the shore
  where the painted mask stopped short, and a block on the right where
  the 2020 lidar sits far below IfSAR in a canyon (IfSAR's own quality
  problem, now expressed as a winner). Dates for IfSAR and Whittier are
  assumed in the recipe and must be corrected.
- Tool: class and quality thumbnails per row (shown only when
  non-uniform or painted), backgrounds for class / σ / σ_total / class
  now, a **why readout** on hover (per layer z, class, gate, σ_q, σ_total,
  shown; winner marked), and a **paint axis** selector: weight (as
  before), class (pick a class; hard-edged), quality σ (good / ok / poor /
  veto bands). Class and σ paint rerun `state_rules` and refold; they
  save to `obs/<layer>/{class,sigma}_paint.tif` in the work dir, not to
  the composite's mask paint. Reading them back at build time is not
  wired yet (next).

### Observe stage experiment: re-gridding the Portage soundings (2026-09-20, late)

`tools/lidar/grid_bathy.py` grids sparse soundings into a bathymetric
observation using the above-water merge (`composites/portage_subaerial`,
built over the whole lake with `--diag-dir`) for the shoreline:
soundings (σ by type: 2024 sounder 0.5 m, Mayo 1977 3 m, 1972 estimates
5 m), waterline points at the lake level along the domain boundary, and
slope-extension points that continue the land slope 5–25 m above the
waterline down to 60 m below it (skipped where the land is flat or
steeper than `--max-slope`, i.e. deltas and ice fronts). Domain = Hig's
edge polygon (else water class + a sounding buffer). Outputs `bathy.tif`,
`distance.tif` (to the nearest real sounding), `sigma.tif` (σ from type
+ k·distance, k fitted on the hold-out), `constraints.geojson`,
`report.json`. `--holdout N` withholds every Nth sounding and reports
error by distance, separately for the 2024 sounder points.

Findings (355 points over 5.3 km², a fjord-shaped basin to −167 m):

| interpolator | sounder hold-out RMSE 0–50 m | 50–100 m |
|---|---|---|
| plain TIN of soundings (≈ Hig's, without his contours) | 6.9 | 15.3 |
| thin-plate spline, soundings only | 7.3 | 17.4 |
| thin-plate + shoreline constraints, local solve | 7.1 | 21.6 |
| thin-plate + constraints, global solve | 7.7 | 18.6 |
| **linear-kernel RBF + constraints, global solve** | **6.6** | **16.5** |

- The error is set by the data density, not the interpolator: ~7 m
  within 50 m of a sounding, 15–20 m at 100 m, for every method (n = 24
  and 13 test points, so the differences between methods are within
  noise). σ grows at ≈ 0.2 m per metre of distance from data.
- The shoreline constraints do not reduce the hold-out error, but they
  fix the *shape*: a bowl-shaped rim from the land slope instead of a TIN
  hull, and no interpolation over land. That is what they are for.
- A **local** RBF (`neighbors=64`) leaves visible seam streaks in the
  hillshade; the global solve (≈2 300 sites, 14 s) does not. Thin-plate
  bows deeper than the TIN between sparse points (bias −5 m at 50–150 m,
  −21 m at 150–400 m from data); the linear kernel overshoots less and
  is the default.
- With this observation in the axes recipe (`portage_axes_regrid`,
  σ from the raster, `class_sigma_max` large because the domain polygon
  is the class evidence), the lake bed wins the whole lake including the
  retreat basin. `state_rules` was changed for this: class credibility
  (which observation says what is here now) is separate from elevation
  eligibility (σ < `sigma_max` to compete), and where nothing eligible
  is compatible the best compatible observation wins anyway, flagged
  `fallback`. Without that, a big-σ bed observation lost to an obsolete
  ice surface.
- What would actually lower the error: more soundings (each 2024 track
  is worth more than any interpolator), and Hig's guide contours as a
  constraint class (`--contours`, not yet wired; they are in
  `251018_Hig_interpolation_contours.gpkg`).

**Interpolator choice (2026-09-20, late).** Hig: the linear-kernel surface
makes sharp points at each sounding, and the slope extension left a
ridge on the lake bottom. Tried, all with hold-out on the 2024 sounder
points (n = 24 within 50 m, 13 at 50–100 m, so differences under ~2 m are
noise):

| method | 0–50 m | 50–100 m | look |
|---|---|---|---|
| linear-kernel RBF, global | 6.6 | 16.5 | cones at every sounding |
| thin-plate, global, exact | 7.7 | 18.6 | smooth; bows deep between sparse points |
| **thin-plate, global, smoothing 20, one-sided extension** | **7.4** | **16.5** | **smooth, no ridge, natural bowl** |
| trend (depth vs shore distance) + Gaussian process, Matérn 5/2, nugget = σ² | 8.6 | 22.2 | shore-parallel terraces from the binned trend |
| spline in tension t = 0.3, sparse direct solve on 8 m grid | 9.0 | 27.5 | crude implementation, 4 min; dropped |

Decisions: (1) thin-plate spline, global solve, `--smoothing 20`, kernel
smooth at data (no cones); (2) the **slope extension is one-sided**: fit
without it, keep only extension points below the unconstrained surface
(the bed is at least as deep as the continued land slope, never forced
shallower), refit — this removed the ridge (513 of 1125 points kept);
(3) sigma from distance stays; the GP posterior would be the principled
σ but its trend model needs work before it is worth it.

Background: Smith & Wessel 1990, continuous-curvature splines in tension,
is the standard for bathymetry (GMT `surface`; tension suppresses the
minimum-curvature overshoot we see as bowing); kriging is equivalent to
a spline under a particular covariance and gives σ directly; GEBCO
grids sparse soundings with splines in tension. A proper tension spline
(GMT, `brew install gmt`) is the next thing to try if bowing between
sparse points matters at a site; the smoothing parameter does part of
that job here.

**Shoreline, point uncertainty, profile and band tools (2026-09-21).**
Hig: the shoreline still looked strange (suspected merge artifacts
compounding with the shore handling), and closely spaced soundings with
big disagreements made quirky bottom; asked for a profile tool and an
elevation band view.

- **The merge artifact was real and is fixed in the rules.** Inside the
  lake the subaerial merge had no compatible observation (class now
  water, no bed layer), so the fold fell through to the IfSAR base,
  whose lake sits 13 m below the real level. `state_rules` now has a
  last resort: where nothing compatible exists, the newest dated
  observation of any class wins (the water surface), flagged, never a
  stale base. The lake interior of `portage_subaerial` is now the lidar
  water surface at 28.9–29.2 m.
- **The waterline is now where the merge crosses the level** (first dry
  cell beside a wet one), not the drawn polygon edge, which sits ~10 m
  inside the lidar water for 73 % of its length. Slope is measured only
  through dry cells; extension marches only through wet cells. Where a
  topobathy layer contributes bed inside the lake those cells become
  observations (`--winner`, `--bed-winner`); at Portage that is 0.003
  km², so none.
- **Soundings inconsistent with their neighbours** (|resid| > 3 MAD and
  > 8 m against the median of the 6 nearest within 200 m) get σ × 4 and
  are listed in `report.json` (9 at Portage, up to 103 m apart between
  neighbours 70 m apart — a decimal-point or unit error to review).
- **Weighted thin-plate spline** (`--method wtps`): the classic smoothing
  spline with per-site σ² on the diagonal, coordinates scaled to km
  (in metres the system is ill-conditioned and gave 18 m errors).
  Sounder hold-out 6.7 m (0–50 m) / 14.0 m (50–100 m), the best of
  everything tried; weights are what let a 1972 estimate or a flagged
  fix pull less than a 2024 fix.
- Waterline and extension σ are now parameters (`--shore-sigma` 1 m,
  `--ext-sigma` 3 m + 0.1 m/m); with the level-crossing waterline there
  are ~6 000 waterline and ~11 000 extension points, so their σ must
  stay well above the soundings' 0.5 m.
- Open: using the lidar water class to extend the domain beyond the
  polygon (`--water-class`) scored worse on the 0–50 m bin (11.1 vs
  6.7 m) and took 7 min; with n = 24 that is not conclusive. Default
  stays polygon domain + level-crossing waterline.
- Tool: **profile** (drag a line; chart of composite, every layer and
  the reference, `/api/profile`), **elevation band** background
  (composite or layer, viridis ramp clipped to a band, two vertical
  handles or typed limits, "fit to view" takes the 1–99 % range of the
  visible composite via `/api/range`).

### Sequential rules (2026-09-21): a mask is "better than the composite so far"

Hig, looking at the Anchorage 2015 mask: the global rule ("where this
layer beats every other layer", with the per-cell `lowest` tie-break
between three lidars of similar σ) made 18 617 winner fragments that
despeckle and feather turned into an organic 185-blob patchwork. His
model instead: walk the stack bottom-up; IfSAR is the base; Anchorage
2015 replaces it everywhere it is an improvement, even though later
layers will replace much of that again; Whittier then replaces large
parts of the composite so far; and so on to the top. **A layer's mask
is where it beats all layers below it, not where it beats all layers.**

`axes.state_rules_sequential()` is now the default (`"rules":
"sequential"`; `"global"` keeps the old one). Per cell, observation i
replaces the running composite when its class is compatible with the
class-now (the newer of the running newest and i), and either the
composite's shown class is no longer compatible (a newer water/bed
observation makes old ground obsolete), or its expected error beats the
running one by more than `margin_m`, or `lowest` is switched on for
that layer in the recipe and the smoothed (default 50 m) difference is
lower by more than its tolerance. Nothing compatible shown yet + this
is the only observation → last resort, flagged. Masks then get the usual
despeckle + feather.

Results on the test window against Hig's merge: cells exact 82 % → 90 %;
differing > 1 m 11.0 % → 5.4 %; retreat box 23 % → 18 %. The Anchorage
2015 mask is its footprint minus its water class, one piece; Portage
2020's is its full footprint. `lowest` is off in every recipe; Anchorage
2015 carries σ 1 m for its snow (Hig: multi-metre errors in places)
until a snow modifier exists. The legacy regression is still bit-exact.

### Snow, and the water surface in the stack (2026-09-21)

**The lake surface stays in the composite until the bathymetry replaces
it.** Hig: the water surface is accurate data, it just needs classing as
water rather than ground. The compatibility table now lets a `water`
observation stand in at half rank where the class now is water or bed,
so under sequential rules Anchorage 2015 replaces IfSAR over the lake
with its water surface (its mask is its whole footprint, 87 % of the
test window), Whittier's water surface does not beat it, and the sonar
(gate 1) replaces it. The subaerial merge's lake is therefore the lidar
water level with no special case. Snow stands in for ground the same
way.

**Snow is a class, not a sigma.** Added `snow` (code 9) with these
rules, which are a deliberate compromise between snow-awareness and not
mapping every patch:
- A snow surface is ground plus an unknown 0–few m. Compatibility with a
  ground target is half rank, and the sigma raster adds
  `snow_sigma_m` (1.5 m) in quadrature where the class is snow. So a
  snow-free survey (σ 0.15) beats a snowy one even if older, and where
  only snowy data exists it still shows, flagged.
- **Detection is a guess at region scale** (`snow_vs` class step):
  higher than a reference surface by more than `dz_m` (0.5) averaged
  over `smooth_m` (50 m), AND smoother than the reference (roughness
  ratio < 0.7), opened and cleaned of blobs < 2000 m². The reference is
  a named snow-free layer or the composite so far. At Portage the
  reference is Portage 2020 (October); Anchorage 2015 comes out 2 %
  snow on the test window, at the high elevations.
- Residual snow that the detector does not see stays inside σ, and
  "ground" in every DEM is accepted to include some snow surface.
  Users correct with the class brush (snow is in the picker).
- What would make this better: a snowline elevation per survey date as
  a prior, and DSM/DTM pairs where they exist.

**Water and glacier classing for users.** The class brush already
applies any class. Added wand rasters **roughness** (m, local residual
from the mean; water, snow and ice are flat — tolerance ~0.1 m floods a
lake or a glacier tongue in one click) and **slope** (degrees). The
recipe can also class from vector files (`polygons` step: glacier
outlines, drawn lakes) — implemented, not yet used at a site. The
water-level detector remains automatic; for the sea the level is an
input.
