#!/opt/anaconda3/bin/python3
"""points_publish.py -- index and publish a survey's point cloud revisions.

    tools/lidar/points_publish.py index  <survey> <rev>     read every tile, write the index files
    tools/lidar/points_publish.py upload <survey> <rev> [--dry-run]
    tools/lidar/points_publish.py verify <survey> <rev>     bucket against the index
    tools/lidar/points_publish.py survey <survey> [--upload] index.json + index.html of the survey

The registry is tools/lidar/points.json; its header is the specification of the
bucket layout and of what a revision is. This tool never edits or moves a tile:
it reads them where they are, writes the index files to
$LIDAR_BUILD/points/<survey>/<rev>/, and copies both to the public bucket.

index   For each tile: the LAS header (bounds, point count, format; 375 bytes,
        no decompression), the size and an md5 of the file. The md5 pass is the
        slow part (it reads every byte) and is cached in md5.tsv beside the
        index, keyed on name + size + mtime, so a re-run is cheap. Writes
        manifest.json, tiles.geojson (footprints in WGS 84) and urls.txt. A tile
        with no points is left out of the index and is not uploaded.

upload  rclone copy of the tiles the index names, then the index files. Tiles first, so nothing
        on the bucket names a tile that is not there. rclone copy never deletes
        and skips what is already there with the same size and md5, so it
        resumes. Every tile here is under rclone's 200 MB multipart cutoff, so
        the bucket's ETag is the file's md5 and `verify` can compare them.

verify  Lists the bucket prefix and compares name, size and md5 (ETag) of every
        tile with tiles.geojson. Exit 1 on any difference.

survey  Writes points/<survey>/index.json (every revision, hosted or upstream)
        and index.html, the page a person lands on. A hosted revision is listed
        only once its manifest is on the bucket, unless --local.

Credentials: ~/.r2.env, as tools/lidar/r2_sync.sh. No bulk output: rclone logs
to a file under $LIDAR_BUILD/points/.
"""
import argparse, glob, hashlib, html, json, os, struct, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = Path(os.environ.get("LIDAR_BUILD", "/Volumes/Nunatak/lidar_build")) / "points"
PUBLIC = os.environ.get("LIDAR_POINTS_PUBLIC_BASE", "https://lidar.landslidescience.org/points")
RCLONE = "/opt/homebrew/bin/rclone"


def registry():
    return json.loads((HERE / "points.json").read_text())["surveys"]


def find(sid, rev=None):
    for s in registry():
        if s["id"] == sid:
            if rev is None:
                return s
            for r in s["revisions"]:
                if r["rev"] == rev:
                    return s, r
            sys.exit(f"{sid}: no revision {rev} in points.json")
    sys.exit(f"no survey {sid} in points.json")


def las_header(path):
    """Bounds, point count and format from a LAS/LAZ header, without laszip."""
    with open(path, "rb") as f:
        h = f.read(375)
    if h[:4] != b"LASF":
        raise ValueError(f"not a LAS file: {path}")
    major, minor = h[24], h[25]
    fmt = h[104] & 0x7F                      # bit 7 marks laszip compression
    n = struct.unpack_from("<I", h, 107)[0]
    if (major, minor) >= (1, 4):
        n = struct.unpack_from("<Q", h, 247)[0] or n
    maxx, minx, maxy, miny, maxz, minz = struct.unpack_from("<6d", h, 179)
    return {"version": f"{major}.{minor}", "point_format": fmt, "points": n,
            "bounds": [minx, miny, maxx, maxy], "z": [minz, maxz]}


def md5_of(path):
    m = hashlib.md5()
    with open(path, "rb") as f:
        while True:
            b = f.read(4 << 20)
            if not b:
                break
            m.update(b)
    return m.hexdigest()


def to_wgs84(epsg, xs, ys):
    from rasterio.warp import transform
    lon, lat = transform(f"EPSG:{epsg}", "EPSG:4326", xs, ys)
    return lon, lat


def cmd_index(sid, rev):
    s, r = find(sid, rev)
    if not r.get("hosted"):
        sys.exit(f"{sid}/{rev} is not hosted here (upstream only): nothing to index")
    files = sorted(glob.glob(os.path.join(r["local"], r["pattern"])))
    if not files:
        sys.exit(f"no tiles match {r['local']}/{r['pattern']}")
    out = BUILD / sid / rev
    out.mkdir(parents=True, exist_ok=True)
    cache_path = out / "md5.tsv"
    cache = {}
    if cache_path.exists():
        for line in cache_path.read_text().splitlines():
            name, size, mtime, md5 = line.split("\t")
            cache[(name, int(size), int(mtime))] = md5
    epsg = int(r["crs"].split(":")[1])
    feats, total_b, total_n, t0, fresh, empty = [], 0, 0, time.time(), 0, []
    zmin, zmax = float("inf"), float("-inf")
    with open(cache_path, "a") as cf:
        for i, p in enumerate(files):
            st = os.stat(p)
            name = os.path.basename(p)
            h = las_header(p)
            if h["points"] == 0:            # a tile square with no returns: nothing to publish,
                empty.append(name)          # and its all-zero header bounds would map to 0 N
                continue
            key = (name, st.st_size, int(st.st_mtime))
            md5 = cache.get(key)
            if md5 is None:
                md5 = md5_of(p)
                cf.write("\t".join(map(str, (*key, md5))) + "\n")
                cf.flush()
                fresh += 1
            x0, y0, x1, y1 = h["bounds"]
            lon, lat = to_wgs84(epsg, [x0, x1, x1, x0], [y0, y0, y1, y1])
            ring = [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]
            ring.append(ring[0])
            feats.append({"type": "Feature",
                          "geometry": {"type": "Polygon", "coordinates": [ring]},
                          "properties": {"name": name, "url": f"{PUBLIC}/{sid}/{rev}/{name}",
                                         "bytes": st.st_size, "points": h["points"], "md5": md5,
                                         "bounds": [round(v, 2) for v in h["bounds"]],
                                         "z": [round(v, 2) for v in h["z"]]}})
            total_b += st.st_size
            total_n += h["points"]
            zmin, zmax = min(zmin, h["z"][0]), max(zmax, h["z"][1])
            if (i + 1) % 250 == 0:
                print(f"  {i + 1}/{len(files)} tiles, {fresh} hashed, {time.time() - t0:.0f} s", flush=True)
    lons = [c[0] for f in feats for c in f["geometry"]["coordinates"][0]]
    lats = [c[1] for f in feats for c in f["geometry"]["coordinates"][0]]
    first = las_header(os.path.join(r["local"], feats[0]["properties"]["name"]))
    manifest = {
        "survey": sid, "title": s["title"], "revision": rev, "label": r["label"],
        "kind": r["kind"], "parent": r.get("parent"),
        "citation": s.get("citation"),
        "format": r.get("format"), "las_version": first["version"],
        "point_format": first["point_format"],
        "crs": r["crs"], "crs_name": r.get("crs_name"), "vertical_datum": r.get("vertical_datum"),
        "classes": r.get("classes"),
        "made": r.get("made"), "tool": r.get("tool"), "why": r.get("why"),
        "changes": r.get("changes"), "products": r.get("products"),
        "tiles": len(feats), "bytes": total_b, "points": total_n,
        "empty_tiles_left_out": len(empty),
        "bounds_wgs84": [round(min(lons), 5), round(min(lats), 5), round(max(lons), 5), round(max(lats), 5)],
        "z_range_m": [round(zmin, 2), round(zmax, 2)],
        "base_url": f"{PUBLIC}/{sid}/{rev}/",
        "tile_index": f"{PUBLIC}/{sid}/{rev}/tiles.geojson",
        "url_list": f"{PUBLIC}/{sid}/{rev}/urls.txt",
        "indexed": time.strftime("%Y-%m-%d"),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (out / "tiles.geojson").write_text(json.dumps(
        {"type": "FeatureCollection", "name": f"{sid} {rev}", "features": feats},
        separators=(",", ":")) + "\n")
    (out / "urls.txt").write_text("".join(f["properties"]["url"] + "\n" for f in feats))
    (out / "files.txt").write_text("".join(f["properties"]["name"] + "\n" for f in feats))
    print(f"{sid}/{rev}: {len(feats)} tiles, {total_b / 2**30:.2f} GB, {total_n:,} points "
          f"({len(empty)} empty tiles left out) -> {out}")


def rclone_env():
    env = dict(os.environ)
    for line in Path.home().joinpath(".r2.env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.replace("export ", "").strip()] = v.strip().strip("'\"")
    env.update(RCLONE_CONFIG_R2_TYPE="s3", RCLONE_CONFIG_R2_PROVIDER="Cloudflare",
               RCLONE_CONFIG_R2_ACCESS_KEY_ID=env["R2_ACCESS_KEY_ID"],
               RCLONE_CONFIG_R2_SECRET_ACCESS_KEY=env["R2_SECRET_ACCESS_KEY"],
               RCLONE_CONFIG_R2_ENDPOINT=env["R2_ENDPOINT"],
               RCLONE_CONFIG_R2_NO_CHECK_BUCKET="true")
    return env, env["R2_BUCKET"]


def cmd_upload(sid, rev, dry):
    s, r = find(sid, rev)
    out = BUILD / sid / rev
    if not (out / "tiles.geojson").exists():
        sys.exit("run `index` first")
    env, bucket = rclone_env()
    dest = f"r2:{bucket}/points/{sid}/{rev}"
    log = BUILD / f"upload_{sid}_{rev}.log"
    extra = ["--dry-run"] if dry else []
    common = ["--s3-no-check-bucket", "--log-file", str(log), "--log-level", "INFO",
              "--stats", "5m", "--stats-one-line", "--transfers", "3", "--checkers", "8"]
    # Tiles first, and exactly the tiles the index names (files.txt).
    subprocess.run([RCLONE, "copy", r["local"], dest, "--files-from", str(out / "files.txt"),
                    "--checksum", *common, *extra], env=env, check=True)
    for name in ("urls.txt", "tiles.geojson", "manifest.json"):
        subprocess.run([RCLONE, "copyto", str(out / name), f"{dest}/{name}", *common, *extra],
                       env=env, check=True)
    print(f"uploaded {sid}/{rev} -> {dest}{' (dry run)' if dry else ''}; log {log}")


def cmd_verify(sid, rev):
    s, r = find(sid, rev)
    out = BUILD / sid / rev
    want = {f["properties"]["name"]: f["properties"]
            for f in json.loads((out / "tiles.geojson").read_text())["features"]}
    env, bucket = rclone_env()
    res = subprocess.run([RCLONE, "lsjson", "--hash", f"r2:{bucket}/points/{sid}/{rev}",
                          "--s3-no-check-bucket"], env=env, check=True, capture_output=True, text=True)
    have = {o["Name"]: o for o in json.loads(res.stdout)}
    missing = [n for n in want if n not in have]
    size = [n for n in want if n in have and have[n]["Size"] != want[n]["bytes"]]
    md5 = [n for n in want if n in have and have[n]["Size"] == want[n]["bytes"]
           and (have[n].get("Hashes") or {}).get("md5") not in (None, want[n]["md5"])]
    nohash = sum(1 for n in want if n in have and not (have[n].get("Hashes") or {}).get("md5"))
    index = [n for n in ("manifest.json", "tiles.geojson", "urls.txt") if n not in have]
    print(f"{sid}/{rev}: {len(want)} tiles in the index, {len(want) - len(missing)} on the bucket; "
          f"missing {len(missing)}, wrong size {len(size)}, wrong md5 {len(md5)}, "
          f"no md5 to compare {nohash}; index files missing: {index or 'none'}")
    for n in (missing + size + md5)[:10]:
        print("  ", n)
    return 1 if (missing or size or md5 or index) else 0


def on_bucket(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "points_publish"})
        return urllib.request.urlopen(req, timeout=30).status == 200
    except Exception:
        return False


def fmt_bytes(b):
    return f"{b / 2**30:.1f} GB" if b >= 2**30 else f"{b / 2**20:.0f} MB"


def cmd_survey(sid, upload, local):
    s = find(sid)
    revs = []
    for r in s["revisions"]:
        e = {k: r[k] for k in ("rev", "label", "kind", "parent", "notes", "upstream", "made",
                               "why", "changes", "products", "format", "crs", "crs_name",
                               "vertical_datum", "classes") if r.get(k) is not None}
        e["hosted"] = bool(r.get("hosted"))
        if r.get("hosted"):
            mpath = BUILD / sid / r["rev"] / "manifest.json"
            url = f"{PUBLIC}/{sid}/{r['rev']}/manifest.json"
            if not mpath.exists() or not (local or on_bucket(url)):
                print(f"  {sid}/{r['rev']}: not on the bucket yet, left out")
                continue
            m = json.loads(mpath.read_text())
            e.update(manifest=url, tile_index=m["tile_index"], url_list=m["url_list"],
                     base_url=m["base_url"], tiles=m["tiles"], bytes=m["bytes"], points=m["points"])
        elif r.get("points"):
            e["points"] = r["points"]
        revs.append(e)
    out = BUILD / sid
    out.mkdir(parents=True, exist_ok=True)
    index = {"survey": sid, "title": s["title"], "citation": s.get("citation"),
             "page": f"{PUBLIC}/{sid}/index.html", "revisions": revs,
             "updated": time.strftime("%Y-%m-%d")}
    (out / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    esc = html.escape
    parts = [f"<!doctype html><html lang=en><meta charset=utf-8>"
             f"<meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<meta name=robots content=noindex><title>{esc(s['title'])}: point cloud</title>"
             "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:46rem;margin:2rem auto;"
             "padding:0 1rem;color:#222}h2{margin-top:2rem;border-top:1px solid #ccc;padding-top:1rem}"
             "code,pre{background:#f3f3f3;padding:.1rem .3rem;border-radius:3px}pre{padding:.6rem;"
             "overflow:auto}dt{font-weight:600;margin-top:.5rem}dd{margin-left:1rem}</style>"
             f"<h1>{esc(s['title'])}: point cloud</h1><p>{esc(s.get('citation') or '')}</p>"
             "<p>Each revision below is a complete version of the point cloud. A revision is never "
             "changed once published; a correction is a new revision. Hosted by "
             "<a href='https://landslidescience.org/lidar/'>landslidescience.org</a>.</p>"]
    for e in revs:
        parts.append(f"<h2>{esc(e['rev'])}: {esc(e['label'])}</h2><dl>")
        if e.get("parent"):
            parts.append(f"<dt>Made from</dt><dd>{esc(e['parent'])}</dd>")
        if e.get("why"):
            parts.append(f"<dt>Why</dt><dd>{esc(e['why'])}</dd>")
        if e.get("changes"):
            parts.append("<dt>What changed</dt><dd><ul>" +
                         "".join(f"<li>{esc(c)}</li>" for c in e["changes"]) + "</ul></dd>")
        if e.get("notes"):
            parts.append(f"<dt>Notes</dt><dd>{esc(e['notes'])}</dd>")
        if e.get("points"):
            parts.append(f"<dt>Points</dt><dd>{e['points']:,}</dd>")
        if e.get("format"):
            parts.append(f"<dt>Format</dt><dd>{esc(e['format'])}</dd>")
        if e.get("crs"):
            parts.append(f"<dt>Coordinates</dt><dd>{esc(e['crs'])}, {esc(e.get('crs_name') or '')}; "
                         f"heights {esc(e.get('vertical_datum') or 'not stated')}</dd>")
        if e.get("classes"):
            parts.append("<dt>Classes</dt><dd>" + "; ".join(
                f"{esc(k)} = {esc(v)}" for k, v in e["classes"].items()) + "</dd>")
        if e.get("made"):
            parts.append(f"<dt>Made</dt><dd>{esc(e['made'])}</dd>")
        if e["hosted"]:
            parts.append(
                f"<dt>Download</dt><dd>{e['tiles']:,} tiles, {fmt_bytes(e['bytes'])}. "
                f"<a href='{esc(e['tile_index'])}'>Tile index (GeoJSON)</a> with each tile's "
                f"footprint, size and md5 · <a href='{esc(e['url_list'])}'>list of every tile URL</a> · "
                f"<a href='{esc(e['manifest'])}'>manifest (JSON)</a>"
                f"<pre>wget -i {esc(e['url_list'])}</pre>"
                "Open the tile index in QGIS to pick tiles by place.</dd>")
        for u in e.get("upstream") or []:
            parts.append(f"<dt>Source</dt><dd><a href='{esc(u['url'])}'>{esc(u['label'])}</a></dd>")
        parts.append("</dl>")
    (out / "index.html").write_text("".join(parts) + "\n")
    print(f"{sid}: {len(revs)} revision(s) -> {out}/index.json, index.html")
    if upload:
        env, bucket = rclone_env()
        for name in ("index.json", "index.html"):
            subprocess.run([RCLONE, "copyto", str(out / name), f"r2:{bucket}/points/{sid}/{name}",
                            "--s3-no-check-bucket"], env=env, check=True)
        print(f"  uploaded to points/{sid}/")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["index", "upload", "verify", "survey"])
    ap.add_argument("survey")
    ap.add_argument("rev", nargs="?")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--local", action="store_true",
                    help="survey: list hosted revisions from the local index without asking the bucket")
    a = ap.parse_args()
    if a.cmd != "survey" and not a.rev:
        ap.error("a revision is required")
    if a.cmd == "index":
        cmd_index(a.survey, a.rev)
    elif a.cmd == "upload":
        cmd_upload(a.survey, a.rev, a.dry_run)
    elif a.cmd == "verify":
        sys.exit(cmd_verify(a.survey, a.rev))
    else:
        cmd_survey(a.survey, a.upload, a.local)


if __name__ == "__main__":
    main()
