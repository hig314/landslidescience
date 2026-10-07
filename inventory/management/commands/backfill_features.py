"""Fill landslides.features from the eight legacy boolean columns.

For every record whose `features` is NULL: the values whose boolean is TRUE,
in vocabulary order, ", "-joined -- or '' when none are, so that NULL keeps
meaning "not yet backfilled" (the mirror rules leave a NULL-features record's
booleans alone; see derived._feature_mirror_rule). Records with a non-NULL
`features` are never touched, so this cannot undo an edit; --force rewrites
them too (only sensible immediately after migrate_features).

Then the mirror rules are applied for the records written, which also turns
a NULL boolean into FALSE where the value is absent (NULL and FALSE both
read as "not recorded" everywhere -- an unchecked box has never meant
"observed absent").

Idempotent. --dry-run reports and rolls back.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Fill landslides.features from the legacy feature booleans.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true',
                            help='Rewrite records whose features is already set.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn, _invalidate
        from inventory.derived import FEATURE_MIRRORS, FEATURE_MIRROR_RULES, diff_against_db

        cols = list(FEATURE_MIRRORS)
        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'landslides' AND column_name = ANY(%s)", (cols,))
            if cur.fetchone()[0] != len(cols):
                self.stdout.write("The boolean columns are gone (drop_feature_mirrors has run): "
                                  "nothing to backfill from.")
                return
            where = '' if opts['force'] else 'WHERE features IS NULL'
            cur.execute(f"SELECT id, unique_name, {', '.join(cols)} FROM landslides {where} ORDER BY id")
            rows = cur.fetchall()
            n_values, by_value = 0, {}
            for r in rows:
                vals = [FEATURE_MIRRORS[c] for c, v in zip(cols, r[2:]) if v]
                text = ', '.join(vals)
                for v in vals:
                    by_value[v] = by_value.get(v, 0) + 1
                n_values += len(vals)
                cur.execute("UPDATE landslides SET features = %s WHERE id = %s", (text, r[0]))
            self.stdout.write(f"{len(rows)} records written, {n_values} feature values in all:")
            for v, n in sorted(by_value.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f"  {n:4d}  {v}")
            # Mirror pass: booleans that disagree with features (NULL -> FALSE).
            fixes = 0
            for name in FEATURE_MIRROR_RULES:
                for ch in diff_against_db(cur, name)['changes']:
                    cur.execute(f"UPDATE landslides SET {name} = %s WHERE id = %s", (ch['new'], ch['id']))
                    fixes += 1
            self.stdout.write(f"mirror pass: {fixes} boolean cells rewritten (NULL -> FALSE where absent).")
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back."))
            else:
                conn.commit()
                _invalidate()
                self.stdout.write(self.style.SUCCESS("Committed."))
        finally:
            _put_conn(conn)
