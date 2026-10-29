# Water review tool (W3 of WATER_REVIEW_PLAN.md) — local, mock-up (2026-10-29)

Browse a run's flags over the dev surface, decide them, draw or adopt polygons, and write the two files the
chain reads: `tools/lidar/water/review.geojson` (dispositions) and `tools/lidar/water/polygons.geojson`
(reviewed polygons). Mask-tool pattern: Flask on the Mac, never the droplet; nothing is deleted.

```bash
cd tools/lidar/topobathy/review_tool
./server.py --run /Volumes/Nunatak/lidar_build/composite/joint_v3/out_run20j --survey joint_v3j   # then open http://127.0.0.1:8767/
```

- Pyramids come from `~/Claude_projects/landslidescience/data/lidar_dev/pmtiles/<survey>*.pmtiles` (override
  with `LIDAR_DEV_PMTILES`), served here with Range, so the Django dev server need not be running.
- Left: flags by kind (chip = filter, with decided/total), sorted by area, height over the level, or undecided
  first. Click a row or a flag on the map; the card shows its numbers and the run's suggestion; set a disposition
  (island / noise / polygon edit / defer; keys 1–4, 0 clears) and a note; `j`/`k` step; `s` saves.
- Map: the survey's hillshade or Esri imagery; the automatic polygons filled by kind (sea blue, lake teal, river
  green, perched violet), the mapped lakes dashed, reviewed polygons with a white casing (withdrawn dotted).
  Click an automatic polygon → *Adopt as reviewed* (kind, level, name); ✎ draws a new one (Enter finishes).
- Decisions on flags the current run no longer raises are kept (`stale`) and written back on save.

Not here yet (the `/lidar/` version): the water surface and tide, soundings, a per-flag before/after of the bed,
and polygon vertex editing (adopt-and-redraw instead).
