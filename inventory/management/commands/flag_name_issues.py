"""Flag landslide names that likely need attention under the naming standard.

Sets landslides.flagged = true (+ a flag_reason note) on active records whose
unique_name:
  (a) contains a weakly-associated-placename marker ('trib' / 'neighbor') — these
      should move to the `X` distinctor form; or
  (b) is an un-disambiguated token-prefix of another record's name (the shorter
      lacks the letter/number a sibling carries). A sibling that adds only a time
      qualifier (year, incl. 2024.1, or Holocene/Modern) is the intended
      slow+catastrophic same-spot pair (standard case 2) and is NOT flagged.

Re-runnable: only sets flagged on matches and fills flag_reason where it's blank
(never clobbers a hand-written reason). `--dry-run` reports only. `--reset` first
clears this scan's own prior auto-flags (matched by reason text) so a re-run is a
clean sweep that drops stale flags while leaving hand-set flags untouched. Editors
clear/adjust the flag per-record via the info-box.
"""
import re

from django.core.management.base import BaseCommand


_WEAK_RE = re.compile(r'\b(trib(?:utary)?|neighbou?r)\b', re.IGNORECASE)

_WEAK_REASON = "weakly-associated placename ('trib'/'neighbor') — consider the X distinctor"

# A trailing token that is *only* a time qualifier (a 4-digit year, or the
# epoch words) marks the deliberate slow+catastrophic same-spot pairing — e.g.
# the slow "Long Lake" + the catastrophic "Long Lake Holocene" share a base
# name by design (naming standard, case 2). A sibling that instead adds a letter
# or number is genuine missing disambiguation. The optional ".N" suffix is the
# repeat-failure marker (the Nth event in that time-bin): "2024.1", "Holocene.2".
_TIME_QUAL_RE = re.compile(r'^(\d{4}|holocene|modern)(\.\d+)?$')


def _is_time_qualifier(tok):
    return bool(_TIME_QUAL_RE.match(tok))


class Command(BaseCommand):
    help = 'Flag landslide names that likely need attention (trib/neighbor, near-duplicates).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Report only; no writes.')
        parser.add_argument('--reset', action='store_true',
                            help="Clear this scan's own prior auto-flags first (re-runnable "
                                 "clean sweep; leaves manually-set flags untouched).")

    def handle(self, *args, **opts):
        from inventory.views import _get_conn, _put_conn, _invalidate
        from inventory.io_geojson import name_key

        conn = _get_conn()
        try:
            cur = conn.cursor()
            # Active records only (reviewed, not deprecated).
            cur.execute("""
                SELECT id, unique_name FROM landslides
                WHERE reviewed_at IS NOT NULL AND deprecated_at IS NULL
                  AND unique_name IS NOT NULL AND unique_name != ''
                ORDER BY unique_name
            """)
            rows = cur.fetchall()

            reasons = {}   # id -> reason (first reason wins)
            def add(lid, reason):
                reasons.setdefault(lid, reason)

            # (a) weakly-associated placename markers
            for lid, nm in rows:
                if _WEAK_RE.search(nm or ''):
                    add(lid, _WEAK_REASON)

            # (b) an un-disambiguated base name that is a token-PREFIX of another
            # record's name → the shorter one lacks the disambiguation a sibling
            # has (e.g. "Eagle Creek" alongside "Eagle Creek A"; "Moose Creek"
            # alongside "Moose Creek B"). Two exceptions are NOT flagged:
            #   - Properly disambiguated siblings ("Turner Upper A 2014" vs
            #     "… 2015") are not prefixes of each other.
            #   - A sibling whose only extra token is a time qualifier (year /
            #     Holocene / Modern) is the deliberate slow+catastrophic
            #     same-spot pairing (standard, case 2) — e.g. "Long Lake" +
            #     "Long Lake Holocene". Skip those.
            #   - A sibling whose only extra token is a bare number is the
            #     different-feature distinctor (standard, case 5) — e.g.
            #     "Moose Creek" + "Moose Creek 2". The base is fine. Skip those.
            # Bucket by first token to keep it cheap.
            buckets = {}
            for lid, nm in rows:
                toks = name_key(nm).split()
                if toks:
                    buckets.setdefault(toks[0], []).append((lid, nm, toks))
            for grp in buckets.values():
                for a in grp:
                    for b in grp:
                        if a[0] == b[0]:
                            continue
                        # a is a strict token-prefix of b?
                        if len(a[2]) < len(b[2]) and b[2][:len(a[2])] == a[2]:
                            first_extra = b[2][len(a[2])]
                            if _is_time_qualifier(first_extra):
                                continue   # legit slow+catastrophic same-spot pair
                            if re.fullmatch(r'\d+', first_extra):
                                # a different-feature number distinctor — the
                                # intended form ("Moose Creek" + "Moose Creek 2",
                                # standard case 5). The unnumbered base is the
                                # first feature and needs no disambiguation.
                                continue
                            # Base ends in a time qualifier and the sibling tacks
                            # on a letter ("Hick's Creek E Holocene" + "… B") →
                            # this base is the first of repeat failures in a
                            # time-bin; it wants the ".N" form, not a letter.
                            if re.fullmatch(r'[a-z]', first_extra) and _is_time_qualifier(a[2][-1]):
                                add(a[0], "first of repeat failures in a time-bin — "
                                          "use the '.N' form (e.g. '… Holocene.1')")
                                break
                            add(a[0], f"base name of '{b[1]}' — needs disambiguation (e.g. add a letter)")
                            break

            # (c) a single letter trailing a time qualifier ("… Holocene A",
            # "… 2016 B") is the OLD letter-suffix form for repeat failures in
            # one time-bin; the standard now uses the ".N" form ("… Holocene.1",
            # "… 2016.2"). Flag for conversion. (A letter BEFORE the time
            # qualifier — "… A Holocene" — is a slope letter and is fine.)
            for lid, nm in rows:
                toks = name_key(nm).split()
                for i in range(1, len(toks)):
                    if re.fullmatch(r'[a-z]', toks[i]) and _is_time_qualifier(toks[i - 1]):
                        add(lid, "letter-suffixed repeat failure — use the '.N' form (e.g. '… Holocene.1')")
                        break

            self.stdout.write(f"scanned {len(rows)} active records; {len(reasons)} to flag.")
            for lid, reason in sorted(reasons.items()):
                nm = next((n for i, n in rows if i == lid), '')
                self.stdout.write(f"  #{lid} {nm!r} — {reason}")

            if opts['dry_run']:
                conn.rollback()
                self.stdout.write(self.style.WARNING('--dry-run: no writes.'))
                return

            # --reset: clear flags this scan set on a prior run (matched by their
            # reason text), so a re-run is a clean sweep that drops stale flags.
            # Manually-set flags (other/blank reasons) are left untouched.
            if opts['reset']:
                cur.execute(
                    "UPDATE landslides SET flagged = false, flag_reason = NULL "
                    "WHERE flagged AND (flag_reason = %s "
                    "  OR flag_reason LIKE 'base name of %% — needs disambiguation%%' "
                    "  OR flag_reason LIKE 'letter-suffixed repeat failure%%' "
                    "  OR flag_reason LIKE 'first of repeat failures%%')",
                    (_WEAK_REASON,))
                self.stdout.write(f"--reset: cleared {cur.rowcount} prior auto-flag(s).")

            for lid, reason in reasons.items():
                cur.execute(
                    "UPDATE landslides SET flagged = true, "
                    "flag_reason = COALESCE(flag_reason, %s) WHERE id = %s",
                    (reason, lid))
            conn.commit()
            self.stdout.write(self.style.SUCCESS(f"Flagged {len(reasons)} record(s)."))
        finally:
            _put_conn(conn)

        _invalidate('features', 'home_counts', 'unclassified_count', 'flagged_count')
        # This command runs in its own process; the line above only clears THIS
        # process's cache, not the running web worker's. Restart the web process
        # for the flagged count / map labels to refresh (a prod deploy that
        # recreates the container does this automatically).
        self.stdout.write(self.style.WARNING(
            "Restart the web process (dev: `docker compose restart web`) so the "
            "flagged count + map reflect the change — the running worker's cache "
            "is per-process and wasn't touched by this command."))
