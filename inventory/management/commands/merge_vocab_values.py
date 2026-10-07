"""One-shot data normalisation for the vocabulary columns: text columns that
hold one or more values from a short controlled vocabulary, ", "-joined.

  other_subtle_creep  (merged 2026-10-06)  28 spellings -> 14 values
  stream_damming      (merged 2026-10-06)  River -> Stream; deflection -> diversion; a case fix

The edit form offers each column's values in use as a multi-select
(views._value_vocab, _vocab_picker.html), so spellings have to be one each
or the list is a list of typos. CANON maps every spelling seen on production
on 2026-10-06 to its one authoritative value. other_subtle_creep: Hig's
calls -- "change" rather than "creep" for post-AHAP / post-Maxar / post-LIA;
rockfall folds into recurrent failures; the LIA deposit cases become
"Post-LIA change"; the coherence-gap spellings become one value; one record
that read "In-situ instrumentation, thrusting in an active beach." becomes
two values, the second kept as a (singular) vocabulary entry. Values not in
CANON pass through untouched, so the command is safe against anything typed
since.

Each stored string is split on commas, every part mapped, duplicates
dropped, and the parts re-joined as ", " -- the shape the form writes.

Idempotent. --dry-run prints every change and rolls back. --column limits
it to one column. Run once per environment (dev and production are separate
databases); re-running is harmless.
"""
from django.core.management.base import BaseCommand


# column -> {lower-cased spelling: authoritative value}
CANON = {}
CANON['other_subtle_creep'] = {
    'fine ground cracking':         'Fine ground cracking',
    'fine ground-cracking':         'Fine ground cracking',
    'post-ahap creep':              'Post-AHAP change',
    'post-ahap change':             'Post-AHAP change',
    'recurrent failures':           'Recurrent failures',
    'recurrent rockfall':           'Recurrent failures',
    'lidar change':                 'Lidar change',
    'lidar differencing':           'Lidar change',
    'post-maxar creep':             'Post-Maxar change',
    'post-maxar change':            'Post-Maxar change',
    'offset lia deposits':          'Post-LIA change',
    'post-lia creep':               'Post-LIA change',
    'post-lia change':              'Post-LIA change',
    'opera coherence gap':          'OPERA coherence gap',
    'decorrelated in opera':        'OPERA coherence gap',
    'insar opera decorrelation':    'OPERA coherence gap',
    'insar decorrelation':          'OPERA coherence gap',
    'opera decorrelation':          'OPERA coherence gap',
    'radar coherence hole':         'OPERA coherence gap',
    'in-situ instrumentation':      'In-situ instrumentation',
    'in-situ instrumenation':       'In-situ instrumentation',
    'instrumental record':          'In-situ instrumentation',
    'thrusting in an active beach': 'Thrusting in an active beach',
    'thrusting in an active beach.': 'Thrusting in an active beach',
    'active sinkholes':             'Active sinkholes',
    'deflected river':              'Stream diversion',   # the damming vocabulary's general term (Hig, 2026-10-06)
    'deflected stream':             'Stream diversion',
    'stream diversion':             'Stream diversion',
    'muddy discharge':              'Muddy discharge',
    'normal scarp crossing talus':  'Normal scarp crossing talus',
    'tilted trees':                 'Tilted trees',
}
CANON['stream_damming'] = {
    'overtopped dam':          'Overtopped dam',
    'breached dam':            'Breached dam',
    'permeable dam':           'Permeable dam',
    'incised dam':             'Incised dam',
    'river diversion':         'Stream diversion',   # "stream", not "river", throughout (Hig, 2026-10-06)
    'stream diversion':        'Stream diversion',
    'river deflection':        'Stream diversion',   # deflection folds into diversion, the general term
    'stream deflection':       'Stream diversion',
    'pinched stream':          'Pinched stream',
    'partial dam':             'Partial dam',
    'divide-forming dam':      'Divide-forming dam',
    'episodic minor damming':  'Episodic minor damming',
    'filled dam':              'Filled dam',
}


def normalise(raw, canon):
    """Split, map, dedupe, re-join. Returns '' for an empty/whitespace input."""
    out = []
    for part in (raw or '').split(','):
        p = part.strip()
        if not p:
            continue
        v = canon.get(p.lower(), p)
        if v not in out:
            out.append(v)
    return ', '.join(out)


class Command(BaseCommand):
    help = 'Normalise the vocabulary columns (other_subtle_creep, stream_damming) to one spelling per value.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report every change, then ROLLBACK.')
        parser.add_argument('--column', choices=sorted(CANON), help='Only this column.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn, _invalidate

        conn = _get_conn()
        try:
            cur = conn.cursor()
            for col in ([opts['column']] if opts['column'] else sorted(CANON)):
                self.stdout.write(f"== {col}")
                cur.execute(f"SELECT id, unique_name, {col} FROM landslides "
                            f"WHERE {col} IS NOT NULL AND btrim({col}) <> '' ORDER BY id")
                rows = cur.fetchall()
                changed = 0
                for ls_id, name, raw in rows:
                    new = normalise(raw, CANON[col])
                    if new == raw:
                        continue
                    changed += 1
                    self.stdout.write(f"  #{ls_id} {name}: {raw!r} -> {new!r}")
                    cur.execute(f"UPDATE landslides SET {col} = %s WHERE id = %s",
                                (new or None, ls_id))
                self.stdout.write(f"{len(rows)} records carry a value; {changed} rewritten.")
                cur.execute(f"SELECT btrim(t), count(*) FROM landslides, "
                            f"unnest(string_to_array({col}, ',')) AS t "
                            f"WHERE deprecated_at IS NULL AND btrim(t) <> '' "
                            f"GROUP BY 1 ORDER BY 2 DESC, 1")
                self.stdout.write("vocabulary after:")
                for v, n in cur.fetchall():
                    self.stdout.write(f"  {n:4d}  {v}")
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                _invalidate()
                self.stdout.write(self.style.SUCCESS("Committed."))
        finally:
            _put_conn(conn)
