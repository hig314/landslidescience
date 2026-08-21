# DATA_DROP.md — shipping data products to production

How collaborators get built data files onto landslidescience.org without
shell access, and how those files flow through dev testing to a live
deploy. The worked example is the IceBridge point layers; the path is
generic for any `data/` product.

## What ships, what doesn't

The `data/` tree is **gitignored and volume-mounted** — data never travels
through git or the Docker image. Two kinds of artifacts, only one of which
ever leaves your machine:

| | example | ships to prod? |
|---|---|---|
| **Built display products** — decimated/tiled/derived files the site serves | `icebridge_uaf_hf.json`, `icebridge_ares.json`, tile pyramids | **yes** — this is what the drop path is for |
| **Raw source data** — downloads, staging, records of truth | `icebridge_raw/` (~2 GB of NSIDC CSVs) | **no** — stays on your machine; keep your own backup |

If a "built" file is still enormous, that's a design smell — decimate,
tile, or gzip before shipping, and ask before pushing anything >100 MB
(disk on the droplet is finite and shared).

## Uploading (collaborator)

One-time setup: send your SSH public key (the one line in
`~/.ssh/id_ed25519.pub`; `ssh-keygen -t ed25519` if you don't have one) to
Hig. It gets installed on a restricted account.

Then, to ship files:

```bash
rsync -avP your_built_files*.json datadrop@143.198.140.54:/
```

Notes on what this account is: `datadrop`'s key is force-bound to
`rrsync -wo /opt/landslidescience/data/incoming` — a write-only jail. The
`/` in the rsync target *is* the staging directory. You can upload; you
cannot list, read back, open a shell, or touch anything else. `-avP` gives
you resumable transfers — re-run the same command if it drops.

Your files land in **staging**, which the web app cannot serve — nothing
is public at this point.

## Promotion (maintainer — Hig or Claude)

Moving files from staging into the live data tree is a deliberate, reviewed
step, run as root on the droplet. Always an explicit allowlist, never a
bare `mv *`:

```bash
# inspect what arrived
ls -la /opt/landslidescience/data/incoming/
# promote exactly the expected files
rsync -av --include='icebridge_*.json' --exclude='*' \
  /opt/landslidescience/data/incoming/ /opt/landslidescience/data/glaciers/
# clear the staging copies
rm /opt/landslidescience/data/incoming/icebridge_*.json
```

No restart needed — data files are read per-request.

## Why promoted data is safe to stage early

A promoted file is **inert until code that references it deploys**. The
serving routes only answer for specific known paths, and the UI only
requests URLs the deployed JavaScript asks for. So the working order is:

1. Collaborator uploads → maintainer promotes. Prod now *holds* the data,
   serves nothing new.
2. Hig pulls the same files to dev and tests them against the new code:
   ```bash
   rsync root@143.198.140.54:/opt/landslidescience/data/glaciers/icebridge_\*.json data/glaciers/
   ```
3. On approval, the normal code deploy runs — and the moment the container
   recreates, the layers are live, data already in place.

Same pattern as pre-adding a database column before the code that SELECTs
it: data first is a no-op; code first is a 404. Data first, always.

## Key onboarding (maintainer)

Append the collaborator's public key to
`/home/datadrop/.ssh/authorized_keys` **on one line with the forced-command
prefix**:

```
command="/usr/bin/rrsync -wo /opt/landslidescience/data/incoming",restrict ssh-ed25519 AAAA... name@machine
```

The `restrict` option disables forwarding, PTY, X11 — everything except the
forced command. One line per collaborator; remove the line to revoke.

## Ground rules

- Raw/staging directories (`icebridge_raw/`, `experiments/`, `fit_tiles/`)
  never ship — keep deploy rsyncs allowlisted or explicitly excluding them.
- New data directories that the app should serve need a route + a `?v=`
  cache token from birth — see HAZARDS.md §map.js and the tile-route
  precedents in `landslidescience/urls.py`.
- Anything served uncompressed and large (multi-MB JSON) should be
  gzipped or further decimated first — raise it before shipping.
- The staging dir is size-unbounded in principle; the droplet is not.
  Check `df -h /opt` before large pushes.
