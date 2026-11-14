# Hosted files, photo drops, field photos

The `files` and `drops` apps and `inventory/photos.py`.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Hosted files

The `files` app hosts arbitrary admin-uploaded files at stable, human-readable public URLs — a place to park a KML, PDF, dataset, etc. and hand out a link. Managed entirely through Django admin at `/admin/files/hostedfile/` (superusers + `site_admins`).

- **Model `HostedFile`** (SQLite): `file` (FileField), `name` (the public URL token), plus `title`/`description` (admin-only notes), `inline` (bool), `content_type` (MIME override). On save, blank `name` auto-fills from the uploaded filename.
- **Public URL = `/files/<name>`**, served by `files.views.serve` (a `FileResponse`). The URL token is **decoupled from disk storage**: `name` is the URL, `file.name` is wherever Django's storage wrote the bytes (it may suffix on collision — fine). `name` is unique and regex-constrained to `[A-Za-z0-9._-]` (matches `files/urls.py`), so no path traversal. MIME is `content_type` if set, else guessed — with an `_EXTRA_TYPES` table in `views.py` for geo types Python misses (`.kml`/`.kmz`/`.geojson`/`.gpkg`). `inline` toggles `Content-Disposition: inline` vs `attachment`.
- **Fully public, unlisted.** No auth. Nothing links to the files, so they're reachable only by someone who knows the URL, and `robots.txt` keeps `/files/` disallowed so indexing can't undo that.
- **Storage: `MEDIA_ROOT = data/media/`** (`upload_to='hosted_files/'`). `data/` is volume-mounted in dev and prod and gitignored, so uploads **persist across deploys** and are never committed or baked into the image. There is **no `/media/` static route** — the only way out is the `/files/<name>` view.
- **Permissions** are granted in `init_groups` (HostedFile CRUD → `site_admins`), so **re-run `init_groups` after deploying** if the group needs the perms (as with any group change).

## Photo drops — `/drops/`

A link you send a collaborator so they can upload a **folder** of photos
(the first use: a game camera on a slow landslide, thousands of JPEGs). App
`drops/` (SQLite models `Drop` + `DropFile`), design note at the top of
`drops/models.py`. **Live on production since 2026-10-05.**

**Making one:** the Django admin (`/admin/drops/drop/add/`) is the full
editor — title, note shown to the uploader, expiry, a **write-only
passphrase** field (hashed; *Clear passphrase* removes it), `created_by`
set to whoever saves, and the full upload URL as a live link once saved.
`/drops/` (**☰ → Photo drops**) is the quick version: create, copy link,
file counts, close/reopen. Both land in the same table.

- **The bytes never touch the droplet** — it has ~7 GB of disk. The browser
  uploads straight to a **private** Cloudflare R2 bucket (`DROPS_R2_*` in
  `.env`; its own token, the lidar token is bucket-scoped) using URLs Django
  presigns (`drops/r2.py`, boto3 imported lazily). Reads are presigned GETs
  too, so the gallery page is the only thing gunicorn serves. Key layout:
  `drops/<slug>/<relative path as dropped>`; server thumbnails under
  `drops/<slug>/.thumbs/`.
- **The link is the credential.** `slug` is a 12-char random token; an
  optional passphrase (hashed) is a second lock, held in the session. No
  account needed to upload. Viewing needs any site account — every account
  is a known collaborator. Creating/closing drops is editor-only.
- **Uploader** = Uppy **4.13.3** from the Transloadit CDN with our own
  callbacks (`drops/static/drops/upload.js`); Uppy 5+ dropped those for its
  "Companion" server protocol, so do not bump the major without rewriting
  the API. Single presigned PUT under 100 MB, S3 multipart above (5 MB
  parts, `MULTIPART_PART_SIZE`, 3 requests in flight — a part counts only
  once whole and the parts in flight share the uplink, so 6 × 32 MB and
  7 × 8 MB both saved nothing in 30–45 s field-uplink tests; each upload
  records its part size, so changing it restarts uploads in flight rather
  than mis-pairing parts; the page shows "safely stored" bytes, which
  Uppy's bar, counting partial parts, does not). **Resume is "drop
  the folder again"**: the client fetches `{path: size}` of what the server
  has and skips matches — simpler and more robust for a folder than
  IndexedDB state. **A big file resumes mid-file** (2026-10-31, for
  multi-GB photogrammetry rasters): `DropUpload` (SQLite, migration 0003)
  remembers each unfinished multipart upload by path + size + the browser's
  `lastModified`; before a batch, a preprocessor asks `multipart/resume/`
  and, on a hit, sets the file's `s3Multipart {uploadId, key}` — Uppy 4's
  own restore path, which lists the stored parts and sends the rest. A
  different file under the same path aborts the old upload; an upload R2
  has expired (its default lifecycle rule, ~7 days; the token cannot read
  it) answers 410 and starts afresh. **A same-size file under an existing
  path is skipped as received** (the folder resume compares size only), so
  a revised release goes in a new drop or folder, not over the old one.
  Completion is recorded only after the server HEADs
  the object (`api_record`), so a row always means real bytes — but **the
  reverse did not hold** until 2026-10-06: that record call is a second
  request after the bytes are safe, and on a flaky link it was lost (37 of
  the Portage game cam's 5,183 files were in the bucket with no row). Now
  `record/` retries with backoff, every finished batch calls `reconcile/`,
  and `drops/reconcile.py` lists the drop's prefix and adds rows for any
  object without one (also refreshes rows whose object changed; reports,
  and only with `--prune` deletes, rows whose object is gone). Same code
  behind the editor's **Check storage** button on `/drops/` and
  `manage.py drops_reconcile [--drop slug] [--dry-run] [--prune]`.
- **Bucket CORS is required** (origin + `ExposeHeaders: ETag`, which
  multipart completion needs), and **it lives in the Cloudflare dashboard**
  (bucket → Settings → CORS policy), set by hand on 2026-10-05 for
  `https://landslidescience.org` and `http://127.0.0.1:8001`. Reason: an R2
  *Admin* token cannot be bucket-scoped and an *Object* token cannot set
  CORS, and a bucket-scoped token was the right trade. So `manage.py
  drops_setup_bucket` fails against the real token by design and prints the
  JSON to paste. The token is an **Account** API token (not User — it must
  outlive any one login), Object Read & Write, scoped to
  `landslidescience-drops`; its four values sit in the repo `.env` (dev),
  `/opt/landslidescience/.env` (prod) and `~/.r2_drops.env` (Mac, for the
  mirror script).
- **Processing is two-tier.** Light, on the droplet: `drops/thumbs.py`
  makes a 400 px thumbnail + reads EXIF `taken_at` (the hook a future
  time-lapse type hangs off), one object in memory at a time, in a per-process
  worker thread that claims rows with an atomic conditional UPDATE (two
  gunicorn workers); `manage.py drops_thumbs [--drop slug] [--retry-errors]
  [--stalled]` is the synchronous equivalent after a restart. Heavy, on the
  Mac: `tools/drops/r2_pull.sh <slug>` mirrors a drop to
  `/Volumes/Nunatak/landslidescience/drops/<slug>/` (rclone `copy`, never deletes;
  credentials in `~/.r2_drops.env`) — that mirror is also the backup; R2 is
  otherwise the only copy.
  **Never edit the mirror in place** (rclone re-copies any file whose size
  changed, so an EXIF edit there is undone by the next pull); processed
  output goes to a sibling folder. `tools/drops/gamecam_fix_clock.py`
  (2026-10-05) is the first such processor: it corrects a game camera's
  unset clock by one constant offset, stamps the corrected time on a copy
  of each frame and records the raw value in EXIF `UserComment`; its
  docstring records how the Portage offset was estimated from the
  night-mode switch.
- **Emailed game cameras lived here from 2026-10-29 to 2026-11-14 and are
  now entirely the monitoring project's** (Hig). Removed from this repo:
  `drops/mailcam.py`, `manage.py drops_mailcam`, `tools/drops/mailcam_cron.sh`
  and the public `/drops/cam/<camera>/latest.jpg|json` routes, so drops have
  no public URL again. What stays: the closed drop "Game cam email feed"
  (`LjSRG_RBBCZL`) with the frames filed up to 2026-10-31, and the
  `brightness`/`contrast` columns thumbnailing fills (grey-level mean and
  spread; low mean = night, low spread = obscured lens), which are what a
  "clearest frame of the day" pick would use. The monitoring app's
  `_dashboard.py` still names `landslidescience.org/drops/cam/` as a
  fallback for a camera its own feed has no frames from; that address now
  404s, and tidying it belongs to that project.
- **Nothing in the UI deletes bytes.** Close a drop to refuse uploads;
  deleting is rclone + Django admin, on purpose.
- Robots: `/drops/` disallowed and every template is `noindex`.

**The first drop — Portage game cam (slug `RZsJDoXqWe74`, 2026-10-05).** A
Braun SC1320WS4KDS on the slope above Portage Glacier, one frame every 30
min around the clock for 109 days, 5,183 files / 28 GB. What it taught:

- **Its clock was unset** (reset to 2022-01-01), so every EXIF date is one
  constant offset from the truth. The offset was recovered from the data:
  the camera's IR night mode writes 3840×2160 frames and daylight writes
  8416×4736, so each night's length is known to ±30 min; fitted to solar
  altitude at the site, the first frame is **2026-06-15 09:03:49 AKDT**
  (date ±1 week, time of day ±10 min; the camera ran 5 h 46 min ahead of
  AKDT). The correction is **+1625 days 18:14:00**. If the real deployment
  date surfaces, only that one parameter changes.
- **`tools/drops/gamecam_fix_clock.py`** applies it on the Mac: a stamped
  copy of each frame ("Est. corrected datetime: …", upper right, one
  line-height down so QuickTime's bar clears it) with EXIF dates shifted,
  UTC offset set, and the raw value plus the basis of the estimate in
  `UserComment`. The raw mirror is never edited (see above).
- **Where things live on Nunatak** (`/Volumes/Nunatak/landslidescience/drops/`):
  `RZsJDoXqWe74/` raw mirror (every size verified against the bucket),
  `RZsJDoXqWe74_corrected/` stamped set, `RZsJDoXqWe74_timelapse/` a
  one-frame-per-day video (109 noon frames, 4K + 1080p) with
  `frames_daily.tsv` naming the frames. Rain on the lens ruins many frames;
  picking each day's clearest frame by scoring the 400 px `.thumbs/` is the
  obvious refinement, not done.
- **Pulling 28 GB over the Homer link took ~12 h** at 0.3–2 MiB/s (the link,
  not R2); `--bwlimit` is only settable at rclone start, so throttling means
  stop-and-resume. Processing a subset never needs the full mirror: `rclone
  copy --files-from` fetched the 109 noon frames (0.8 GB) in two minutes.
- Only IM_04987 never reached the bucket; the collaborator's card is the
  only place it could come from.

## Field photos

Editor-uploaded field photographs, N:M linked to landslides. All server code
in `inventory/photos.py` (self-contained, like trace_views); schema via
`migrate_field_photos` (run once per environment): `photos` +
`landslide_photos (landslide_id, photo_id, sort_order)` in **PostGIS**, not
SQLite — photos are landslide data.

- **Bytes**: `data/media/field_photos/<id>/` — `original.<ext>` (never
  re-encoded; HEIC accepted via pillow-heif), `web.jpg` (~2000 px),
  `thumb.jpg` (~320 px). `data/` is volume-mounted → survives deploys, needs
  its own backup story (irreplaceable, outside git).
- **Serving URL is load-bearing** (like `/inventory/planet/…`):
  `/inventory/photo/<id>/{thumb.jpg,web.jpg,original.<ext>}`, immutable
  Cache-Control (bytes never change for an id). Snapshots reference these
  URLs rather than copying bytes.
- **Two-tier ordering**: `sort_order` set = "featured" (editor-starred,
  drag-ordered, lead the strip/slideshow); NULL = uncurated, displayed by
  `taken_at` (EXIF, no timezone — stored as-if-UTC, display dates only).
- **Dedupe by sha256**: re-uploading identical bytes links the existing
  photo — that IS the share-across-landslides path. Unlinking the last
  landslide deletes the photo row + files; nothing orphans.
- **EXIF ingest** (Pillow): DateTimeOriginal, GPS camera position (→
  `geom`, Point 4326 — camera, not landslide), GPSImgDirection. Upload
  response carries distance-from-centroid as a wrong-record check.
- **Uploads are one file per request** (manage-card JS loops, 2 in flight,
  per-file retry); cap 40 MB (`MAX_UPLOAD_BYTES`).
- Public display: `api_detail.photos` (featured-first order) → detail-panel
  strip + lightbox + white camera-dots on the map while the panel is open
  (clustered when co-located; camera never auto-moves). Manage card:
  `_photos_card.html` (lives OUTSIDE `#ls-edit-form` so autosave.js doesn't
  claim its inputs).
- Deps Pillow + pillow-heif are baked into the image → deploys that touch
  them need the rebuild (the normal deploy flow covers it).
