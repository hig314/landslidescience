"""Flag catastrophic landslides whose event timing is weakly constrained.

Catastrophic display/age rests on a date bracket (date_min = last image WITHOUT
the event, date_max = first image WITH it) or a seismic timestamp. Records that
are only one-sided (a max OR a min, not both), or that assert a specific year
with no bracket at all, have under-supported timing worth an editor pass — the
"max only" case in particular is a recency guess from the first image and is
often correct, but should be confirmed.

Match (active catastrophic, no seismic timestamp):
  • only a max date (no earliest bound),
  • only a min date (no latest bound), or
  • a specific 4-digit year with no date bracket.

The flag_reason notes which case. Re-runnable: only sets flagged on matches and
fills flag_reason where it's blank (never clobbers a hand-written reason).
`--dry-run` reports only. `--reset` first clears this scan's own prior auto-flags
(reason prefix 'weak event timing:'), so a re-run drops records since given a
full bracket while leaving hand-set flags untouched.
"""
from django.core.management.base import BaseCommand

# Active catastrophic records with weakly-constrained timing. No %s params on
# this SELECT, so the single % in the regex passes through psycopg2 untouched.
_SQL = """
    SELECT id, unique_name, year_text,
           (date_min IS NOT NULL) AS has_min,
           (date_max IS NOT NULL) AS has_max
    FROM landslides
    WHERE landslide_type = 'catastrophic'
      AND reviewed_at IS NOT NULL AND deprecated_at IS NULL
      AND seismic_datetime IS NULL
      AND (
            (date_max IS NOT NULL AND date_min IS NULL)
         OR (date_min IS NOT NULL AND date_max IS NULL)
         OR (date_min IS NULL AND date_max IS NULL AND year_text ~ '^[0-9]{4}$')
      )
    ORDER BY id
"""


def _reason(year_text, has_min, has_max):
    if has_max and not has_min:
        return ("weak event timing: only a max date (first image showing it) — "
                "no earliest bound; confirm the year/era")
    if has_min and not has_max:
        return ("weak event timing: only a min date (last image without it) — "
                "no latest bound; confirm the year/era")
    return (f"weak event timing: specific year ({year_text}) with no date bracket "
            f"or seismic time to support it")


class Command(BaseCommand):
    help = ('Flag catastrophic records with weakly-constrained timing '
            '(one-sided date bracket, or a specific year with no bracket).')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report only; no writes.')
        parser.add_argument('--reset', action='store_true',
                            help="Clear this scan's own prior auto-flags first (re-runnable "
                                 "clean sweep; leaves manually-set flags untouched).")

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn, _invalidate

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(_SQL)
            rows = cur.fetchall()
            self.stdout.write(f"{len(rows)} catastrophic record(s) with weak timing to flag.")
            flags = []
            for lid, nm, yt, has_min, has_max in rows:
                reason = _reason(yt, has_min, has_max)
                flags.append((lid, reason))
                self.stdout.write(f"  #{lid} {nm!r} — {reason}")

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: no writes.'))
                return

            if opts['reset']:
                cur.execute(
                    "UPDATE landslides SET flagged = false, flag_reason = NULL "
                    "WHERE flagged AND flag_reason LIKE 'weak event timing:%'")
                self.stdout.write(f"--reset: cleared {cur.rowcount} prior auto-flag(s).")

            for lid, reason in flags:
                cur.execute(
                    "UPDATE landslides SET flagged = true, "
                    "flag_reason = COALESCE(flag_reason, %s) WHERE id = %s",
                    (reason, lid))
            conn.commit()
            self.stdout.write(self.style.SUCCESS(f"Flagged {len(flags)} record(s)."))
        finally:
            _put_conn(conn)

        _invalidate('features', 'home_counts', 'unclassified_count', 'flagged_count')
        # Runs in its own process; this clears only THIS process's cache, not the
        # web worker's. Restart the web process for the flagged count / map labels
        # to refresh (a prod deploy that recreates the container does this).
