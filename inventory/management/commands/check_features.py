"""Validate the features transition. Three checks, all must pass (check 1
only while the boolean columns still exist):

  1. MIRRORS: for every legacy boolean, column TRUE <=> value in features,
     on every record (the rule outputs agree with their input).
  2. BACKUP: for every record present in landslides_backup_20261006, each
     boolean that was TRUE before the transition is in features now, and
     each that was not is absent (nothing gained or lost in the backfill).
     A record edited since can legitimately differ: those are listed
     separately, not failed. "Edited since" is read from the audit log
     (LandslideEditMeta.last_edited_at on or after BACKUP_DATE) -- the
     table's own updated_at was never stamped by the editor before
     2026-10-07, so it cannot tell.
  3. VOCAB: every value in use has a FeatureVocab row (otherwise it is
     offered as 'both', undescribed -- not an error, but reported).

Exit status 1 if check 1 or 2 finds a mismatch. Read-only.
"""
import sys
from django.core.management.base import BaseCommand

from .migrate_features import BACKUP_TABLE

BACKUP_DATE = '2026-10-06T00:00:00+00:00'    # the day the backup table was made (its name says so)


class Command(BaseCommand):
    help = 'Prove the legacy booleans, the backup and features agree.'

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn
        from inventory.derived import FEATURE_MIRRORS, split_features
        from inventory.models import FeatureVocab, LandslideEditMeta

        edited_since = set(LandslideEditMeta.objects.filter(last_edited_at__gte=BACKUP_DATE)
                           .values_list('landslide_id', flat=True))

        cols = list(FEATURE_MIRRORS)
        bad = 0
        conn = _get_conn()
        try:
            cur = conn.cursor()
            # After drop_feature_mirrors (Phase 3) the live booleans are gone;
            # check 1 then has nothing to compare and is skipped.
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'landslides' AND column_name = ANY(%s)", (cols,))
            live = [r[0] for r in cur.fetchall()]
            cur.execute(f"SELECT id, unique_name, features{''.join(', ' + c for c in live)} FROM landslides ORDER BY id")
            rows = cur.fetchall()
            null_feat = [r[0] for r in rows if r[2] is None]
            self.stdout.write(f"{len(rows)} records; {len(null_feat)} with features NULL (not backfilled)"
                              + (f": {null_feat[:20]}" if null_feat else ""))
            # 1. mirrors
            if not live:
                self.stdout.write("SKIP 1. mirrors: the boolean columns have been dropped")
            else:
                m = 0
                for r in rows:
                    if r[2] is None:
                        continue
                    have = set(split_features(r[2]))
                    for c, v in zip(live, r[3:]):
                        if bool(v) != (FEATURE_MIRRORS[c] in have):
                            m += 1
                            self.stdout.write(f"  MIRROR #{r[0]} {r[1]}: {c}={v} but features={r[2]!r}")
                self.stdout.write(("OK " if not m else "FAIL ") + f"1. mirrors: {m} disagreements")
                bad += m
            # 2. backup
            cur.execute("SELECT to_regclass(%s)", (BACKUP_TABLE,))
            if cur.fetchone()[0] is None:
                self.stdout.write(f"SKIP 2. backup: {BACKUP_TABLE} not present")
            else:
                cur.execute(f"SELECT b.id, l.unique_name, l.features, false, "
                            f"{', '.join('b.' + c for c in cols)} "
                            f"FROM {BACKUP_TABLE} b JOIN landslides l ON l.id = b.id ORDER BY b.id")
                b = 0; edited = 0
                for r in cur.fetchall():
                    if r[2] is None:
                        continue
                    have = set(split_features(r[2]))
                    diffs = [(c, v) for c, v in zip(cols, r[4:]) if bool(v) != (FEATURE_MIRRORS[c] in have)]
                    if not diffs:
                        continue
                    if r[0] in edited_since:
                        edited += 1
                        self.stdout.write(f"  edited since backup #{r[0]} {r[1]}: {diffs} vs features={r[2]!r}")
                    else:
                        b += 1
                        self.stdout.write(f"  BACKUP #{r[0]} {r[1]}: {diffs} vs features={r[2]!r}")
                self.stdout.write(("OK " if not b else "FAIL ") +
                                  f"2. backup: {b} unexplained differences, {edited} records edited since")
                bad += b
            # 3. vocab
            cur.execute("SELECT DISTINCT btrim(t) FROM landslides, unnest(string_to_array(features, ',')) t "
                        "WHERE btrim(t) <> ''")
            used = {r[0] for r in cur.fetchall()}
        finally:
            _put_conn(conn)
        known = set(FeatureVocab.objects.values_list('value', flat=True))
        missing = sorted(used - known)
        self.stdout.write(f"{'OK' if not missing else 'NOTE'} 3. vocabulary: {len(used)} values in use, "
                          f"{len(missing)} without a vocabulary row" + (f": {missing}" if missing else ""))
        if bad:
            self.stdout.write(self.style.ERROR(f"{bad} problems."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("All checks passed."))
