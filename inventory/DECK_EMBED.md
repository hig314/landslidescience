# The inventory map as a live slide: what the deck needs from this page

Written 2026-11-22 from the Presentation project (`~/Claude_projects/Presentation`, private
repo `hig314/presentation`) as a brief for work done here. Nothing in this repo has been
changed; this file is the only thing added, and it is not committed.

**Added 2026-11-22: Part 2, at the end of this file**, covers serving finished decks from
this site at `/decks/<name>/` (decided by Hig that day), drafts, presenter notes, and what
offline use will later ask of the map pages. Part 1 below is the original brief.

**The goal.** A slide in a talk shows `/inventory/` live in place of a screenshot. Each click
of the presenter's remote moves the map to the next prepared view (camera, basemap, wiper,
overlays, filters, an open record); between clicks the presenter can use the map by hand.
The same deck will later be published, so that someone watching a recording can explore
the same map on the same slide. First use: slide 30 of the `gc_outline` talk, within the next
two weeks.

**How a slide holds the map.** The deck puts the page in an `<iframe>` and talks to it with
`postMessage`. The page includes one small file, `deck-embed.js`, and gives it three
functions. The file and its header (the protocol's specification) are at
`~/Claude_projects/Presentation/lib/deck-embed.js`; copy it into this repo's static files
(suggested: `inventory/static/inventory/js/vendor/deck-embed.js`) and re-copy if it changes.

```js
DeckEmbed.init({
  getState: function () { return /* the whole view as one string */; },
  setState: function (state, opts) { /* show it; opts.animate = travel there, or cut */ }
});
DeckEmbed.ready();      // once the map can be shown and will obey setState
DeckEmbed.changed();    // whenever the view has changed (coalesced; safe to call often)
```

`DeckEmbed.embedded` is true when the page is in a frame. The deck stores the strings
`getState` returns and sends them back through `setState`, one per click. It never looks
inside them, so the format is entirely this page's: the existing hash grammar
(`ls_hash.js`) is the natural choice, and it makes every captured step a working link.

## What blocks it today

The site answers `X-Frame-Options: DENY` (Django's `XFrameOptionsMiddleware`,
`settings.py`), so no browser will show any page of it in a frame. Checked 2026-11-22
against production.

## What is needed

### Must have, for the slide to work at all

1. **An embed mode that may be framed.** Suggested: `/inventory/?embed=1`. For that
   response only, drop `X-Frame-Options` (`@xframe_options_exempt` or the equivalent in the
   view) and send `Content-Security-Policy: frame-ancestors …` naming who may frame it.
   The deck is served from `http://127.0.0.1:<port>` or `http://localhost:<port>` while
   presenting (`frame-ancestors http://127.0.0.1:* http://localhost:*` is valid), and later
   from this site itself at `/decks/` (Part 2), so `'self'` joins the list. Every other page keeps DENY.
   If anything caches whole responses, it must key on the query string.

2. **Embed mode is the signed-out view, always.** Decks are published, and a talk is
   projected, so nothing gated may appear. Do not rely on the browser withholding the
   session cookie: it does so only for cross-*site* frames, and ports do not make a site.
   A deck at `localhost:8792` framing dev at `localhost:8001` is same-site and the
   editor's cookie is sent, as it would be for a deck ever hosted under
   `landslidescience.org`. So embed mode should render, and fetch its data, as anonymous
   whoever is signed in (and drop the editing tools with it). How to carry that through the
   API calls the page makes is a design choice for this side.

3. **`getState` must describe the whole view.** This is the one place the existing
   machinery does not already fit. `_viewStateString('url')` writes the view "as short as
   it can be", and a list param that is absent means *leave alone*. Stepping from view A to
   view B then leaves A's overlays on if B happens to have none. The `'view'` mode (stored
   default views) is complete in that sense, but leaves out filters and the explorer
   selection on purpose. A slide wants a third mode, complete *and* including filters:
   everything a member of the audience would see change. Suggested: a `'deck'` mode in
   `_VIEW_PARAMS.collect`, used only by `getState`.

4. **An open record should be part of the state.** `id=` is read but never written
   ("opening a record strips it"). For a slide, "this landslide's panel is open" is
   something a step needs to capture and restore, and "no panel open" likewise. Open
   analysis panels (`an=`) and the sidebar tab already travel.

5. **`setState` applies a string without a reload.** The hashchange path
   (`_applyViewState(parseHashState(…), 'hash')`) is nearly this already. Two additions:
   honour `opts.animate` (false = `jumpTo`, no flight: used when the slide first appears
   and when stepping backwards quickly), and close whatever the new state does not list
   (see 3 and 4).

6. **Report changes and readiness.** Call `DeckEmbed.changed()` from `writeHashState`
   (every view change already passes through it). Call `DeckEmbed.ready()` when the map
   has loaded and the first data layers are drawn; until then the slide shows a still image
   in the map's place.

7. **The remote's keys go to the deck.** Once the presenter has clicked the map, key
   presses land in the frame. `deck-embed.js` catches ArrowLeft, ArrowRight, PageUp and
   PageDown in the capture phase (outside text inputs) and hands them to the deck, so
   MapLibre's arrow-key panning will not fire in embed mode. Nothing to do here beyond
   knowing it; `+`/`-` zoom and everything else are untouched.

8. **Leave the browser's saved views alone in embed mode.** Do not read `ls_map_view` or
   `ls_map_session` on load (a slide must open at the view it was given, on any machine),
   and do not write them (rehearsing a talk should not change the map the presenter finds
   tomorrow). The address bar inside the frame does not matter either way.

### Should have, for it to look right on a projector

9. **Page chrome.** Hide the site header in embed mode. The sidebar is a choice (see the
   questions below): hidden, collapsed with a handle, or as it is. Whatever is chosen, the
   layout must fill the frame exactly, with no page scrollbars.

10. **The frame's size.** A slide is laid out at 1920 × 1080 CSS pixels and scaled to the
    screen, so the page always sees a 1920 × 1080 window (or smaller if the map is given
    only part of a slide). Check that controls and labels are legible at that size from the
    back of a room; an embed-only type scale may be wanted.

11. **Loading while hidden.** The deck loads a live slide a few slides early so it is ready
    on arrival, which means the map initialises in a frame that is not yet visible. It
    must come up correctly sized when shown.

12. **Flights that suit a talk.** A step between two distant views is watched by a room.
    A predictable duration (and a cap on it) matters more than it does for a link click.

### Later

- **A settled signal** (tiles in, nothing moving), so a recording or a screenshot of the
  slide can wait for the map. Would be a new message; not in the protocol yet.
- **A copy with no network.** Venue wifi is the risk a still-image fallback covers. The
  snapshot bundles (`/inventory/archive/<slug>/`) may be able to serve as a same-origin
  copy shipped with a deck; what they include (tiles?) decides whether that is worth
  pursuing.
- **Attribution.** Basemap and data credits must stay visible in embed mode: published
  decks are public pages.
- **The same for `/glaciers/` and `/lidar/`,** which share `ls_hash.js`.

## Testing from this side

The Presentation project has a page that frames any URL as a slide would and shows every
message. Start its server and open it against dev:

```
/opt/anaconda3/bin/python3 ~/Claude_projects/Presentation/tools/serve.py
http://127.0.0.1:8792/tools/embed_test.html?src=http://localhost:8001/inventory/?embed=1
```

(Using `127.0.0.1` for the deck and `localhost` for dev makes the frame cross-site, as it
will be in a real talk; see item 2 for why the other combination behaves differently. If
port 8792 is taken, pass another port as an argument.) The page reports when `deck:ready`
arrives, has a box for sending a view string with or without animation, a button that asks
for the current view, and a log. With no `src` it frames a stand-in figure, which shows the
protocol working end to end.

Done means: the status line turns to "ready"; moving the map by hand logs `deck:state`
messages; **Get current** fills the box with a string; sending that string after moving
away restores the view exactly, including overlays turned off, filters, and an open record;
arrow keys pressed after clicking the map log `deck:key` and do not pan it; a signed-in
editor sees only the public map.

## What the slide will be

For reference, the deck's side (already built and checked with the stand-in figure):

```json
{"type": "live", "src": "https://landslidescience.org/inventory/?embed=1",
 "states": ["map=5/61.2/-150.1&base=…", "map=11/60.9/-148.9&base=…&ov=…", "…"],
 "fallback": "assets/Screenshot_2026-11-14_at_12.48.33_PM-10081.png",
 "x": 0, "y": 0, "w": 1920, "h": 1080}
```

One string per click. The first is sent without animation when the slide appears.

## Questions this side decides

1. The embed URL's form (`?embed=1`, a separate path such as `/inventory/embed/`, or both).
2. What is on screen in embed mode: sidebar hidden, collapsed, or present; which map
   controls stay.
3. How anonymity is enforced for the page's own data requests (item 2).
4. Who may frame it once decks are published (a fixed list of origins, or anyone, given
   that the view is public and signed out).
5. Whether a `'deck'` state mode (item 3) is the right shape, or stepping should instead
   reset to a baseline before applying each string.

## Constraints from this repo that apply

- The hash grammar is additive only (`ls_hash.js` header); a new mode that *writes* more
  params is fine, a new reading of an old token is not. `_VIEW_STATE_RE` in `views.py` is
  the server-side party to keep in step if a new character appears.
- Snapshot builds render `home.html`; an embed flag must not change what they produce.
- Nothing here is deployed without Hig's approval, and prod needs the build sequence in
  `HAZARDS.md`, not a restart.

---

# Part 2: decks served from this site at `/decks/<name>/`

**Superseded the same day.** `/decks/` is built, and decks are now edited on the site, not
published to it as static copies. The current design is `docs/DECKS.md`; what follows is the
original request, kept for its reasoning. The section on map caching is still open.

Written 2026-11-22, after Hig decided: published decks live at
`landslidescience.org/decks/<name>/`; presenter notes stay with the presenter; a deck is
walled off until its creator publishes it. Facts about this repo below were read from it on
that date (HEAD c49ada0) and nothing here was changed. The Presentation side is built and
was checked on a scratch deck in Chrome, including with the server stopped.

**The aim.** Open `https://landslidescience.org/decks/gc_outline/` on any computer with a
current browser and give the talk: no install, the right fonts, and after one visit with a
connection, no dependence on the venue's network for the deck itself.

## What the Presentation project produces

`tools/publish.py <deck> [--public | --draft]` writes a folder there (nothing is uploaded):

```
published/<deck>/
  site/           everything a browser may fetch, self-contained and relative-addressed:
                  index.html, deck.json, sw.js, lib/, vendor/, fonts/, assets/, live/
  private/        notes.json: presenter notes, {"<slide index>": "text"}
  publish.json    {"name", "title", "public": true|false, "version", "built", "bytes",
                   "files": [[path, bytes, sha1], …], "live": […]}
```

- `site/` works from any static host as it is. Live figures that were on a dev server are
  addressed root-relative (`/lidar/compare/?…&embed=1`), so they resolve to whichever
  instance serves the deck: dev frames dev, production frames production.
- `site/sw.js` is a service worker whose scope is the deck's folder. It saves every file in
  `publish.json` in the browser and then serves the deck from there; it cuts byte ranges
  out of the saved movie itself. It never saves `notes.json`.
- The page asks for `notes.json` next to `index.html` on every load and uses it if the
  answer is 200. That request is how notes reach the presenter and nobody else.
- `gc_outline` today: 88 files, 253 MB, of which one movie is 124 MB.

## What is needed here

### Must have

1. **A place on disk.** Suggested `data/decks/<name>/`, holding the three items above
   exactly as built, shipped the way the rest of `data/` is (rsync; it is bind-mounted, so
   updating a deck needs no deploy). **Disk is the constraint**: the droplet has roughly
   5 to 7 GB free (HAZARDS.md, CLAUDE.md here) and one deck is a quarter of a gigabyte. See
   question 1.

2. **Routes** (`/decks/` is free in the root `urls.py`):
   `decks/<name>/` and `decks/<name>/<rest>`, modelled on `snapshot_serve`
   (`inventory/views.py:2630`), with these differences:
   - **Serve only what `publish.json` lists** (plus `sw.js`), from `site/`. That is the
     traversal guard, and it keeps `private/` and `publish.json` unreachable by
     construction. The trailing slash on `decks/<name>/` is required: every address in the
     deck is relative, and the service worker's scope is that folder.
   - **Byte ranges are required.** `snapshot_serve` uses `FileResponse`, which ignores
     `Range`; Safari will not play a movie served that way, and nothing can seek. Reuse
     `lidar_serve.serve_ranged` or the logic in `glaciers/videos.serve_video`.
   - **`notes.json`** answers from `private/notes.json` for the deck's owner, 404 for
     everyone else, with `Cache-Control: private, no-store`.
   - **Headers.** `sw.js`, `index.html` and `deck.json`: `Cache-Control: no-cache`. Other
     files are not content-named, so `no-cache` with an `ETag` (the sha1 in `publish.json`)
     is the safe choice; the service worker makes repeat requests rare. `sw.js` must be
     served from the deck's own folder with a JavaScript content type.
   - **`X-Frame-Options: SAMEORIGIN` on deck files, not DENY.** A deck frames its own
     local figures (`live/…/index.html` inside `site/`), and the default DENY would blank
     them. Setting the header on the response stops the middleware adding DENY.

3. **The wall around drafts.** `publish.json` carries `"public"`. When false, the whole
   deck (every file, and the `/decks/` listing if there is one) answers only to the owner;
   everyone else gets 404, not 403, so a draft's name confirms nothing. Responses for a
   draft: `Cache-Control: private`. "Owner" is this side's call; the existing precedent is
   `lidar_serve._gate` (superuser only). The Presentation side flips the flag with
   `publish.py --public` / `--draft` and a re-sync; an admin switch here instead would
   also work, and then `publish.json`'s flag should be ignored.

4. **Embed pages must render signed-out when framed by a deck on this site.** Part 1,
   item 2 becomes the normal case, not the edge case: deck and map now share an origin, so
   the session cookie is always sent to the frame. `lidar/compare` already fetches with
   `credentials: 'omit'` in embed mode; the page's own server-rendered parts (anything
   templated from `request.user`) need the same treatment, and so does `/inventory/?embed=1`
   when it exists. `EMBED_FRAME_ANCESTORS` can then be `'self'` plus the local dev origins.

5. **Embed mode deployed.** The talk needs `/lidar/compare/?embed=1` (and later the
   inventory's) on production. Hig's go-ahead, and the build sequence in `HAZARDS.md`.

### Should have

6. **A `/decks/` index**: public decks for anyone, drafts as well for the owner, from each
   `publish.json` (`title`, `built`, `slides`).
7. **Robots.** Whether public decks are crawlable (`_ROBOTS` in the root `urls.py`).
8. **Backups.** Whether `data/decks/` joins `site_backup`. The Presentation repo does not
   track images or movies, so a published deck may be the only second copy of them.

### Things to know

- **A saved deck outlives the wall.** Once a browser has saved a deck for offline use, it
  opens there without asking the server: after the owner signs out on a borrowed computer,
  and after a public deck is made a draft again, for anyone who had already loaded it.
  Notes are never saved, so they do not linger. Clearing site data removes the copy.
- **Service workers need https or localhost.** Production and dev both qualify.
- **Republishing.** A changed deck gets a new `version`; a browser holding the old one
  shows the new deck at once when online (the page and `deck.json` are fetched fresh), and
  swaps its saved copy in the background.
- **Fonts.** Decks carry Liberation fonts (SIL OFL 1.1, licence file included in
  `site/fonts/`). Nothing to do here.

## Later: maps that survive a bad connection (not needed yet)

The deck's service worker cannot help a map in a frame: a frame is its own page, governed
by a service worker whose scope covers *its* address. So map caching has to be registered
by this site's embed pages. Outline of what I would ask for when that thread starts:

- **A service worker for embed mode**, served from this site with a scope covering the
  embed pages (`/lidar/compare/`, `/inventory/`), registered only when `?embed=1`. It
  saves what the page fetches while a deck "prepares" and answers from that copy when the
  network fails. I can write it from the Presentation side; it has to be hosted here.
- **What it would have to save**, from reading `lidar_compare.html` and the modules it
  loads: MapLibre and PMTiles from `unpkg.com`; terrain from `tiles.mapterhorn.com`;
  lidar PMTiles, COGs and overlays from `lidar.landslidescience.org` **by byte range**
  (the hard part: each range saved as its own entry); ArcticDEM from `di-pgc.img.arcgis.com`;
  the basemap hosts in `basemaps.js`; same-origin `/lidar/catalog.geojson` and the
  `/inventory/api/…` calls. Cross-origin responses can only be saved usefully where the
  host sends CORS headers; `lidar.landslidescience.org` does, the basemap hosts need
  checking one by one.
- **A "settled" message** in the protocol (Part 1, Later): the deck steps through every
  saved view of every figure and needs to know when each has finished loading before
  moving on. `map.once('idle')` after the state is applied is probably all it takes.
- **Vendoring MapLibre and PMTiles** into static files, in place of unpkg, would remove
  one outside dependency from every talk regardless of caching.

## Questions this side decides

1. **Where big files live.** Options: (a) everything on the droplet under `data/decks/`
   (simplest; a few decks fit, not many); (b) movies on R2, as lidar overlays are, with
   `publish.py` rewriting their addresses (not built; the service worker would then save a
   cross-origin file, which needs CORS and range headers from R2, as lidar already has);
   (c) cap deck images at a smaller size on publish (the 4096 px cap could come down;
   images are 124 MB of this deck). I would start with (a) for the one talk.
2. Who counts as a deck's owner (superuser only, a group, or per deck).
3. Whether the public/draft switch lives in `publish.json` or in an admin page here.
4. Whether editing a deck on the site (behind sign-in) is ever wanted. It would mean
   porting three small endpoints from `tools/serve.py`; not proposed now.

## Testing from this side

```
cd ~/Claude_projects/Presentation
/opt/anaconda3/bin/python3 tools/publish.py gc_outline          # a draft
rsync -a published/gc_outline/ ~/Claude_projects/landslidescience/data/decks/gc_outline/
```

Then on dev, signed in as the owner, `http://localhost:8001/decks/gc_outline/`. Done means:
the deck opens and a mark at lower right reaches "Ready offline"; the movie on slide 31
plays and seeks; the lidar figure on slide 30 frames dev's own `/lidar/compare/`; the relief
figure on slide 37 shows (it is a frame inside the deck: the SAMEORIGIN point above);
signed out, every address under `/decks/gc_outline/` is 404, `notes.json` included; after
`publish.py gc_outline --public` and a re-sync, signed out, the deck opens and `notes.json`
is still 404; with dev stopped, a reload of the deck still works.
