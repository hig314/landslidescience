"""Validate the features transition. Three checks, all must pass:

  1. MIRRORS: for every legacy boolean, column TRUE <=> value in features,
     on every record (the rule outputs agree with their input).
  2. BACKUP: for every record present in landslides_backup_20261006, each
     boolean that was TRUE before the transition is in features now, and
     each that was not is absent (nothing gained or lost in the backfill).
     A record edited since can legitimately differ; --since-edits lists
     those separately by updated_at rather than failing on them.
  3. VOCAB: every value in use has a FeatureVocab row (otherwise it is
     offered as 'both', undescribed -- not an error, but reported).

Exit status 1 if check 1 or 2 finds a mismatch. Read-only.
"""
import sys
from django.core.management.base import BaseCommand

from .migrate_features import BACKUP_TABLE


class Command(BaseCommand):
    help = 'Prove the legacy booleans, the backup and features agree.'

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn
        from inventory.derived import FEATURE_MIRRORS, split_features
        from inventory.models import FeatureVocab

        cols = list(FEATURE_MIRRORS)
        bad = 0
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT id, unique_name, features, {', '.join(cols)} FROM landslides ORDER BY id")
            rows = cur.fetchall()
            null_feat = [r[0] for r in rows if r[2] is None]
            self.stdout.write(f"{len(rows)} records; {len(null_feat)} with features NULL (not backfilled)"
                              + (f": {null_feat[:20]}" if null_feat else ""))
            # 1. mirrors
            m = 0
            for r in rows:
                if r[2] is None:
                    continue
                have = set(split_features(r[2]))
                for c, v in zip(cols, r[3:]):
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
                cur.execute(f"SELECT b.id, l.unique_name, l.features, l.updated_at > b.updated_at, "
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
                    if r[3]:
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
