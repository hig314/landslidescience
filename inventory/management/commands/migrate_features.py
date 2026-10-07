"""One-shot schema migration: landslides.features + its safety net.

  1. ALTER TABLE landslides ADD COLUMN IF NOT EXISTS features text
     -- the multi-valued "Landslide features" column, ", "-joined values
     (see derived.FEATURE_MIRRORS and inventory.models.FeatureVocab).
  2. CREATE TABLE landslides_backup_20261006 AS SELECT * FROM landslides
     (only if absent): the pre-transition copy that check_features
     validates against. Drop it by hand once the mirrors are gone.
  3. Seed FeatureVocab (app SQLite) with the eight legacy values: which
     type each applies to, its description (the form's tooltip), order, and
     the boolean column it replaced. get_or_create, so re-running neither
     duplicates nor overwrites an edited row.

Then run backfill_features (--dry-run first), then check_features.

Idempotent. Safe to re-run.
"""
from django.core.management.base import BaseCommand

BACKUP_TABLE = 'landslides_backup_20261006'

SEED = [
    # value, applies_to, legacy_column, description
    ('Molards', 'catastrophic', 'molards',
     'Conical mounds of debris in the deposit, left by thawing ice-rich blocks — a thaw indicator.'),
    ('Exclusively supraglacial', 'catastrophic', 'exclusively_supraglacial',
     'The runout stayed entirely on a glacier surface.'),
    ('Super-elevated deposits', 'catastrophic', 'super_elevated_deposits',
     'Deposit runs up the outer bank of a bend, recording flow fast enough to bank around it — a velocity indicator.'),
    ('Tsunamigenic', 'catastrophic', 'tsunamigenic',
     'The failure generated a tsunami or displacement wave.'),
    ('Precursory headscarp', 'catastrophic', 'precursory_headscarp',
     'A headscarp was visible before the failure.'),
    ('Post-2012 activity increase', 'slow', 'post_2012_activity_increase',
     'Activity has increased since 2012.'),
    ('Creeping permafrost mass', 'slow', 'creeping_permafrost_mass',
     'A creeping permafrost mass. Its volume is estimated with a uniform 20 m thickness.'),
    ('Glacier contact', 'slow', 'glacier_contact',
     'The moving mass is in contact with a glacier.'),
]


class Command(BaseCommand):
    help = 'Add landslides.features, back the table up, seed the feature vocabulary.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Run the DDL in a transaction, then ROLLBACK; no vocab rows.')

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn
        from inventory.models import FeatureVocab

        conn = _get_conn()
        try:
            cur = conn.cursor()
            cur.execute("ALTER TABLE landslides ADD COLUMN IF NOT EXISTS features text")
            self.stdout.write("schema: landslides.features ensured.")
            cur.execute("SELECT to_regclass(%s)", (BACKUP_TABLE,))
            if cur.fetchone()[0] is None:
                cur.execute(f"CREATE TABLE {BACKUP_TABLE} AS SELECT * FROM landslides")
                cur.execute(f"SELECT count(*) FROM {BACKUP_TABLE}")
                self.stdout.write(f"backup: {BACKUP_TABLE} created, {cur.fetchone()[0]} rows.")
            else:
                cur.execute(f"SELECT count(*) FROM {BACKUP_TABLE}")
                self.stdout.write(f"backup: {BACKUP_TABLE} already exists ({cur.fetchone()[0]} rows), kept.")
            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING("--dry-run: rolled back; vocabulary not seeded."))
                return
            conn.commit()
        finally:
            _put_conn(conn)

        made = 0
        for i, (value, applies, col, desc) in enumerate(SEED):
            _, created = FeatureVocab.objects.get_or_create(
                value=value,
                defaults={'applies_to': applies, 'legacy_column': col,
                          'description': desc, 'sort_order': (i + 1) * 10})
            made += created
        self.stdout.write(self.style.SUCCESS(
            f"vocabulary: {made} rows added, {len(SEED) - made} already present."))
