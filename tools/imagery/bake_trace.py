#!/usr/bin/env python3
"""bake_trace.py -- pre-bake an imagery GeoTIFF into one PMTiles file locally.

The server-side path (upload the original, bake on the droplet) costs the
droplet minutes of CPU, hundreds of MB of PNG tiles per render mode, and a
slow upload of the original. This does the identical bake here, writes lossy
WebP tiles (imagery tolerates it; terrain-RGB would not), packs them into a
single PMTiles file, and writes a JSON sidecar the upload form registers from.

    tools/imagery/bake_trace.py scene.tif --render nrg [--quality 80]
        [--max-zoom 14] [--fmt webp|png] [--title ...] [--date YYYY-MM-DD]
        [--out DIR]

Output: <out>/<stem>.<render>.pmtiles + <stem>.<render>.json.
Uses inventory/raster_tiles.py unchanged (Django settings stubbed), so what
you see locally is exactly what the server would have produced.
"""
import argparse, json, os, re, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _v in ("PROJ_LIB", "PROJ_DATA"):
    os.environ.pop(_v, None)
sys.path.insert(0, str(ROOT))

# raster_tiles reads exactly one thing from Django: settings.BASE_DIR. Stub
# the module so the tool runs on a plain Python with rasterio and no Django.
import types  # noqa: E402
_dj = types.ModuleType("django"); _conf = types.ModuleType("django.conf")
_conf.settings = types.SimpleNamespace(BASE_DIR=ROOT)
_dj.conf = _conf
sys.modules.setdefault("django", _dj); sys.modules.setdefault("django.conf", _conf)
from inventory import raster_tiles as rt  # noqa: E402

PMTILES = Path(os.environ.get("PMTILES_BIN", "/opt/homebrew/bin/pmtiles"))


def date_from_name(name):
    m = re.search(r'(?:^|[^\d])((?:19|20)\d{2})[-_]?(0[1-9]|1[0-2])[-_]?(0[1-9]|[12]\d|3[01])(?!\d)', name)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def scene_id_from_name(name):
    m = re.match(r'(\d{8}_\d{6}_\d{2}_[0-9a-f]{4})', name, re.I)
    return f"PlanetScope {m.group(1)}" if m else ""


def dir_to_mbtiles(tiles, mb_path, title, min_zoom, max_zoom, fmt, bounds=None):
    if mb_path.exists():
        mb_path.unlink()
    con = sqlite3.connect(mb_path)
    con.executescript("""
        PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;
        CREATE TABLE metadata (name TEXT, value TEXT);
        CREATE TABLE tiles (zoom_level INTEGER, tile_column INTEGER,
                            tile_row INTEGER, tile_data BLOB);
    """)
    n = 0
    for zdir in sorted(tiles.iterdir()):
        if not zdir.is_dir() or not zdir.name.isdigit():
            continue
        z = int(zdir.name)
        for xdir in zdir.iterdir():
            if not xdir.is_dir() or not xdir.name.isdigit():
                continue
            x = int(xdir.name)
            rows = []
            for tile in xdir.iterdir():
                if not tile.stem.isdigit() or tile.suffix.lstrip('.') != fmt:
                    continue
                y = 2 ** z - 1 - int(tile.stem)          # XYZ -> TMS
                rows.append((z, x, y, tile.read_bytes()))
            if rows:
                con.executemany("INSERT INTO tiles VALUES (?,?,?,?)", rows)
                n += len(rows)
        con.commit()
    con.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
    meta = [("name", title), ("format", fmt), ("type", "overlay"),
            ("minzoom", min_zoom), ("maxzoom", max_zoom)]
    if bounds:
        # Without these `pmtiles convert` stamps the world into the header,
        # and the server registers the upload from that header.
        w, s_, e, n = bounds
        meta += [("bounds", f"{w},{s_},{e},{n}"),
                 ("center", f"{(w + e) / 2},{(s_ + n) / 2},{min_zoom}")]
    for k, v in meta:
        con.execute("INSERT INTO metadata VALUES (?,?)", (k, str(v)))
    con.commit(); con.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tif")
    ap.add_argument("--render", default="auto", choices=["auto", "nrg", "rgb", "gray"])
    ap.add_argument("--fmt", default="webp", choices=["webp", "png"])
    ap.add_argument("--quality", type=int, default=80)
    ap.add_argument("--max-zoom", type=int, default=None, help="cap the finest zoom (downsample)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--out", default=None, help="output dir (default: next to the input)")
    a = ap.parse_args()

    src = Path(a.tif).resolve()
    stem = src.stem
    render = rt.resolve_render(str(src), a.render)
    out_dir = Path(a.out) if a.out else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    title = a.title or stem
    date = a.date or date_from_name(stem)
    source = a.source if a.source is not None else scene_id_from_name(stem)

    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="bake_", dir=out_dir) as td:
        tiles = Path(td) / "tiles"
        print(f"baking {src.name} as {render} ({a.fmt} q{a.quality}"
              f"{', max zoom ' + str(a.max_zoom) if a.max_zoom else ''}) ...", flush=True)
        meta = rt._bake(src, tiles, render=render, fmt=a.fmt, quality=a.quality,
                        max_zoom_cap=a.max_zoom)
        mb = Path(td) / "t.mbtiles"
        n = dir_to_mbtiles(tiles, mb, title, meta["min_zoom"], meta["max_zoom"], a.fmt,
                           bounds=(meta["bounds_w"], meta["bounds_s"], meta["bounds_e"], meta["bounds_n"]))
        pm = out_dir / f"{stem}.{render}.pmtiles"
        subprocess.run([str(PMTILES), "convert", str(mb), str(pm)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    side = {
        "title": title, "image_date": date, "source_note": source, "render": render,
        "format": a.fmt, "quality": a.quality if a.fmt == "webp" else None,
        "bounds_w": meta["bounds_w"], "bounds_s": meta["bounds_s"],
        "bounds_e": meta["bounds_e"], "bounds_n": meta["bounds_n"],
        "min_zoom": meta["min_zoom"], "max_zoom": meta["max_zoom"],
        "tile_count": n, "pmtiles_bytes": pm.stat().st_size,
        "original": str(src), "baked_at": int(time.time()),
    }
    (out_dir / f"{stem}.{render}.json").write_text(json.dumps(side, indent=1))
    print(f"-> {pm.name}: {n} tiles z{meta['min_zoom']}-{meta['max_zoom']}, "
          f"{pm.stat().st_size / 2**20:.1f} MB, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
