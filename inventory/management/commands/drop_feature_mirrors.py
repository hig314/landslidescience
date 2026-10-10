"""Phase 3 of the landslide-features transition: drop the eight boolean
columns that `features` replaced (derived.FEATURE_MIRRORS).

Refuses unless every boolean still agrees with `features` on every record
(the mirror rules have kept them in step since backfill_features; a
disagreement here means something wrote a boolean directly and that value
would be lost). Then, in one transaction:

  1. DROP VIEW landslide_overview  -- the Tethys-era view names the columns,
     so the ALTER fails on the dependency otherwise. Its definition is read
     first and re-created afterwards WITHOUT those columns, so an external
     client (QGIS) keeps its view, minus eight columns.
  2. ALTER TABLE landslides DROP COLUMN x 8.
  3. CREATE VIEW landslide_overview AS <definition minus the columns>.

landslides_backup_20261006 is NOT touched: it still holds the booleans as
they were before the transition, and check_features keeps validating the
live `features` against it. Drop the backup by hand when that is no longer
wanted.

Idempotent (a column already gone is skipped). --dry-run rolls back.
"""
import re
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Drop the eight feature booleans mirrored by landslides.features.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn, _invalidate
        from inventory.derived import FEATURE_MIRRORS, split_features

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'landslides' AND column_name = ANY(%s)",
                        (list(FEATURE_MIRRORS),))
            present = [r[0] for r in cur.fetchall()]
            if not present:
                self.stdout.write("nothing to drop: the mirror columns are already gone.")
                return
            # Guard: every present boolean must agree with features.
            cur.execute(f"SELECT id, unique_name, features, {', '.join(present)} FROM landslides ORDER BY id")
            bad = 0
            for r in cur.fetchall():
                have = set(split_features(r[2]))
                for col, v in zip(present, r[3:]):
                    if r[2] is not None and bool(v) != (FEATURE_MIRRORS[col] in have):
                        bad += 1
                        self.stdout.write(f"  DISAGREE #{r[0]} {r[1]}: {col}={v} features={r[2]!r}")
                    elif r[2] is None and v:
                        bad += 1
                        self.stdout.write(f"  NOT BACKFILLED #{r[0]} {r[1]}: {col}={v} features=NULL")
            if bad:
                self.stdout.write(self.style.ERROR(f"{bad} disagreements -- run backfill_features / check_features first. Nothing dropped."))
                conn.rollback()
                return
            self.stdout.write(f"guard: every record's {len(present)} booleans agree with features.")

            # The view.
            cur.execute("SELECT pg_get_viewdef('landslide_overview'::regclass, true)")
            viewdef = cur.fetchone()[0]
            new_def, removed = [], []
            for line in viewdef.split('\n'):
                m = re.match(r'\s*l\.(\w+),?\s*$', line)
                if m and m.group(1) in present:
                    removed.append(m.group(1))
                    continue
                new_def.append(line)
            new_def = '\n'.join(new_def)
            # The view predates three of the columns, so it names a subset.
            assert set(removed) <= set(present), (removed, present)
            cur.execute("DROP VIEW landslide_overview")
            for col in present:
                cur.execute(f"ALTER TABLE landslides DROP COLUMN {col}")
            cur.execute(f"CREATE VIEW landslide_overview AS {new_def}")
            cur.execute("SELECT count(*) FROM landslide_overview")
            self.stdout.write(f"dropped {len(present)} columns: {', '.join(present)}; "
                              f"landslide_overview re-created without {len(removed)} of them "
                              f"({cur.fetchone()[0]} rows).")
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                _invalidate()
                self.stdout.write(self.style.SUCCESS("Committed."))
        finally:
            _put_conn(conn)
