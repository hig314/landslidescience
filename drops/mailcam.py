"""Game-camera photos that arrive by email, filed into a drop.

Cellular game cameras (Spartan GL-VLTEb) email every photo through HCO
Wireless's cloud to the Google Group cam@groundtruthalaska.org. The group's
one member is the Workspace mailbox camfeed@groundtruthalaska.org, which this
reads over IMAP with an app password (`CAMFEED_IMAP_*` in the environment;
on production they live in /opt/monitoring/.env and are passed in by
tools/drops/mailcam_cron.sh, never into this repo's .env). Setup of the
mailbox side is recorded in the groundtruthalaska repo's CLAUDE.md.

Each accepted message's JPEG goes to R2 under the drop as
`<camera id>/<YYYY-MM-DD>/<attachment name>` and gets a DropFile row, so the
usual gallery, thumbnails, `drops_reconcile` and `tools/drops/r2_pull.sh`
all apply unchanged. The mailbox is opened read-only: nothing is deleted or
marked, and the group keeps its own archive besides.

Accepted = Gmail's own top Authentication-Results says DMARC passed for
hcowireless.com (the group accepts mail from anyone, and a From line is
free to forge), and the subject is `<YYYY-MM-DD HH:MM:SS> - <camera id>`.
That subject time is the camera's clock, stored in taken_at as-if-UTC like
every other drop photo; the thumbnail pass replaces it only with an EXIF
date, if the attachment has one.

Progress is a UID watermark in data/drops_mailcam.json, per mailbox and
UIDVALIDITY. It only advances past messages that were filed or permanently
refused, so a failed upload is retried next run; the (drop, path) unique
key makes a retry harmless. `--all` ignores the watermark.
"""
import email
import email.utils
import imaplib
import json
import logging
import os
import re
from datetime import datetime, timezone as tz
from email.policy import default as default_policy

from django.conf import settings
from django.utils import timezone

from . import r2
from .models import DropFile, clean_rel_path

log = logging.getLogger(__name__)

SENDER_DOMAIN = 'hcowireless.com'
CAMERA_RE = r'GL-VLTEb-\d+'
SUBJECT_RE = re.compile(rf'^\s*(\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d) - ({CAMERA_RE})\s*$')
STATE_PATH = settings.BASE_DIR / 'data' / 'drops_mailcam.json'

# A photo counts as "good" for the public latest-photo view when it is
# neither night (mean grey < 40) nor almost wholly obscured (spread < 15).
# Set on the first 28 Portage frames, 2026-10-29: night frames sit at
# mean ~5, the dawn frame at 20, and hazy or overcast daylight frames
# (mean 108-134, spread 43-56) stay in on purpose.
MIN_BRIGHTNESS = 40
MIN_CONTRAST = 15


def latest_good(camera):
    """Newest emailed frame from `camera` that passes the thresholds."""
    return (DropFile.objects
            .filter(path__startswith=f'{camera}/', thumb_state='done',
                    brightness__gte=MIN_BRIGHTNESS, contrast__gte=MIN_CONTRAST)
            .order_by('-taken_at', '-pk').first())
FETCH_BATCH = 20


class ConfigError(RuntimeError):
    pass


def imap_config():
    cfg = {k: os.environ.get(f'CAMFEED_IMAP_{k.upper()}', '').strip()
           for k in ('host', 'port', 'user', 'password')}
    missing = [k for k, v in cfg.items() if not v]
    if missing:
        raise ConfigError('CAMFEED_IMAP_* not set: ' + ', '.join(missing))
    cfg['port'] = int(cfg['port'])
    cfg['password'] = cfg['password'].strip('"\'').replace(' ', '')   # Google shows it in fours
    return cfg


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(state):
    tmp = STATE_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True))
    tmp.replace(STATE_PATH)


def authenticated(msg):
    """True when Gmail, at final delivery, recorded a DMARC pass for the
    sender domain. The topmost Authentication-Results is Gmail's own; the
    group's hop shows up inside it as the ARC result."""
    ar = msg.get('Authentication-Results', '')
    return (ar.lstrip().startswith('mx.google.com;')
            and f'dmarc=pass fromdomain={SENDER_DOMAIN}' in ar)


def parse(msg):
    """(camera, shot_at naive datetime, [(filename, bytes)]) or raises
    ValueError with the reason a message is refused."""
    if not authenticated(msg):
        raise ValueError('no DMARC pass for ' + SENDER_DOMAIN)
    m = SUBJECT_RE.match(str(msg.get('Subject', '')))
    if not m:
        raise ValueError(f'subject not a camera subject: {msg.get("Subject")!r}')
    shot_at = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S')
    images = []
    for part in msg.iter_attachments():
        if part.get_content_type() != 'image/jpeg':
            continue
        data = part.get_payload(decode=True)
        if data:
            name = part.get_filename() or f'{shot_at:%Y%m%d%H%M%S}.jpg'
            images.append((name.rsplit('/', 1)[-1], data))
    if not images:
        raise ValueError('no JPEG attached')
    return m.group(2), shot_at, images


def file_message(drop, msg, dry_run=False):
    """Upload one message's photos; returns the number of new files."""
    camera, shot_at, images = parse(msg)
    received = email.utils.parsedate_to_datetime(msg['Date']) if msg['Date'] else timezone.now()
    added = 0
    for name, data in images:
        path = clean_rel_path(f'{camera}/{shot_at:%Y-%m-%d}/{name}')
        if drop.files.filter(path=path).exists():
            continue
        added += 1
        if dry_run:
            continue
        key = drop.key_for(path)
        r2.put_bytes(key, data, 'image/jpeg')
        obj = r2.head(key)
        DropFile.objects.create(
            drop=drop, path=path, key=key, size=obj['size'], etag=obj['etag'],
            content_type='image/jpeg', uploaded_at=received,
            taken_at=shot_at.replace(tzinfo=tz.utc), thumb_state='pending')
    return added


def run(drop, rescan=False, dry_run=False, out=lambda s: None):
    """Returns {'seen', 'added', 'refused'}."""
    cfg = imap_config()
    state = _load_state()
    stats = {'seen': 0, 'added': 0, 'refused': 0}
    m = imaplib.IMAP4_SSL(cfg['host'], cfg['port'], timeout=60)
    try:
        m.login(cfg['user'], cfg['password'])
        m.select('INBOX', readonly=True)
        validity = m.response('UIDVALIDITY')[1][0].decode()
        skey = f'{cfg["user"]}:{validity}:{drop.slug}'
        last = 0 if rescan else int(state.get(skey, 0))
        typ, data = m.uid('search', None, f'UID {last + 1}:*', 'FROM', SENDER_DOMAIN)
        # "n:*" always matches the newest message, even when n is past it
        uids = sorted(u for u in map(int, data[0].split()) if u > last)
        try:
            for i in range(0, len(uids), FETCH_BATCH):
                batch = uids[i:i + FETCH_BATCH]
                typ, resp = m.uid('fetch', ','.join(map(str, batch)), '(UID BODY.PEEK[])')
                for item in resp:
                    if not isinstance(item, tuple):
                        continue
                    uid = int(re.search(rb'UID (\d+)', item[0]).group(1))
                    msg = email.message_from_bytes(item[1], policy=default_policy)
                    stats['seen'] += 1
                    try:
                        n = file_message(drop, msg, dry_run=dry_run)
                    except ValueError as e:
                        stats['refused'] += 1
                        log.warning('mailcam: refused UID %s: %s', uid, e)
                        out(f'refused UID {uid}: {e}')
                    else:
                        stats['added'] += n
                    last = max(last, uid)
        finally:
            if not dry_run and not rescan:
                state[skey] = last
                _save_state(state)
    finally:
        try:
            m.logout()
        except Exception:                       # noqa: BLE001 — closing only
            pass
    return stats
