# Talks at `/decks/`

Slide decks that are stored, presented and edited on this site. Added
2026-11-22. The serving code is `landslidescience/decks_serve.py` (read its
docstring first); the routes are in the root `urls.py`. The runtime that draws
and edits a deck comes from a separate, private project (Presentation) and is
kept here as a copy in `landslidescience/decks_rt/`.

## The life of a deck (Hig, 2026-11-22)

1. **Draft.** A deck starts as a draft: imported from Keynote on the Mac and
   sent here (`tools/site.py push` in the Presentation project), or made empty
   from `/decks/`. A draft is private to its owner, who can open it on any
   computer by signing in, and edit it in place (press E). It can stay a draft
   through any amount of refining and rehearsal.
2. **Published.** The owner publishes it from `/decks/`. It is then public and
   nothing else changes: same address, same slides. **A published deck is
   constant**: no request writes to it.
3. **Revising a published deck** starts with a copy (a new draft). Then either
   - **publish the copy** as a deck of its own; the original is untouched; or
   - **replace the original with the copy.** The original's address stays and
     its slides change for everyone. This is behind a typed confirmation in a
     "Danger zone", because the public may take a published deck to be fixed,
     and a recording or screen capture made against it will no longer match.
     It is meant for small corrections (a co-author's name, an attribution).
     The replaced version is kept in the deck's `history/`, and the change is
     recorded in `meta.json` (`revised`).

## On disk

```
data/decks/<name>/
  deck.json      the deck, presenter notes included
  meta.json      {"owner", "state": "draft"|"published", "created", "published",
                  "copy_of", "revised": [{"at", "from", "by"}]}
  assets/ media/ live/ vendor/     files the slides use
  history/       earlier deck.json files: one per ten minutes of editing (200
                 kept), and the version before each replacement
data/decks/.trash/                 discarded drafts and merged copies; emptied by hand
```

Not in git or the image, like the rest of `data/`. A copy of a deck is made
with hard links, so it costs no disk until its files diverge. One deck with
movies can be a quarter of a gigabyte; the droplet's free disk is the limit.
Putting movies on R2 is the way out and is not built. `data/decks/` should be
in the site backup: once a deck is edited here, this is its only copy.

## Rules the code keeps

- **Owner** = the account named in `meta.json`, or a superuser. Only
  superusers create decks.
- **A draft is 404 to everyone else** at every address (not 403: the name
  confirms nothing), and is not listed.
- **Presenter notes go to the owner only.** `deck.json` is always sent without
  them; `notes.json` is owner-only and `no-store`. `history/` and `meta.json`
  are never served. File paths are judged after resolving them (a request for
  `assets/../history/…` was a real hole, closed 2026-11-22).
- **Writes** are the owner's, on a draft: `PUT deck.json` (refused with 409 if
  the deck changed since the page loaded it), `PUT assets/<file>` (50 MB cap;
  movies arrive by rsync, never through the site), `POST copy` (files brought
  from another deck when slides are pasted across). All CSRF-checked.
- **Byte ranges** through `lidar_serve.serve_ranged` (movies must seek; Safari
  will not play one otherwise), with its CORS headers removed.
- **`X-Frame-Options: SAMEORIGIN`** on deck files, because a deck frames
  figures inside it. Every other page of the site keeps DENY.
- **Robots:** `/decks/` is disallowed for now, published decks included.

## Things to know

- **Offline copy.** A deck registers a service worker scoped to its folder
  (`sw.js`, generated per request from `decks_rt/lib/sw.js` and the deck's file
  list) and keeps its files in the browser, so a talk does not depend on the
  venue's network. Editing needs a connection. A saved copy opens offline
  without asking the server, so on a borrowed computer a draft stays readable
  there until the browser's site data is cleared; online, a 404 from the
  server wins over the saved copy. Notes are never saved. Only pictures and
  movies a slide names are saved; large files arrive in 2 MB byte-range pieces
  so an interrupted save resumes.
- **Practice (live maps offline).** A deck's map figures are this site's own
  pages in frames (`/inventory/?embed=1`, `/lidar/compare/?…&embed=1`), which
  the deck's worker cannot reach. When the owner switches Practice on (the ☰
  menu), the deck page registers a second worker, `/decks/_rt/lib/mapsw.js`,
  over `/inventory/` and `/lidar/compare/`; `decks_serve.runtime` sends
  `Service-Worker-Allowed: /` for that one file so it may take those scopes.
  It acts only on a framed page whose address has `embed=1` and on that
  page's workers: any other page under those folders goes to the network
  untouched. While Practice is on it keeps what the maps fetch (tiles, lidar
  byte ranges, the page's files) in that browser, within limits, and the
  owner reviews each run; afterwards the saved files are answered first.
  With nothing saved the worker is unregistered. It lives in one browser
  only: nothing is stored on the server. The header of `mapsw.js` is the
  specification.
- **Display copies.** A picture is not sent as uploaded. `deck.json` goes out
  with `site.files` (`{path: "v=<version>&w=<width>"}`) and the page asks with
  that query; `w` gets a WebP made from the original at twice the widest the
  deck shows it (times the slide's zoom), one of 640/1280/1920/2560/3840. Made
  on first request into the deck's `.display/` (never served by name, safe to
  delete, rebuilt when missing); the original is never changed and is what a
  request without `w` gets. A request whose `v` is the file's current version
  is answered `immutable`. `PUT deck.json` answers 200 with the new map, so a
  picture enlarged in the editor is fetched again at the larger width.
- **gunicorn runs threaded** (`gthread`, 2 workers x 8 threads; `Dockerfile`).
  With one-request-at-a-time workers, two slow downloads of deck files held the
  whole site (2026-11-25).
- **Live map slides** frame this site's own embed pages
  (`/lidar/compare/?embed=1`), addressed root-relative. Embed mode must render
  signed-out whoever is signed in (`lidar_serve.compare` does): deck and map
  share an origin, so the session cookie always reaches the frame.
- **The owner's menu.** On a deck, the owner gets a ☰ at upper right (shown only when the mouse is pushed against the right edge of the window or M is pressed, so it never covers slide content; in the editor it sits in the editor's bar): edit, publish, copy, and links to the Danger zone on `/decks/`. It makes the same POSTs to `/decks/_do` as the list page.
- **The runtime is shared.** `/decks/_rt/` serves `landslidescience/decks_rt/`
  to every deck, so a runtime change reaches published decks too. Update it
  with `tools/site.py runtime` in the Presentation project (which overwrites
  the folder), then deploy. Do not edit `decks_rt/` here.
- **Fonts.** A deck arrives in open fonts (Liberation, in `decks_rt/fonts/`,
  SIL OFL 1.1); the substitution is part of the import on the Mac.

## From the Mac (Presentation project)

```
tools/site.py push <deck> [--prod]     first arrival, as a draft (refuses to overwrite)
tools/site.py pull <deck> [--prod]     a copy back down, for git or the local tools
tools/site.py runtime                  refresh landslidescience/decks_rt/
```
