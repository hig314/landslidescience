# Accounts, roles and sign-in

Groups, who can do what, the two sign-in doors, sessions.

*Part of the per-feature reference split out of [CLAUDE.md](../CLAUDE.md) on 2026-11-14 (text moved verbatim). "Above"/"below" references to other sections may now point into a sibling file — the index is in CLAUDE.md.*

## Auth & permissions

Three non-superuser groups (created/maintained idempotently by `python manage.py init_groups`; the role list is `ROLE_GROUPS` in `inventory/auth.py`):

| Group | What they can do | Where they work |
|---|---|---|
| `data_users` | Read-only access to the restricted surfaces (non-public QMS layers, trace imagery, gated lidar). **Every new account joins it automatically** (`inventory/signals.py`, creation only). Renamed from `inventory_viewers` by migration `inventory/0006`. | the public site, signed in |
| `inventory_editors` | Edit landslide records via custom UI | `/inventory/manage/` |
| `site_admins` | Edit Page content (homepage, /tracyarm2025/) + manage `HostedFile`s (`/files/`) | `/admin/` |

Adding a user (do this via `/admin/auth/user/`):
1. Create user with a temp password.
2. For inventory editors: add to the `inventory_editors` group. They sign in at
   **`/inventory/login/`** and go straight to `/inventory/manage/`. `is_staff` is
   NOT needed and should not be set — it only grants Django-admin access.
3. View-only collaborators need nothing more: new accounts start in
   `data_users` (`manage.py add_data_user` does the same from the shell).
4. For site admins: add to the `site_admins` group **and** set `is_staff=True`,
   because their work is in `/admin/` and that form admits staff only.

Hig (superuser) bypasses all role checks.

**No role = a banner, not a silent downgrade.** A signed-in, non-superuser
account in none of the role groups (`has_no_role`) sees only the public site,
so both base templates include `_no_role_banner.html` telling them to contact
an admin. The Django admin's User list has a **Roles** column and a role
filter with a `(no role)` option (`inventory/admin.py`) to find such accounts.
Existing accounts were not backfilled into `data_users` when the default
arrived; only new ones get it.

**Sessions are rolling** — `SESSION_SAVE_EVERY_REQUEST = True` (settings.py) resets the 2-week `SESSION_COOKIE_AGE` clock on every request, so an actively-used editor session doesn't lapse mid-work; an idle one still expires after two weeks (that's expected, not a bug — distinct from the *fleet-wide* logout that only a `DJANGO_SECRET_KEY` change causes). When a session does expire, the manage endpoints 302-redirect to the login page; the in-app draw flow (`_drawPost` in `map.js`) detects that redirect / non-JSON response and shows a clear "log in again" message instead of choking on the login HTML with `Unexpected token '<' … is not valid JSON`. Staged draw components live server-side (`provisional_polygons`), so they survive the re-login.

(Resolved 2026-09-10: `inventory_editor_required` sends anonymous users to
`/inventory/login/`, not `/admin/login/`. The old target was unreachable for
any editor without `is_staff` — the admin form rejected the account even with
the right password, so it only ever worked for editors who happened to also be
staff.)

## Sign-in — one door for collaborators, one for site admins

There are exactly **two** ways to authenticate, and it is worth being clear
about which is which:

| Page | Who | Notes |
|---|---|---|
| `/inventory/login/` | any collaborator account (viewers, editors) | The general door. No `is_staff` needed. `LOGIN_URL` points here, and so does `inventory_editor_required`. |
| `/admin/login/` | site administrators | Django admin. **Rejects any account without `is_staff`**, which is exactly why it cannot be the general door. |

Everything else — the map, the table, the methods page, `/glaciers/`, the API
and the export — is **public**. Signing in adds editor/viewer surfaces; it is
never required to look.

**The preview password is gone (removed 2026-09-10).** It was the pre-launch
barrier over `/inventory/*` and `/glaciers/*`, already switched off in
production, and it had a failure mode worth remembering rather than
reinventing: when a real login lapsed, the middleware bounced the user to the
*preview* form rather than the sign-in form, the shared password was accepted,
and they landed in an anonymous session that looked completely normal but
silently lacked every editor control. Hig hit precisely that and reasonably
concluded the draw tool had broken. **Two password prompts where the weaker one
satisfies the gate is a trap** — if a barrier is ever wanted again, make it
refuse anonymous sessions rather than manufacture one.

A logged-in editor who unexpectedly finds themselves signed out means the
request arrived **unauthenticated** (session cookie missing/expired). A
*fleet-wide* logout is almost always a **`DJANGO_SECRET_KEY` change**
(settings.py falls back to a constant, so the only way every signed session
invalidates at once is that env var changing) — keep it stable in prod `.env`.
The header tells you which state you are in: username + "Log out" when signed
in, a "Log in" link when not.
