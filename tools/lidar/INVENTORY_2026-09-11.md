# Local lidar holdings vs what is online — inventory of 2026-09-11

Read-only scan of `/Volumes/Powder/Alaska GIS/Raster/AK_lidar/` (47 entries, ~1.6 TB) and
`/Volumes/Nunatak/{lidar_src,lidar_build/vrt,Hoonah_lidar,2023_NOAA_2m_contours,Seldovia}`,
cross-referenced with `tools/lidar/datasets.json` (19 online). gdalinfo header reads only.
Purpose: decide what to bring online, in what order, and what to gate; then rebuild every
dataset from its rawest form with one consistent merge/reproject pipeline.

Status: **online** = the manifest's source is this folder · **partial** = a product is online
but rawer or sibling forms are not · **not online** · **superseded** · **gate?** = provenance
or licence to confirm before publishing.

## A. Already online (12 folders → 18 manifest ids) and what they still hold

| Folder | Size | Manifest id | Rawer / extra forms NOT online |
|---|---|---|---|
| Homer/ | 47 G | homer_2019 | **partial** — `Homer/2018/2018_NCMP_merged_UTM5.tif` (2.2 G, EPSG:32605, 0.54 m) is a separate 2018 NOAA NCMP DEM. 103 tifs, 2 laz, 3 gpkg contours |
| Lower_Kenai/ | 23 G | lower_kenai | **partial** — `Bare-Earth_ArcGrids/` 2,574 `.adf` (5.5 G) ESRI GRID originals, the rawest form behind `Lower_KP_lidar_UTM5.tif` |
| KBay_2023/ | 324 G | kbay_2023 | **partial** — online is the COG; rawest is 5,422 tiles (268 G) `be_UTM4_*` at 0.5 m in NAD83(2011)/UTM 4N. 4 gpkg (25 G contours). NOAA report pdf |
| Matsu_2019/ | 394 G | matsu_2019 | **partial** — 7,490 intensity tiles (183 G) not online; UTM6 and `_meters` variants are derived duplicates |
| Matsu_Matanuska_dtm_9ft_c/ | 3.6 G | matanuska_2011 | SP AK-4 US-ft, 9 ft posts |
| Seward_2023/ | 38 G | seward_2023 | `_mod5`, `_shade` derived |
| 2024_Glenn_Alps/ · 2024_South_Eagle_River/ | 373 M · 398 M | glen_alps_2024 · eagle_river_2024 | DGGS RDF 2025-19 / 2025-20 |
| Columbia/ | 7.5 G | columbia_2022 | + intensity 178 M, 10 m contour gpkg |
| rdf2026_019_lidar-sitka…/ | 11 G | sitka_2019 | |
| Goodwin_2023/ · Ketchikan_2024/ · Dickson Highlands 2024/ | 514 M · 1.4 G · 2.0 G | goodwin_2023 · ketchikan_2023 + ketchikan_2024 · dickason_2024 | |
| 2021_Grewingk/ | 65 G | grewingk_2021 | **partial** — `rdf2023_002_dsm/` (DSM 1.4 G), `rdf2023_002_dtm_detail/` (11.3 G), intensity (1.4 G) not online; 15 `.sdat` (20.8 G) and `_v3` are derived |
| 2020_Portage/ · 2022_Maynard/ · Barry 2023/ | 6.2 G · 4.7 G · 14 G | portage_2020 · maynard_2022 · barry_arm_2023 | `Barry 2023/*_dtm_old.tif` (6.9 G) superseded, already noted |

## B. NOT online (AK_lidar), with gdalinfo of the main DTM

| Folder | Size | Year / area | Main raw form | CRS · px · dims · nodata · notes |
|---|---|---|---|---|
| Wrangell_2023/ | 64 G | 2023-07 Wrangell I. (RDF 2023-28) | `data/dtm/wrangell_lidar_2023_dtm.tif` 21 G + intensity 18 G + tiled zip 17 G | EPSG:6337 · 0.5 m · 63734×86799 · −9999 · uncompressed, 8 ovr |
| Haines_rdf2023_018_dtm/ | 68 G | 2021+2022 Haines (RDF 2023-18) | `data/dtm/haines_2022_dtm.tif` 46 G + 4 `dtm_detail_*` sub-AOIs | EPSG:6337 · 0.5 m · 109925×109438 · −3.4e38 · uncompressed, 9 ovr |
| Cordova_2023/ | 33 G | 2023 Cordova (RDF 2024-6) | `data/dtm_hydro_flattened/cordova_lidar_2023_dtm.tif` 14.6 G + intensity | EPSG:6335 · 0.5 m · 62277×61058 · −3.4e38 · 8 ovr |
| 2022_Twentymile/ | 34 G | 2022 Twentymile R. (RDF 2023-3) | `rdf2023_003_dtm/data/dtm/…_dtm.tif` 13.5 G; zips of dtm_detail 2.4 G + intensity 5.8 G; 10 m contours | EPSG:6335 · 0.5 m · 55002×64368 · −9999 · 8 ovr |
| turnagain_pass_fall_2018/ | 3.1 G | 2018 fall Turnagain Pass | `dtm/TurnagainPass_2018Fall_DTM_50cm.tif` 2.0 G + intensity | EPSG:6335 · 0.5 m · 32958×34386 · −3.4e38 · LZW |
| Penguin_Ridge_2021/ | 1.1 G | 2021-09-22 (RDF 2024-5) | `…_dtm_50cm_ponds_hydroflattened.tif` 529 M + intensity | COMPOUND NAD83(2011)/UTM 6N + NAVD88 · 0.5 m · 30181×10961 · +3.4e38 · LZW, 6 ovr |
| rdf2025_030_lidar-jago-dtm/ | 60 G | 2025-08-01 Jago R. permafrost, ANWR | 68 tiles `data/dtm/utm07_*.tif` 50 G (+ Hig's `Jago_merged.tif` 19.7 G) | COMPOUND NAD83(2011)/UTM 7N + NAVD88 · 0.1 m · tiles 7500×7500 · 3 ovr · first zone-7 dataset |
| Juneau_2012/ | 11 G | 2012 Juneau | `juneau_2012_dtm_1m_filled.tif` 4.8 G | EPSG:26908 · 1 m · 34037×36975 · −3.4e38 · vertical datum undeclared |
| 2019_Juneau_Thane_Rd/ | 419 M | 2019 Thane Rd (RDF 2024-16) | `…thaneroad_lidar_2019_09_06_dtm_1m.tif` 108 M + intensity | EPSG:6337 · 1.0 m · 12099×11558 · −3.4e38 · LZW, 6 ovr |
| Anchorage_2015/ | 35 G | 2015 MOA | RAW: 308 `.img` tiles (1.3 G) `Raw/<tile>/<tile>.img`; merged working copy `…_Eklutna_Eagle_meters_UTM6.sdat` 4.8 G; 1,236 pdf + 309 docx QC; 2 m contours 3.5 G | SP AK-4 US-ft (FIPS 5004) · 3 ft posts · 1000×1000 tiles · −9999 |
| Matsu_{Caswell_Lakes, Core_Area, Point_MacKenzie, Talkeetna, Willow}_dtm_9ft_c/ | 0.7–1.1 G each | 2011 Mat-Su Borough | one tif each | SP AK-4 US-ft · 9 ft posts · −3.4e38 · five siblings of matanuska_2011 |
| barry_arm_2020/ | 909 M | 2020-06-26 Barry Arm | `dtm/BarryArm_06262020_DTM1m_Hydroflattened.tif` 148 M + DSM 148 M + intensity 518 M | EPSG:26906 · 1 m · 8096×12263 · −3.4e38 · LZW, no ovr |
| Wolken/ | 10 G | 2019 Grewingk + 2020-06-27 Barry (G. Wolken, DGGS) | `Barry_June_2020/LandslideDEM01m_NAD83_2011_UTM6N.tif` 4.3 G + DSM 5.1 G; `Grewingk_May_2019/grewingk_dtm.tif` | EPSG:6335 · 0.1 m · 30009×50010 · LZW · **gate?** personal / pre-publication transfer |
| Larsen_Barry_Lidar/ | 2.0 G | 2020-06-03 Barry Arm (C. Larsen, UAF) | `2020-6-3-BarryArm_BareEarth.tif` 1.6 G | EPSG:32606 (WGS84) · 0.3 m · 16740×25044 · odd nodata 5.364 · **gate?** research delivery |
| glacierBay_DTM_2015/ | 796 M | 2015 Fairweather Fault, Lituya–Icy Pt (USGS/Bender 2016; NCALM+CRREL) | 4 tiles `fairweatherFault_DTM_{a,b,c,d}_…tif` | EPSG:26908 · 1 m · ~7005×9005 · −3.4e38 · NAVD88 via GEOID12B (README) |
| Glacier_Bay_2019/ | 129 G | 2019 GLBA (USGS 3DEP) | 575 `USGS_OPR_…BE_GB_*.tif` (partial) + `Reprocessed/Merged.tif` 77 G + `Hillshades_from_Chad/` 11 G | tiles EPSG:6337 · 0.5 m · **superseded** by the complete 6,728-tile copy in Nunatak/lidar_src (online). `Merged.tif` has no CRS tag |
| Seldovia_USACE/ | 39 G | 2019 NOAA/USACE NCMP, Seldovia/Kachemak | 124 bare-earth 1 m tifs; 157 `1mGrid` tifs; 125 RGB orthos (30 G); **31 `.las` 6.5 G**; 2 m contours | `2019_NCMP_Seldovia_Merged.tif` in GEOGRAPHIC EPSG:6318, 9e-6° (~1 m) · 7627×7579 · −9999 · public NOAA/USACE |
| Kipnuk_2023/ | 636 M | 2021 Kipnuk (file says 2021) | `kipnuk_2021_dtm.tif` 465 M | EPSG:6332 (UTM 3N) · 0.5 m · 8514×14188 · 6 ovr |
| Kwigillingok/ | 816 M | 2021-08-18 (RDF 2023-19) | `data/dtm/kwigillingok_lidar_2021_dtm.tif` | EPSG:6332 · 0.5 m · 11497×13868 · 6 ovr |
| MNI_2023/ | 4.6 G | 2023 "Corax" (also Nunatak/Corax_Boulder_Index_Muddy) | `Corax2023_6393usfeet.tif` 1.2 G | EPSG:6393 NAD83(2011)/Alaska Albers · 0.105 m (≈0.344 usft) · 17068×18286 · **gate?** no provenance, very high res |
| Eklutna_Glacier_2010/ | 283 M | 2010 Eklutna Glacier | `eklutna_glacier_dtm/eklutna_glacier_2pt5m_dtm.tif` | EPSG:26906 · 2.5 m · 7286×9263 · LZW · low priority |
| dds4/ | 975 M | 2018 fall Sitka | `sitka_2018/dtm/sitka_fall_2018_dtm.tif` | EPSG:26908 · 1 m · 20458×25684 · −3.4e38 · likely **superseded** by sitka_2019 |
| seward_2008_ds_geotiff/ | 19 M | 2008 Seward | `sewardds_geotiff.tif` 14 M (+ ERDAS rrd/aux) | tiny, superseded by seward_2023 |
| UTM/ · Lower_KP_lidar_UTM5_mod5.tif | 10 G · 5.3 G | — | UTM5 reprojections of the six Mat-Su 9 ft tiles; stray derivative of lower_kenai | derived duplicates, ignore |
| Icebridge/ | 6.3 M | NASA IceBridge sample | 1 tif | trivial |

## C. Other locations

| Path | Size | Contents | Online? |
|---|---|---|---|
| Nunatak/lidar_src/glacier_bay_2019/ | 26.4 G | 6,728 USGS 3DEP bare-earth tifs in 4 units | yes — source of glacier_bay_2019 |
| Nunatak/lidar_build/vrt/ | 8.8 M | `glacier_bay_2019.vrt` (6,728 sources, absolute paths into lidar_src) + tile lists; the only VRT | yes |
| Nunatak/Hoonah_lidar/ | 10 G | `Hoonah_2015_LiDAR_DEM.tif` + hillshade/ovr — EPSG:26931 (NAD83 / Alaska State Plane zone 1) · 1 m · 51300×39600 · −9999 · uncompressed | **not online** |
| Nunatak/2023_NOAA_2m_contours/ | 13 G | two gpkg of 2 m contours from the KBay 2023 lidar (duplicate of the KBay folder's) | vector, n/a |
| Nunatak/Seldovia/ | — | not lidar (photogrammetry, rain gauges, photos, tree rings) | ignore |
| Nunatak root `2023_NOAA_KBay_draft*.tif` | 45 G + 11 G | draft KBay DEM + shade/slope, same as the KBay folder's draft | superseded by the final COG |

## D. Rebuild notes for the NOT-online set (rawest form → one pipeline)

1. **Wrangell 2023** — EPSG:6337, 0.5 m, 21 G: single-file translate; nodata −9999 (not −3.4e38); NAVD88/GEOID12B per RDF 2023-28.
2. **Haines 2021–22** — EPSG:6337, 0.5 m, 46 G: translate; the four `dtm_detail_*` AOIs could be separate ids.
3. **Cordova 2023** — EPSG:6335, 0.5 m, 14.6 G: translate.
4. **Twentymile 2022** — EPSG:6335, 0.5 m, 13.5 G: translate.
5. **Turnagain Pass 2018** — EPSG:6335, 0.5 m: translate.
6. **Penguin Ridge 2021** — compound 6335 + NAVD88, 0.5 m: translate; keep the compound CRS (the gdalwarp geoid-shift gotcha in CLAUDE.md applies).
7. **Jago River 2025** — compound UTM 7N (EPSG:6336) + NAVD88, 0.1 m: merge 68 tiles (`gdalbuildvrt` over `data/dtm/`, then translate); prefer the tiles over `Jago_merged.tif`. New target zone. Remote ANWR permafrost — low landslide value, high cost.
8. **Juneau 2012** — EPSG:26908 NAD83, 1 m: warp to 6337; vertical datum undeclared.
9. **Thane Road 2019** — EPSG:6337, 1 m, 108 M: translate; cheap.
10. **Anchorage 2015 (MOA)** — rawest is 308 `.img` tiles, SP AK-4 US survey feet, 3 ft posts: buildvrt + warp to 6335 with a 0.3048 vertical scale (the same foot trap as Mat-Su). Big win: Anchorage bowl + Eklutna. Check MOA redistribution terms.
11. **Mat-Su 9 ft siblings** (Caswell Lakes, Core Area, Point MacKenzie, Talkeetna, Willow) — identical treatment to matanuska_2011: SP AK-4 US-ft → 6335/6334, vertical scale 0.3048, NAVD88/GEOID09. Five near-free additions.
12. **Barry Arm 2020 ×3** — barry_arm_2020 (EPSG:26906, 1 m, hydro-flattened + DSM), Wolken 0.1 m (EPSG:6335), Larsen 0.3 m (WGS84/UTM6, nodata 5.364): three co-located June-2020 surfaces; pick one, and settle Wolken/Larsen provenance.
13. **Glacier Bay 2015 Fairweather (Witter/Bender)** — 4 tiles, EPSG:26908, 1 m, NAVD88 GEOID12B: buildvrt the 4 + warp to 6337.
14. **Hoonah 2015** — EPSG:26931 (State Plane AK-1), 1 m, 10 G, no overviews: warp to 6337; vertical datum unknown.
15. **Homer 2018 NCMP** — EPSG:32605 (WGS84 tag), 0.54 m: same WGS84-tag question as lower_kenai/matsu_2019; overlaps homer_2019.
16. **Seldovia USACE 2019 NCMP** — bare-earth tiles are geographic EPSG:6318 (~1 m): buildvrt 124 tiles → warp to 6334; NAVD88 per filenames; the 31 `.las` are the true raw.
17. **Kipnuk 2021 / Kwigillingok 2021** — EPSG:6332 (UTM 3N), 0.5 m: new zone; small; Y-K delta coast, not landslide terrain.
18. **Eklutna Glacier 2010** (2.5 m) and **Sitka fall 2018** (dds4) — low priority, likely superseded.

## E. Superseded / duplicate (do not rebuild)

`AK_lidar/Glacier_Bay_2019/` (575-tile partial + `Merged.tif`) vs the complete lidar_src copy · `Barry 2023/*_dtm_old.tif` · `2021_Grewingk/*_v3*` and `Grewingk2021_v4_WGS84_UTM5.tif` · `UTM/` and root `Lower_KP_lidar_UTM5_mod5.tif` · `2023_NOAA_KBay_draft*` (Nunatak root + KBay folder) vs the final COG · `seward_2008_ds_geotiff` vs seward_2023 · every `*_mod5`, `*_shade`, `*_slope`, `*_smooth`, `.sdat/.sgrd` (Hig-made derivatives).

## F. Gate / discuss before publishing

- **MNI_2023 (Corax)** — 0.1 m Alaska Albers, no provenance in the folder; likely a private/client survey.
- **Wolken/** and **Larsen_Barry_Lidar/** — individually supplied research data (DGGS, UAF).
- **Anchorage_2015/** — MOA vendor delivery with per-tile QC reports; check redistribution terms.
- **Seldovia_USACE RGB orthos** (125 files, 30 G) — public NOAA/USACE, but imagery rather than DEM.

No LICENSE or "confidential" files were found anywhere; every DGGS RDF folder carries a public DOI in its README.

## G. Decisions and findings, 2026-09-11 evening (Hig)

**Gated (behind the sign-in; `inventory_viewers`+):**
- **Corax llc** — everything: `AK_lidar/MNI_2023/Corax2023_6393usfeet.tif` (2023 DTM, Alaska Albers usft, 0.105 m, ~1.8 km × 1.9 km near 147.62°W 61.81°N; UTM6 and `_m` derivatives beside it) and the three 2024 packages in `/Volumes/Nunatak/Corax_Boulder_Index_Muddy/` (Index Lake DSM + ortho, Muddy Creek DSM + ortho, Upper Boulder Creek DEM at 0.137 m in UTM 6N, 37 G; metadata origin "Corax llc", pubdate 2024-05-29). A letter of commitment sits in `Nunatak/Landslides/AK_systematic_surveys/LACCE/Docs/`.
- **Chugachmiut lidar** — `/Volumes/Powder/Alaska GIS/Raster/Chugachmiut_lidar_GeoTiff_Mosaic/be_mosaic.tif` (4.4 G folder): NAD83(2011)/UTM 5N, 1 m, 33629×29040, Float32, LZW, nodata −3.4e38, covering 151.96–151.38°W, 59.17–59.44°N (Nanwalek / Port Graham / the peninsula tip), plus four hillshade variants. It is a mosaic of `be_chug_NNNN` tiles that live on the **St Augustine** drive (`/Volumes/St Augustine/Chugachmiut LIDAR/UTM/Rasters/Bare_Earth/`, per the Dec 2018 QGIS project) — the rawest form, not currently mounted. BIA-funded; not found online after searching, so gate.

**Public, confirmed:** Barry Arm (all three 2020 surfaces), Anchorage 2015, USACE Seldovia 2019 (green lidar and the RGB orthos).

**Anchorage 2015 is fully online** at NOAA Digital Coast, dataset 8431: 51 GeoTIFF tiles of 10240×10240 px at 3 ft, State Plane AK-4 NAD83 (usft), NAVD88, Float32 LZW, nodata −9999, 13 GB total, plus a VRT and `urllist8431.txt`:
`https://noaa-nos-coastal-lidar-pds.s3.us-east-1.amazonaws.com/dem/AK_Anchorage_DEM_2015_8431/` (InPort 50332). GDAL reads the tiles by `/vsicurl/`, so the rebuild can pull straight from S3 instead of Hig's partial 308 `.img` tiles. Point clouds are on USGS 3DEP (`USGS_LPC_AK_Anchorage_2015_LAS_2017`).
