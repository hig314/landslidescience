"""Field-photo endpoints: editor-uploaded photos, N:M linked to landslides.

Self-contained on purpose (like trace_views.py) — views.py is the large
landslide-data module. Schema lives in migrate_field_photos; bytes under
data/media/field_photos/<id>/ as three files:

    original.<ext>   untouched upload (the scientific record; never re-encoded)
    web.jpg          ~2000 px long edge, EXIF-rotated — lightbox size
    thumb.jpg        ~320 px — strips and grids

Serving URL shape is /inventory/photo/<id>/<name> and is LOAD-BEARING like
/inventory/planet/<slug>.mp4: snapshot bundles will reference it, so it must
not change without redirects. Derivatives are immutable for a given id, so
they're served with a far-future immutable Cache-Control.

Uploads arrive ONE FILE PER REQUEST — the manage-form JS loops with limited
concurrency and per-file retry, so a dropped connection on field internet
costs one file, not the batch. Dedupe is by sha256 of the original bytes:
re-uploading an identical file (typically to a second landslide) links the
existing photo instead of storing a copy — that's the intended path for the
rare photo shared across landslides.

HEIC (iPhone) originals are accepted via pillow-heif; their derivatives are
plain JPEGs so the public site never depends on browser HEIC support.
"""
import hashlib
import io
import json
import math
import re
import shutil
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.views.decorators.http import require_POST, require_safe

from .auth import inventory_editor_required

PHOTO_ROOT = Path(settings.MEDIA_ROOT) / 'field_photos'

MAX_UPLOAD_BYTES = 40 * 1024 * 1024
WEB_LONG_EDGE = 2000
THUMB_LONG_EDGE = 320

# Extensions we accept as originals (normalized, lowercase, no dot).
# Derivatives are always JPEG regardless of the original format.
ALLOWED_EXTS = {'jpg', 'jpeg', 'png', 'heic', 'heif', 'tif', 'tiff', 'webp'}

# Metadata columns editable through photo_edit — anything else 400s.
EDITABLE_FIELDS = ('caption', 'photographer', 'license', 'taken_at',
                   'azimuth_deg')

_ORIGINAL_NAME_RE = re.compile(r'^original\.(?P<ext>[a-z0-9]{2,5})$')


def _conn_helpers():
    # Lazy import: views.py is heavy and urls.py imports both modules.
    from .views import _get_conn, _put_conn
    return _get_conn, _put_conn


# ---------------------------------------------------------------------------
# Pillow / EXIF helpers
# ---------------------------------------------------------------------------

def _open_image(fh):
    """Open an upload with Pillow, with HEIC support registered."""
    from PIL import Image
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass   # HEIC uploads will fail cleanly in Image.open
    return Image.open(fh)


def _rational(v):
    """EXIF rationals arrive as Fraction-like IFDRational (or tuples in old
    files); collapse to float, None on junk (e.g. 0-denominator)."""
    try:
        if isinstance(v, tuple):
            return v[0] / v[1] if v[1] else None
        return float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _exif_gps(exif):
    """(lat, lon, azimuth_deg) from the GPS IFD, Nones where absent."""
    try:
        gps = exif.get_ifd(0x8825)
    except Exception:
        return None, None, None
    if not gps:
        return None, None, None

    def dms_to_deg(dms, ref):
        if not dms or len(dms) != 3:
            return None
        parts = [_rational(x) for x in dms]
        if any(p is None for p in parts):
            return None
        deg = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
        if ref in ('S', 'W'):
            deg = -deg
        return deg

    lat = dms_to_deg(gps.get(2), gps.get(1))    # GPSLatitude / Ref
    lon = dms_to_deg(gps.get(4), gps.get(3))    # GPSLongitude / Ref
    az = _rational(gps.get(17))                 # GPSImgDirection
    # Reject null-island and out-of-range junk some cameras write.
    if lat is not None and lon is not None:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
            lat = lon = None
    if az is not None and not (0 <= az < 360):
        az = None
    return lat, lon, az


def _exif_taken_at(exif):
    """EXIF DateTimeOriginal ('YYYY:MM:DD HH:MM:SS') → ISO string or None.
    EXIF timestamps carry no timezone; stored as-if-UTC, displayed date-first."""
    try:
        sub = exif.get_ifd(0x8769)
        raw = sub.get(36867) or exif.get(306)   # DateTimeOriginal, else DateTime
    except Exception:
        return None
    if not raw or not isinstance(raw, str):
        return None
    m = re.match(r'^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})', raw)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    if not (1900 <= int(y) <= 2100):   # cameras with unset clocks say 0000
        return None
    return f'{y}-{mo}-{d} {h}:{mi}:{s}'


def _make_derivatives(img, photo_dir):
    """Write web.jpg + thumb.jpg (EXIF-rotated, RGB). Returns (w, h) of the
    rotated full image — the display dimensions."""
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img)
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    w, h = img.size
    for name, edge, quality in (('web.jpg', WEB_LONG_EDGE, 82),
                                ('thumb.jpg', THUMB_LONG_EDGE, 80)):
        d = img.copy()
        d.thumbnail((edge, edge))   # no-op if already smaller
        d.save(photo_dir / name, 'JPEG', quality=quality, optimize=True,
               progressive=True)
    return w, h


def _haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Row → JSON
# ---------------------------------------------------------------------------

_PHOTO_COLS = """p.id, p.orig_filename, p.ext, p.caption, p.photographer,
                 p.license, p.taken_at, ST_Y(p.geom), ST_X(p.geom),
                 p.azimuth_deg, p.width, p.height, p.size_bytes,
                 p.uploaded_by, p.uploaded_at,
                 (SELECT COUNT(*) FROM landslide_photos c
                   WHERE c.photo_id = p.id) AS link_count"""


def _photo_json(row, sort_order=None):
    (pid, orig_filename, ext, caption, photographer, license_, taken_at,
     lat, lon, azimuth, width, height, size_bytes, uploaded_by,
     uploaded_at, link_count) = row
    return {
        'id': pid,
        'orig_filename': orig_filename,
        'thumb_url': f'/inventory/photo/{pid}/thumb.jpg',
        'web_url': f'/inventory/photo/{pid}/web.jpg',
        'orig_url': f'/inventory/photo/{pid}/original.{ext}',
        'caption': caption or '',
        'photographer': photographer or '',
        'license': license_ or '',
        'taken_at': taken_at.isoformat() if taken_at else None,
        'lat': lat, 'lon': lon,
        'azimuth_deg': azimuth,
        'width': width, 'height': height,
        'size_bytes': size_bytes,
        'uploaded_by': uploaded_by,
        'uploaded_at': uploaded_at.isoformat() if uploaded_at else None,
        'link_count': link_count,
        'sort_order': sort_order,
    }


def photos_for_landslide(cur, landslide_id):
    """All photos linked to a landslide, featured (sort_order) first, then
    uncurated chronologically. Shared by the manage form context and (later)
    api_detail."""
    cur.execute(f"""
        SELECT {_PHOTO_COLS}, lp.sort_order
        FROM landslide_photos lp
        JOIN photos p ON p.id = lp.photo_id
        WHERE lp.landslide_id = %s
        ORDER BY (lp.sort_order IS NULL), lp.sort_order,
                 COALESCE(p.taken_at, p.uploaded_at), p.id
    """, (landslide_id,))
    return [_photo_json(r[:-1], sort_order=r[-1]) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@inventory_editor_required
@require_POST
def photo_upload(request, landslide_id):
    """Ingest one photo and link it to the landslide.

    Multipart, field name 'photo'. Same-bytes re-upload (sha256 hit) links
    the existing photo — response carries dedupe flags so the UI can say so.
    Response includes distance from the landslide centroid when the photo
    has GPS (sanity check against wrong-record uploads).
    """
    f = request.FILES.get('photo')
    if not f:
        return JsonResponse({'ok': False, 'error': 'No file received.'}, status=400)
    if f.size > MAX_UPLOAD_BYTES:
        return JsonResponse({'ok': False, 'error':
                             f'{f.name} is {f.size // (1024 * 1024)} MB — the cap is '
                             f'{MAX_UPLOAD_BYTES // (1024 * 1024)} MB per photo.'},
                            status=400)

    ext = Path(f.name).suffix.lower().lstrip('.')
    if ext == 'jpeg':
        ext = 'jpg'
    if ext not in ALLOWED_EXTS:
        return JsonResponse({'ok': False, 'error':
                             f'Unsupported type ".{ext}" — accepted: '
                             f'{", ".join(sorted(ALLOWED_EXTS))}.'}, status=400)

    data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    editor = (request.user.get_full_name() or request.user.username or '').strip()

    _get_conn, _put_conn = _conn_helpers()
    conn = _get_conn()
    photo_dir = None
    created = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT centroid_lat, centroid_lon FROM landslides WHERE id = %s",
                    (landslide_id,))
        ls_row = cur.fetchone()
        if not ls_row:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Landslide not found.'},
                                status=404)
        c_lat, c_lon = ls_row

        cur.execute("SELECT id FROM photos WHERE sha256 = %s", (sha,))
        hit = cur.fetchone()
        if hit:
            photo_id = hit[0]
        else:
            created = True
            # Parse before touching disk so a corrupt file fails clean.
            # (BytesIO, not f — the sha256 pass above consumed the stream.)
            try:
                img = _open_image(io.BytesIO(data))
                img.load()
            except Exception:
                conn.rollback()
                return JsonResponse({'ok': False, 'error':
                                     f'{f.name} could not be read as an image.'},
                                    status=400)
            try:
                exif = img.getexif()
            except Exception:
                exif = {}
            lat, lon, azimuth = _exif_gps(exif) if exif else (None, None, None)
            taken_at = _exif_taken_at(exif) if exif else None

            cur.execute("""
                INSERT INTO photos (sha256, orig_filename, ext, taken_at, geom,
                                    azimuth_deg, size_bytes, uploaded_by)
                VALUES (%s, %s, %s, %s,
                        CASE WHEN %s::float8 IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%s, %s), 4326) END,
                        %s, %s, %s)
                RETURNING id
            """, (sha, f.name, ext, taken_at, lon, lon, lat, azimuth,
                  len(data), editor))
            photo_id = cur.fetchone()[0]

            # Files after the INSERT (the id names the directory), commit after
            # the files: a failed write rolls back the row, and the except
            # handler removes any partial directory.
            photo_dir = PHOTO_ROOT / str(photo_id)
            photo_dir.mkdir(parents=True, exist_ok=True)
            (photo_dir / f'original.{ext}').write_bytes(data)
            w, h = _make_derivatives(img, photo_dir)
            cur.execute("UPDATE photos SET width = %s, height = %s WHERE id = %s",
                        (w, h, photo_id))

        cur.execute("""
            INSERT INTO landslide_photos (landslide_id, photo_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (landslide_id, photo_id))
        newly_linked = cur.rowcount > 0
        conn.commit()

        cur.execute(f"SELECT {_PHOTO_COLS} FROM photos p WHERE p.id = %s",
                    (photo_id,))
        payload = _photo_json(cur.fetchone())
        conn.rollback()

        if payload['lat'] is not None and c_lat is not None:
            payload['centroid_dist_m'] = round(_haversine_m(
                payload['lat'], payload['lon'], c_lat, c_lon))
        payload.update({'dedupe': not created,
                        'already_linked': not created and not newly_linked})
        return JsonResponse({'ok': True, 'photo': payload})
    except Exception as exc:
        conn.rollback()
        if created and photo_dir is not None:
            shutil.rmtree(photo_dir, ignore_errors=True)
        return JsonResponse({'ok': False, 'error': f'Upload failed: {exc}'},
                            status=500)
    finally:
        _put_conn(conn)


# ---------------------------------------------------------------------------
# Metadata edit / link / unlink / order
# ---------------------------------------------------------------------------

def _parse_taken_at(s):
    """Editor-typed capture time: ISO, '14-Sep-2010', or blank to clear.
    Returns the value to store, or raises ValueError."""
    from datetime import datetime
    s = (s or '').strip()
    if not s:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M',
                '%Y-%m-%dT%H:%M', '%Y-%m-%d', '%d-%b-%Y', '%d %b %Y'):
        try:
            return datetime.strptime(s, fmt).isoformat(sep=' ')
        except ValueError:
            continue
    raise ValueError('Unrecognized date — use e.g. "2024-07-14", '
                     '"14-Sep-2010" or "2024-07-14 18:22".')


@inventory_editor_required
@require_POST
def photo_edit(request, photo_id):
    """Autosave one metadata field. Body: {name, value} — the photo-side
    mirror of manage_edit_field."""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid JSON body.'}, status=400)
    name = (payload.get('name') or '').strip()
    if name not in EDITABLE_FIELDS:
        return JsonResponse({'ok': False, 'error': 'Field is not editable.'},
                            status=400)
    raw = payload.get('value')
    try:
        if name == 'taken_at':
            val = _parse_taken_at(raw)
        elif name == 'azimuth_deg':
            val = float(raw) % 360 if (raw is not None and str(raw).strip()) else None
        else:
            val = (raw or '').strip() or None
    except ValueError as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=400)

    _get_conn, _put_conn = _conn_helpers()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        # name is whitelisted against EDITABLE_FIELDS (not user-supplied SQL).
        cur.execute(f"UPDATE photos SET {name} = %s WHERE id = %s", (val, photo_id))
        if cur.rowcount == 0:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Photo not found.'}, status=404)
        conn.commit()
        return JsonResponse({'ok': True, 'value': val})
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Save failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)


@inventory_editor_required
@require_POST
def photo_link(request, photo_id):
    """Link an existing photo to another landslide. Body: {landslide_id}."""
    try:
        target = int(json.loads(request.body.decode('utf-8')).get('landslide_id'))
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid landslide id.'}, status=400)
    _get_conn, _put_conn = _conn_helpers()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM photos WHERE id = %s", (photo_id,))
        if not cur.fetchone():
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Photo not found.'}, status=404)
        cur.execute("SELECT unique_name FROM landslides WHERE id = %s", (target,))
        ls = cur.fetchone()
        if not ls:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Landslide not found.'}, status=404)
        cur.execute("""
            INSERT INTO landslide_photos (landslide_id, photo_id)
            VALUES (%s, %s) ON CONFLICT DO NOTHING
        """, (target, photo_id))
        conn.commit()
        return JsonResponse({'ok': True, 'landslide_name': ls[0]})
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Link failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)


@inventory_editor_required
@require_POST
def photo_unlink(request, photo_id):
    """Detach a photo from a landslide. Body: {landslide_id}.

    Unlinking the LAST landslide deletes the photo row and its files —
    nothing orphans. The UI phrases its confirm from link_count, so the
    editor knows which of the two they're doing.
    """
    try:
        target = int(json.loads(request.body.decode('utf-8')).get('landslide_id'))
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid landslide id.'}, status=400)
    _get_conn, _put_conn = _conn_helpers()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            DELETE FROM landslide_photos
            WHERE landslide_id = %s AND photo_id = %s
        """, (target, photo_id))
        if cur.rowcount == 0:
            conn.rollback()
            return JsonResponse({'ok': False, 'error': 'Photo is not linked to '
                                 'this landslide.'}, status=404)
        cur.execute("SELECT COUNT(*) FROM landslide_photos WHERE photo_id = %s",
                    (photo_id,))
        remaining = cur.fetchone()[0]
        deleted = False
        if remaining == 0:
            cur.execute("DELETE FROM photos WHERE id = %s", (photo_id,))
            deleted = True
        conn.commit()
        if deleted:
            shutil.rmtree(PHOTO_ROOT / str(photo_id), ignore_errors=True)
        return JsonResponse({'ok': True, 'deleted': deleted,
                             'remaining_links': remaining})
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Unlink failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)


@inventory_editor_required
@require_POST
def photo_order(request, landslide_id):
    """Set the featured tier. Body: {featured: [photo_id, …]} in display
    order. Listed photos get sort_order = position; every other photo linked
    to this landslide reverts to NULL (uncurated → chronological)."""
    try:
        featured = json.loads(request.body.decode('utf-8')).get('featured')
        featured = [int(x) for x in featured]
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'Invalid featured list.'}, status=400)
    _get_conn, _put_conn = _conn_helpers()
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE landslide_photos SET sort_order = NULL "
                    "WHERE landslide_id = %s", (landslide_id,))
        for pos, pid in enumerate(featured):
            cur.execute("""
                UPDATE landslide_photos SET sort_order = %s
                WHERE landslide_id = %s AND photo_id = %s
            """, (pos, landslide_id, pid))
        conn.commit()
        return JsonResponse({'ok': True, 'featured': featured})
    except Exception as exc:
        conn.rollback()
        return JsonResponse({'ok': False, 'error': f'Reorder failed: {exc}'}, status=500)
    finally:
        _put_conn(conn)


# ---------------------------------------------------------------------------
# Serving
# ---------------------------------------------------------------------------

@require_safe
def photo_serve(request, photo_id, name):
    """Serve thumb.jpg / web.jpg / original.<ext> for a photo — public (the
    preview-password middleware already guards /inventory/* pre-launch).

    Bytes for a given photo id never change, hence immutable caching. The
    original's extension is checked against the DB so the URL can't probe
    arbitrary files.
    """
    if name not in ('thumb.jpg', 'web.jpg'):
        m = _ORIGINAL_NAME_RE.match(name)
        if not m:
            raise Http404
        _get_conn, _put_conn = _conn_helpers()
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT ext FROM photos WHERE id = %s", (photo_id,))
            row = cur.fetchone()
            conn.rollback()
        finally:
            _put_conn(conn)
        if not row or row[0] != m.group('ext'):
            raise Http404
    path = PHOTO_ROOT / str(photo_id) / name
    try:
        fh = open(path, 'rb')
    except (FileNotFoundError, NotADirectoryError):
        raise Http404
    _CTYPES = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
               'heic': 'image/heic', 'heif': 'image/heif', 'tif': 'image/tiff',
               'tiff': 'image/tiff', 'webp': 'image/webp'}
    ctype = _CTYPES.get(name.rsplit('.', 1)[-1], 'application/octet-stream')
    resp = FileResponse(fh, content_type=ctype)
    resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    resp['Content-Disposition'] = f'inline; filename="photo-{photo_id}-{name}"'
    return resp
