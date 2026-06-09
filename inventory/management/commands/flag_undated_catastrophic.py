"""Flag catastrophic landslides that have no age determination.

Display rests on age for catastrophic records (the map colors them by era and
renders an undated one magenta = "incomplete"). This scan sets
landslides.flagged = true (+ a flag_reason note) on active catastrophic records
whose age can't be resolved from ANY source — mirroring derived._resolve_event_era
and the year_num derivation in views.py:

  no age  ⟺  seismic_datetime IS NULL
            AND date_min IS NULL
            AND year_text is neither a 4-digit year nor a Modern/Holocene token
            AND landslide_class carries no Modern/Holocene era token

Re-runnable: only sets flagged on matches and fills flag_reason where it's blank
(never clobbers a hand-written reason). `--dry-run` reports only. `--reset` first
clears this scan's own prior auto-flags (matched by reason text) so a re-run is a
clean sweep that drops records since given an age, while leaving hand-set flags
untouched. Editors clear/adjust the flag per-record via the info-box.
"""
from django.core.management.base import BaseCommand

_REASON = ("catastrophic landslide with no age determination — set a 4-digit year, "
           "Modern, or Holocene")

# Active catastrophic records with no resolvable age (matches the year_num=NULL
# branch). No %s params on this SELECT, so the single % in the LIKE/ILIKE/regex
# patterns passes through psycopg2 untouched.
_UNDATED_SQL = """
    SELECT id, unique_name FROM landslides
    WHERE landslide_type = 'catastrophic'
      AND reviewed_at IS NOT NULL AND deprecated_at IS NULL
      AND seismic_datetime IS NULL
      AND date_min IS NULL
      AND (year_text IS NULL OR (
            year_text !~ '^[0-9]{4}$'
            AND year_text NOT ILIKE '%holocene%'
            AND year_text NOT ILIKE '%modern%'))
      AND (landslide_class IS NULL OR (
            landslide_class NOT LIKE '%Holocene%'
            AND landslide_class NOT LIKE '%Modern%'))
    ORDER BY id
"""


class Command(BaseCommand):
    help = 'Flag catastrophic landslides with no age determination (no year / Modern / Holocene).'

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
            cur.execute(_UNDATED_SQL)
            rows = cur.fetchall()
            self.stdout.write(f"{len(rows)} undated catastrophic record(s) to flag.")
            for lid, nm in rows:
                self.stdout.write(f"  #{lid} {nm!r}")

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: no writes.'))
                return

            if opts['reset']:
                cur.execute(
                    "UPDATE landslides SET flagged = false, flag_reason = NULL "
                    "WHERE flagged AND flag_reason LIKE 'catastrophic landslide with no age%'")
                self.stdout.write(f"--reset: cleared {cur.rowcount} prior auto-flag(s).")

            for lid, _nm in rows:
                cur.execute(
                    "UPDATE landslides SET flagged = true, "
                    "flag_reason = COALESCE(flag_reason, %s) WHERE id = %s",
                    (_REASON, lid))
            conn.commit()
            self.stdout.write(self.style.SUCCESS(f"Flagged {len(rows)} record(s)."))
        finally:
            _put_conn(conn)

        _invalidate('features', 'home_counts', 'unclassified_count', 'flagged_count')
        # Runs in its own process; this clears only THIS process's cache, not the
        # web worker's. Restart the web process for the flagged count / map labels
        # to refresh (a prod deploy that recreates the container does this).
