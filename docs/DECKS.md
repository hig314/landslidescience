# Talks at `/decks/`

Published slide decks, served as static folders. Added 2026-11-22. The decks are
built in a separate project (Presentation, `tools/publish.py` there); this site
only stores and serves them. The serving code is `landslidescience/decks_serve.py`
(read its docstring first), the routes are in the root `urls.py`.

## On disk

```
data/decks/<name>/
  site/          everything a browser may fetch (index.html, deck.json, sw.js,
                 lib/, vendor/, fonts/, assets/, media/, live/)
  private/       notes.json: presenter notes
  publish.json   {"public": true|false, "title", "slides", "built", "version",
                  "files": [[path, bytes, sha1], ...]}
```

Like the rest of `data/`, it is not in git or the image: a deck arrives by rsync
of the folder `publish.py` wrote, and a re-sync needs no deploy or restart (the
manifest is re-read when its mtime changes). One deck can be a quarter of a
gigabyte (movies); the droplet's free disk is the limit on how many fit. Moving
movies to R2 is the way out if it comes to that and is not built.

## Rules

- **Only paths listed in `publish.json` (plus `sw.js`) are served, from `site/`.**
  Nothing else under the deck's folder is reachable.
- **Draft until `"public": true`.** A draft answers the owner only; everyone else
  gets 404 at every address, and `/decks/` does not list it. The flag is set on
  the Presentation side (`publish.py --public` / `--draft`) and arrives with the
  sync. Owner = superuser.
- **`notes.json` is owner-only always**, public deck or not, and `no-store`.
- **Byte ranges** go through `lidar_serve.serve_ranged` (movies must seek; Safari
  will not play one otherwise). Its CORS headers are removed: decks are
  same-origin.
- **`X-Frame-Options: SAMEORIGIN`** on deck files, because a deck frames figures
  that live inside it. Every other page of the site keeps DENY.
- **`/decks/<name>` redirects to the slashed form** for any name. The slash is
  required: addresses inside a deck are relative and its service worker's scope
  is the folder.
- **Robots:** `/decks/` is disallowed for now.

## Things to know

- A deck registers a service worker scoped to its own folder and keeps all its
  files in the browser. **A saved copy opens without asking the server**, so it
  outlives the draft wall in a browser that already loaded it (the owner's, after
  signing out; anyone's, after a public deck is made a draft again). Notes are
  never saved.
- Live map slides frame this site's own embed pages (`/lidar/compare/?embed=1`),
  addressed root-relative, so dev frames dev and production frames production.
  Embed mode renders signed-out whoever is signed in (`lidar_serve.compare`);
  any future embed page must do the same, because deck and map share an origin
  and the session cookie is always sent to the frame.
- Maps are not saved for offline use; only the deck's own files are.

## Publishing a deck

In the Presentation project: `tools/publish.py <name> [--public]`, then

```
rsync -a --delete published/<name>/ <this repo>/data/decks/<name>/       # dev
rsync -a --delete published/<name>/ root@<droplet>:/opt/landslidescience/data/decks/<name>/
```
