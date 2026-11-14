"""Check the agreements this project keeps by hand. Read-only.

Several things in this codebase are true only because someone remembered to
edit two places. Each has failed silently at least once (a record whose class
label map.js did not list simply vanished from the map: Tatonduk River C,
2026-10-26). This command states those agreements as code. Run it before a
deploy, and after touching the class rule, the creep ladder, the era
resolution, the map's class list, the ramps or the pinned-label list:

    python manage.py check_invariants            # code + data
    python manage.py check_invariants --no-db    # code only (no PostGIS needed)

CODE checks (exit status 1 if any fails):
  1. Every label compute_landslide_class can return is in map.js CLASS_ORDER
     and views._CLASS_COLOR, and neither lists a label the rule cannot return.
     Labels in KNOWN_HIDDEN are a decision still open, reported not failed.
  2. Every copy of the year_num SQL expression is the same text.
  3. forms.DISPLAY_REQUIRED names only fields the class / creep / era rules
     read (or landslide_type); rule inputs it does not mark are listed.
  4. map.js _PIN_LABELS and home.html's #pin-field options name the same fields.
  5. Every ramp file in views._RAMP_SPECS exists.

DATA checks (reported; exit status 1 only with --strict):
  6. Public records whose class the map does not list (they are hidden).
  7. Records where the SQL year_num and derived._resolve_event_era disagree.

Adding a check: a function returning (failures, notes), registered in handle().
"""
import itertools
import re
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from inventory import derived, forms, views

# Labels the rule can return that the map deliberately does not list yet.
# Undecided (Hig deferred it, 2026-10-26): return NULL/magenta instead, or
# give map.js a fallback. Until then a record with one of these is hidden,
# and check 6 names every such record.
KNOWN_HIDDEN = {
    'Catastrophic': 'full size, 2012 or later, creep not evaluated',
    'Slow Cryptic': 'slow, creep evaluated with no evidence',
}

INV = Path(settings.BASE_DIR) / 'inventory'
MAP_JS = INV / 'static' / 'inventory' / 'js' / 'map.js'
HOME = INV / 'templates' / 'inventory' / 'home.html'


def _rule_labels():
    """Every label the class rule returns, by running the real rules over
    every combination of their inputs (not by reading their source)."""
    creep_in = derived.compute_creep_behavior.inputs
    creeps = {None}
    for combo in itertools.product((None, True), repeat=len(creep_in)):
        row = {k: (('x' if 'other' in k else True) if v else None)
               for k, v in zip(creep_in, combo)}
        creeps.add(derived.compute_creep_behavior(row))
    labels = set()
    for t, size, cb, yt in itertools.product(
            ('slow', 'catastrophic'), (True, False), creeps,
            ('2015', '1900', '1700', 'Holocene', 'Modern', None)):
        lab = derived.compute_landslide_class({
            'landslide_type': t, 'size_inclusion': size, 'creep_behavior': cb,
            'year_text': yt, 'seismic_datetime': None, 'date_min': None})
        if lab:
            labels.add(lab)
    return labels


def _class_order():
    src = MAP_JS.read_text(encoding='utf-8')
    m = re.search(r'var CLASS_ORDER = \[(.*?)\];', src, re.S)
    if not m:
        return None
    body = re.sub(r'//[^\n]*', '', m.group(1))
    return [s for s in re.findall(r"'([^']+)'", body) if s != '__unclassified__']


def check_class_labels():
    fails, notes = [], []
    rule = _rule_labels()
    order = _class_order()
    if order is None:
        return ['could not find CLASS_ORDER in map.js (was it moved? update this check)'], []
    for name, listed in (('map.js CLASS_ORDER', set(order)),
                         ('views._CLASS_COLOR', set(views._CLASS_COLOR))):
        for lab in sorted(rule - listed):
            if lab in KNOWN_HIDDEN:
                notes.append(f'{lab!r} is not in {name} (known, undecided: {KNOWN_HIDDEN[lab]})')
            else:
                fails.append(f'the class rule can return {lab!r}, which {name} does not list: '
                             f'such a record is hidden from the map')
        for lab in sorted(listed - rule):
            fails.append(f'{name} lists {lab!r}, which the class rule never returns')
    for lab in sorted(set(KNOWN_HIDDEN) - rule):
        fails.append(f'KNOWN_HIDDEN names {lab!r}, which the rule no longer returns: remove it here')
    return fails, notes


_YEAR_RE = re.compile(
    r'CASE\s+WHEN\s+(\w+)\.seismic_datetime IS NOT NULL\s+THEN EXTRACT\(YEAR FROM \1\.seismic_datetime\)::int'
    r'\s+WHEN\s+\1\.year_text.*?\bEND\b', re.S)      # the year_text arm tells it from other CASEs


def check_year_num():
    seen = {}
    for path in sorted(INV.glob('*.py')):
        for m in _YEAR_RE.finditer(path.read_text(encoding='utf-8')):
            text = re.sub(r'\s+', ' ', m.group(0)).replace('%%', '%')
            text = re.sub(r'\b%s\.' % re.escape(m.group(1)), 'l.', text)
            line = path.read_text(encoding='utf-8')[:m.start()].count('\n') + 1
            seen.setdefault(text, []).append(f'{path.name}:{line}')
    n = sum(len(v) for v in seen.values())
    if n < 4:
        return [f'found only {n} copies of the year_num expression (expected at least 4): '
                f'this check no longer recognises them'], []
    if len(seen) > 1:
        return (['the year_num SQL copies differ, so the map, the charts and the explorer '
                 'can disagree about a record\'s age:'] +
                [f'    {", ".join(where)}: {text[:150]}…' for text, where in seen.items()]), []
    return [], [f'{n} copies of the year_num expression agree ({", ".join(next(iter(seen.values())))})']


def check_display_required():
    inputs = (set(derived.compute_landslide_class.inputs)
              | set(derived.compute_creep_behavior.inputs) | {'landslide_type'})
    # creep_behavior and size_inclusion are rule outputs / not editor-set here.
    fails = [f'forms.DISPLAY_REQUIRED marks {k!r}, which no class/creep/era rule reads'
             for k in sorted(set(forms.DISPLAY_REQUIRED) - inputs)
             if not k.startswith('insar_')]      # insar_* feed insar_creep, itself a rule
    unmarked = sorted(inputs - set(forms.DISPLAY_REQUIRED) - {'creep_behavior', 'size_inclusion', 'insar_creep'})
    notes = [f'rule inputs with no display-required mark (check each is deliberate): {", ".join(unmarked)}'] if unmarked else []
    return fails, notes


def check_pin_labels():
    js = MAP_JS.read_text(encoding='utf-8')
    m = re.search(r'var _PIN_LABELS = \{(.*?)\};', js, re.S)
    h = re.search(r'id="pin-field".*?</select>', HOME.read_text(encoding='utf-8'), re.S)
    if not m or not h:
        return ['could not find _PIN_LABELS in map.js or #pin-field in home.html'], []
    a = set(re.findall(r'(\w+)\s*:', m.group(1)))
    b = set(re.findall(r'<option value="(\w+)"', h.group(0)))
    if a != b:
        return [f'pinned-label fields differ: map.js only {sorted(a - b)}, home.html only {sorted(b - a)}'], []
    return [], []


def check_ramp_files():
    tools = Path(settings.BASE_DIR) / 'tools'
    return [f'_RAMP_SPECS[{k!r}] names tools/{s["file"]}, which does not exist'
            for k, s in views._RAMP_SPECS.items()
            if s.get('file') and not (tools / s['file']).exists()], []


def data_hidden_records(cur):
    order = _class_order() or []
    cur.execute(f"""SELECT l.id, l.unique_name, l.landslide_class FROM landslides l
                    WHERE {views.public_landslide_filter('l')}
                      AND l.landslide_class IS NOT NULL AND l.landslide_class <> ''
                      AND NOT (l.landslide_class = ANY(%s)) ORDER BY l.id""", (order,))
    return [f'{name} (id {i}): class {cls!r} is not in the map\'s class list, so the record is hidden'
            for i, name, cls in cur.fetchall()]


def data_year_num(cur):
    m = _YEAR_RE.search((INV / 'table_data.py').read_text(encoding='utf-8'))
    cur.execute(f"""SELECT l.id, l.unique_name, l.seismic_datetime, l.year_text, l.date_min,
                           {m.group(0).replace('%%', '%').replace('%', '%%')} AS year_num
                    FROM landslides l WHERE l.deprecated_at IS NULL""")
    out = []
    token = {'Holocene': -1, 'Modern': 0}
    for i, name, sd, yt, dm, sql_year in cur.fetchall():
        era = derived._resolve_event_era({'seismic_datetime': sd, 'year_text': yt, 'date_min': dm})
        if era is None:
            continue        # SQL may still resolve from the class token; nothing to compare
        want = token.get(era, era)
        if want != sql_year:
            out.append(f'{name} (id {i}): the rule resolves {era!r}, the SQL gives {sql_year!r}')
    return out


class Command(BaseCommand):
    help = 'Check the hand-kept agreements (class labels, year_num copies, ...). Read-only.'

    def add_arguments(self, parser):
        parser.add_argument('--no-db', action='store_true', help='code checks only')
        parser.add_argument('--strict', action='store_true', help='data findings also fail')

    def handle(self, *args, **opts):
        w = self.stdout.write
        bad = 0
        for title, fn in (('class labels', check_class_labels),
                          ('year_num copies', check_year_num),
                          ('display-required fields', check_display_required),
                          ('pinned-label fields', check_pin_labels),
                          ('ramp files', check_ramp_files)):
            fails, notes = fn()
            w(f'{"FAIL" if fails else "ok  "}  {title}')
            for f in fails:
                w(f'        {f}')
            for n in notes:
                w(f'        note: {n}')
            bad += len(fails)
        data = 0
        if not opts['no_db']:
            conn = views._get_conn()
            try:
                cur = conn.cursor()
                for title, fn in (('records hidden by their class', data_hidden_records),
                                  ('year_num vs the era rule', data_year_num)):
                    found = fn(cur)
                    w(f'{"DATA" if found else "ok  "}  {title}' + (f': {len(found)}' if found else ''))
                    for f in found[:40]:
                        w(f'        {f}')
                    if len(found) > 40:
                        w(f'        … and {len(found) - 40} more')
                    data += len(found)
                conn.rollback()
            finally:
                views._put_conn(conn)
        if bad or (data and opts['strict']):
            sys.exit(1)
