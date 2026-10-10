#!/usr/bin/env python3
"""Correct a game camera's unset clock and stamp the corrected time on each frame.

The Braun game cam on the Portage slope (drop RZsJDoXqWe74) reset its clock
to 2022-01-01 at power-on, so every EXIF date is a fiction that is exactly
one constant offset from the truth. This script takes that offset as the
pair (raw time of one frame, real time of that frame), then for every JPEG:

  1. writes a copy with a label in the upper right
     ("Est. corrected datetime: 2026-06-15 09:03"), and
  2. in that copy, shifts EXIF DateTimeOriginal / CreateDate / ModifyDate,
     sets the UTC offset, and records the raw value in UserComment so the
     correction is reversible and its basis is stated.

The source tree is never modified. The mirror under
/Volumes/Nunatak/landslidescience/drops/<slug>/ is the backup copy of what is
on R2, and rclone re-copies any file whose size changed, so an in-place EXIF
edit there would be undone by the next pull. Corrected files go to a sibling
tree instead.

    tools/drops/gamecam_fix_clock.py \
        --src "/Volumes/Nunatak/landslidescience/drops/RZsJDoXqWe74/Timelapse Camera" \
        --out "/Volumes/Nunatak/landslidescience/drops/RZsJDoXqWe74_corrected" \
        --raw-first "2022-01-01 14:49:49" --real-first "2026-06-15 09:03:49"

How the real time was estimated (2026-10-05): the camera switches to an IR
night mode (3840x2160 frames) when it is dark and back to 8416x4736 by day,
so the length of each night is known to +-30 min. Fitted against the sun's
altitude at 60.78 N, 148.85 W, the 109 nights are best explained by a first
frame on 2026-06-15 (acceptable range 2026-06-08 .. 06-21, threshold ~-1.5
deg) and the night midpoints put solar midnight at ~07:41 camera time, i.e.
the camera ran 5 h 46 min ahead of AKDT (+-10 min). Re-run with a different
--real-first if the deployment date is known; only the label and the EXIF
change, the pixels are the same.

Needs Pillow and exiftool. Skips files already present in --out, so it can be
resumed; --force redoes them.
"""
import argparse
import datetime as dt
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = '/System/Library/Fonts/Helvetica.ttc'
FONT_INDEX = 1          # Helvetica Bold within the collection
EXIF_DTO = 36867        # DateTimeOriginal
EXTS = ('.jpg', '.jpeg')


def parse(s):
    return dt.datetime.strptime(s, '%Y-%m-%d %H:%M:%S')


def raw_taken(path):
    with Image.open(path) as im:
        v = im.getexif().get_ifd(0x8769).get(EXIF_DTO)
    if not v:
        raise ValueError(f'{path}: no DateTimeOriginal')
    return dt.datetime.strptime(v, '%Y:%m:%d %H:%M:%S')


def stamp_one(src, dst, delta, prefix, quality):
    """Write the labelled copy. EXIF bytes are carried over untouched; the
    date shift happens afterwards in one exiftool pass over the output."""
    src, dst = Path(src), Path(dst)
    with Image.open(src) as im:
        raw = dt.datetime.strptime(im.getexif().get_ifd(0x8769)[EXIF_DTO], '%Y:%m:%d %H:%M:%S')
        real = raw + delta
        label = f'{prefix}{real:%Y-%m-%d %H:%M}'
        im.load()
        exif = im.info.get('exif')
        w, h = im.size
        size = max(24, round(w / 70))
        font = ImageFont.truetype(FONT, size, index=FONT_INDEX)
        draw = ImageDraw.Draw(im)
        margin = round(size * 0.6)
        x0, y0, x1, y1 = draw.textbbox((0, 0), label, font=font)
        x = w - margin - (x1 - x0)
        # one line-height down from the top: QuickTime's title bar covers the
        # top stripe of the frame in some states
        y = margin + size
        draw.text((x, y), label, font=font, fill='white',
                  stroke_width=max(2, size // 12), stroke_fill='black')
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, 'JPEG', quality=quality, subsampling=0, exif=exif)
    return str(dst), raw, real


def shift_exif(out_dir, delta, tz, note):
    """One exiftool process over the whole output tree. `-if` makes it
    idempotent: a file whose UserComment already carries the note is skipped,
    so re-running never shifts twice."""
    days = delta.days
    secs = delta.seconds
    hh, rem = divmod(secs, 3600)
    mm, ss = divmod(rem, 60)
    sign = '+' if days >= 0 else '-'
    if days < 0:
        # exiftool shifts want a single sign; express a negative delta positively
        delta = -delta
        days, secs = delta.days, delta.seconds
        hh, rem = divmod(secs, 3600)
        mm, ss = divmod(rem, 60)
    shift = f'0:0:{days} {hh}:{mm}:{ss}'
    cmd = [
        'exiftool', '-overwrite_original', '-P', '-q', '-q', '-r', '-ext', 'jpg', '-ext', 'jpeg',
        '-if', 'not $UserComment =~ /clock was unset/',
        f'-UserComment<Camera clock was unset: raw EXIF DateTimeOriginal $DateTimeOriginal; '
        f'shifted {sign}{days} days {hh:02d}:{mm:02d}:{ss:02d} to AKDT (UTC{tz}). {note}',
        f'-AllDates{sign}={shift}',
        f'-OffsetTimeOriginal={tz}', f'-OffsetTimeDigitized={tz}', f'-OffsetTime={tz}',
        str(out_dir),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # exiftool exits 1 when some files failed the -if condition and 2 when all
    # did (an already-corrected tree); neither is an error here
    if r.returncode not in (0, 1, 2):
        sys.exit(f'exiftool failed ({r.returncode}): {r.stderr.strip()}')
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--src', required=True, help='folder of raw JPEGs (read only)')
    ap.add_argument('--out', required=True, help='folder for corrected, stamped copies')
    ap.add_argument('--raw-first', required=True, help='raw EXIF time of a reference frame, "YYYY-MM-DD HH:MM:SS"')
    ap.add_argument('--real-first', required=True, help='real local time of that same frame')
    ap.add_argument('--tz', default='-08:00', help='UTC offset of the real time (AKDT = -08:00)')
    ap.add_argument('--prefix', default='Est. corrected datetime: ')
    ap.add_argument('--note', default='Estimated from the length of each night (IR-mode switch) fitted to '
                                      'solar altitude at the site; date uncertain by about a week, time of day by ~10 min.')
    ap.add_argument('--quality', type=int, default=95)
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument('--force', action='store_true', help='re-stamp files already in --out')
    ap.add_argument('--exif-only', action='store_true', help='skip stamping; only run the EXIF pass on --out')
    a = ap.parse_args()

    delta = parse(a.real_first) - parse(a.raw_first)
    print(f'offset: raw + {delta} = real   (e.g. {a.raw_first} -> {a.real_first})')
    src, out = Path(a.src), Path(a.out)

    if not a.exif_only:
        files = sorted(p for p in src.rglob('*') if p.suffix.lower() in EXTS and not p.name.startswith('.'))
        jobs = []
        for p in files:
            dst = out / p.relative_to(src)
            if dst.exists() and not a.force:
                continue
            jobs.append((p, dst))
        print(f'{len(files)} frames, {len(jobs)} to stamp, {a.workers} workers')
        done = 0
        with ProcessPoolExecutor(a.workers) as ex:
            futs = [ex.submit(stamp_one, p, d, delta, a.prefix, a.quality) for p, d in jobs]
            for f in as_completed(futs):
                dst, raw, real = f.result()
                done += 1
                if done % 100 == 0 or done == len(jobs):
                    print(f'  {done}/{len(jobs)}  {Path(dst).name}  {raw} -> {real}', flush=True)

    print('shifting EXIF dates in', out)
    shift_exif(out, delta, a.tz, a.note)
    # verify on one file
    sample = next(p for p in sorted(out.rglob('*')) if p.suffix.lower() in EXTS)
    r = subprocess.run(['exiftool', '-s', '-DateTimeOriginal', '-OffsetTimeOriginal', '-UserComment', str(sample)],
                       capture_output=True, text=True)
    print(f'check {sample.name}:\n{r.stdout}')


if __name__ == '__main__':
    main()
