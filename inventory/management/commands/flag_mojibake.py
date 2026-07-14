"""Flag scan: text fields carrying broken character-encoding translations.

The observed case: UTF-8 bytes decoded as MacRoman — 'Göran Ekström'
becomes 'G√∂ran Ekstr√∂m' (ö = C3 B6 → '√∂'). The same accident via
Latin-1 yields 'GÃ¶ran'; smart quotes yield '‚Äô' (MacRoman) or 'â€™'
(Latin-1). All four signatures are characters that essentially never occur
legitimately in this inventory's prose, so they're high-precision tells.

Scans every text column on active landslides. Follows the flag_* scan
conventions (CLAUDE.md): re-runnable; --dry-run; --reset clears this
scan's own prior flags by reason prefix; COALESCE never clobbers a
hand-written reason; cache _invalidate at the end; normally run WITHOUT
--reset so unresolved flags aren't dropped.

The output also prints a SUGGESTED repair per hit when the reverse
round-trip (mac_roman/latin-1 encode → UTF-8 decode) succeeds and clears
every signature — the fix is applied by the editor (inline or in Manage),
not by this command.
"""
import re

from django.core.management.base import BaseCommand

REASON_PREFIX = 'possible broken encoding (mojibake)'

# High-precision signatures of UTF-8 double-decoding:
#   √ + letter-ish   MacRoman mojibake of Latin accents (√∂ √© √± …)
#   ‚Ä + anything    MacRoman mojibake of smart punctuation (‚Äô ‚Äì …)
#   Ã + high char    Latin-1 mojibake of Latin accents (Ã¶ Ã© …)
#   â€ + anything    Latin-1 mojibake of smart punctuation (â€™ â€œ …)
SUSPECT_RE = re.compile(
    '(\u221a.|\u201a\u00c4.|\u00c3[\u0080-\u00ff]|\u00e2\u20ac.)')


def suggest_repair(s):
    """Reverse the double-decode when it round-trips cleanly; else None."""
    for enc in ('mac_roman', 'latin-1'):
        try:
            fixed = s.encode(enc).decode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if not SUSPECT_RE.search(fixed):
            return fixed
    return None


class Command(BaseCommand):
    help = 'Flag records whose text fields look like broken encoding (mojibake).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report + suggest repairs, then ROLLBACK.')
        parser.add_argument('--reset', action='store_true',
                            help="First clear this scan's own prior flags (by reason prefix).")

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _invalidate, _put_conn

        conn = _get_conn()
        flagged = 0
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='landslides'
                  AND udt_name IN ('text', 'varchar')
                  AND column_name != 'flag_reason'
                ORDER BY ordinal_position""")
            text_cols = [r[0] for r in cur.fetchall()]

            if opts['reset']:
                cur.execute(
                    "UPDATE landslides SET flagged = false, flag_reason = NULL "
                    "WHERE flag_reason LIKE %s", (REASON_PREFIX + '%',))
                self.stdout.write(f'reset: cleared {cur.rowcount} prior flags from this scan.')

            cur.execute(f"""
                SELECT id, unique_name, {', '.join(text_cols)}
                FROM landslides WHERE deprecated_at IS NULL ORDER BY id""")
            rows = cur.fetchall()
            for row in rows:
                ls_id, name = row[0], row[1]
                hits = []
                for col, val in zip(text_cols, row[2:]):
                    if val and SUSPECT_RE.search(val):
                        hits.append((col, val))
                if not hits:
                    continue
                flagged += 1
                cols_txt = ', '.join(c for c, _v in hits)
                snippet = hits[0][1][:60]
                reason = f'{REASON_PREFIX} in {cols_txt}: "{snippet}"'
                cur.execute(
                    "UPDATE landslides SET flagged = true, "
                    "flag_reason = COALESCE(flag_reason, %s) WHERE id = %s",
                    (reason[:500], ls_id))
                self.stdout.write(f'#{ls_id} {name}:')
                for col, val in hits:
                    fix = suggest_repair(val)
                    self.stdout.write(f'    {col}: {val[:70]!r}')
                    self.stdout.write(f'      suggest: {fix[:70]!r}' if fix
                                      else '      suggest: (no clean round-trip — fix by hand)')

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: rolled back.'))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS('Committed.'))
        finally:
            _put_conn(conn)
        self.stdout.write(f'\n{flagged} record(s) flagged of {len(rows)} scanned '
                          f'({len(text_cols)} text columns).')
        if flagged and not opts['dry_run']:
            _invalidate('features', 'home_counts', 'flagged_count')
            self.stdout.write('Map: filter to Flagged to work through them.')
