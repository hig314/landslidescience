"""One-shot: convert legacy ESRI Wayback #ext= links to the #mapCenter= form.

ESRI's Wayback app dropped support for the old bounding-box hash
    .../wayback/#ext=<lonW>,<latS>,<lonE>,<latN>&active=<release>
in favour of a center+zoom hash
    .../wayback/#mapCenter=<lon>%2C<lat>%2C<zoom>&mode=explore&active=<release>
Stored #ext= links now open to the whole world. This command rewrites each
landslides.esri_wayback_link that still uses #ext= into the new form, keeping
the same centre, an equivalent zoom, and the original `active` release.

The conversion lives in inventory.views._convert_wayback_ext_url, shared with
the auto-seeded imagery suggestions, so this command and the editor agree.

Idempotent: rows already in #mapCenter= form match nothing and are left alone.

Sequence to run on dev or prod:
    python manage.py migrate_wayback_urls --dry-run   # preview, rolls back
    python manage.py migrate_wayback_urls             # commit
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Convert legacy #ext= ESRI Wayback links to #mapCenter= form.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Apply updates in a transaction, then ROLLBACK.')

    def handle(self, *args, **opts):
        from inventory.views import (_get_conn, _put_conn,
                                     _convert_wayback_ext_url)

        dry = opts['dry_run']
        conn = _get_conn()
        try:
            cur = conn.cursor()
            # Select every Wayback link and let _convert_wayback_ext_url decide:
            # it converts a bbox (#ext= or #active=N&ext=) and leaves links that
            # already carry a mapCenter untouched. Gating on the converter rather
            # than an SQL pattern avoids missing ext params past the first slot.
            cur.execute("SELECT id, esri_wayback_link FROM landslides "
                        "WHERE esri_wayback_link LIKE "
                        "'%livingatlas.arcgis.com/wayback%' ORDER BY id")
            rows = cur.fetchall()

            changed = skipped = 0
            for id_, url in rows:
                new, did = _convert_wayback_ext_url(url)
                if not did:
                    # Already in #mapCenter= form (or nothing to convert).
                    skipped += 1
                    continue
                cur.execute("UPDATE landslides SET esri_wayback_link=%s "
                            "WHERE id=%s", (new, id_))
                changed += 1
                if changed <= 10:
                    self.stdout.write(f"  {id_}:\n    - {url}\n    + {new}")

            if dry:
                conn.rollback()
                self.stdout.write(self.style.NOTICE(
                    f"DRY-RUN: would update {changed}, skip {skipped} "
                    f"of {len(rows)} (rolled back)."))
            else:
                conn.commit()
                self.stdout.write(self.style.SUCCESS(
                    f"Updated {changed}, skipped {skipped} of {len(rows)}."))
        except Exception:
            conn.rollback()
            raise
        finally:
            _put_conn(conn)
