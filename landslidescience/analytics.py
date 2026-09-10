"""First-party forwarder for the self-hosted Umami tracker.

WHY A FORWARDER AND NOT A DIRECT SCRIPT TAG
-------------------------------------------
Umami is self-hosted, but "self-hosted" alone does not get past ad blockers:
the common filter lists match on the *filename* (`umami.js`), on `umami.` as a
hostname label, and on the `/api/send` path under a recognisable host. A site
whose audience is technical — which this one's is — loses a large share of its
measurements that way, and the share it loses is not random.

So nothing on the page ever names Umami. The browser loads `/s/t.js` and posts
to `/s/api/send`, both on landslidescience.org, and Django forwards to the
container over the Docker network. This is also why the tracker needs no
`data-host-url`: it derives its endpoint from its own script URL
(`dirname(script.src) + '/api/send'`), so serving the script from `/s/` is
what points the beacon back at `/s/`.

Two things must survive the hop or the data is wrong rather than merely
missing:

* **The client IP.** Umami derives the visitor's session hash and country from
  it. To the container, every request comes from the Django container, so the
  real address is passed in `X-Forwarded-For` and the container is configured
  with `CLIENT_IP_HEADER=x-forwarded-for`. It is read from Caddy's own
  `X-Forwarded-For`, which is why `_client_ip` takes the *first* entry.
* **The `x-umami-*` request headers.** The v3 tracker sends the website id and
  hostname as headers, not in the body, and reads `{cache, disabled}` off the
  response to maintain its session. Drop either direction and sessions
  fragment into one-hit visits.

Cookies are deliberately NOT forwarded: Umami is cookieless, and passing the
Django session cookie to it would create a link between a logged-in editor and
their analytics session for no benefit.

FAILURE POLICY
--------------
Analytics must never damage the page. Every upstream problem — container down,
slow, malformed — returns a quiet 204 and logs nothing to the user. A missing
measurement is a rounding error; a broken map is not.
"""
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

from inventory.auth import is_site_admin

log = logging.getLogger(__name__)

# Where the Umami container answers on the Docker network. Never public.
UMAMI_URL = getattr(settings, 'UMAMI_INTERNAL_URL', 'http://umami:3000')

# Short: this sits in the request path of every page load. A slow analytics
# backend must not become a slow website.
_SCRIPT_TIMEOUT = 5
_SEND_TIMEOUT = 4

# The tracker script is a static asset that only changes when Umami is
# upgraded, so it is cached in the worker rather than re-fetched per visitor.
_script_cache = {}


@require_safe
def script(request):
    """Serve Umami's tracker as a first-party asset at /s/t.js."""
    body = _script_cache.get('body')
    if body is None:
        try:
            with urllib.request.urlopen(UMAMI_URL + '/script.js',
                                        timeout=_SCRIPT_TIMEOUT) as r:
                body = r.read()
            _script_cache['body'] = body
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            log.warning('analytics: tracker fetch failed: %s', e)
            # An empty script is a no-op in the page: window.umami stays
            # undefined and every call site's guard skips.
            resp = HttpResponse(b'', content_type='application/javascript')
            resp['Cache-Control'] = 'no-store'
            return resp

    resp = HttpResponse(body, content_type='application/javascript')
    # A day is long enough to matter for repeat visitors and short enough that
    # an Umami upgrade propagates without a cache-buster.
    resp['Cache-Control'] = 'public, max-age=86400'
    return resp


def _client_ip(request):
    """The visitor's address, as Caddy saw it.

    `X-Forwarded-For` is a chain: client, then each proxy that added itself.
    The first entry is the client. It is only trustworthy because Caddy is the
    sole ingress and rewrites the header — this value must never be used for
    anything security-relevant, only for Umami's session hash and geo lookup.
    """
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if fwd:
        return fwd.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


@csrf_exempt
@require_POST
def send(request):
    """Forward one tracker beacon to Umami, preserving IP and x-umami-* headers.

    CSRF-exempt on purpose: there is no session, no authentication and no
    state of ours to protect — the endpoint writes only into the analytics
    database, and requiring a token would mean the tracker could not run on
    cached pages.
    """
    if len(request.body) > 64 * 1024:
        # Nothing the tracker sends is remotely this big.
        return HttpResponse(status=204)

    headers = {
        'Content-Type': request.META.get('CONTENT_TYPE', 'application/json'),
        'User-Agent': request.META.get('HTTP_USER_AGENT', ''),
        'X-Forwarded-For': _client_ip(request),
    }
    # The v3 tracker carries website id / hostname / session cache as headers.
    for key, value in request.META.items():
        if key.startswith('HTTP_X_UMAMI_'):
            headers[key[5:].replace('_', '-').lower()] = value

    req = urllib.request.Request(UMAMI_URL + '/api/send', data=request.body,
                                 headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT) as r:
            payload, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        payload, status = e.read(), e.code
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        log.warning('analytics: beacon forward failed: %s', e)
        return HttpResponse(status=204)

    resp = HttpResponse(payload, status=status,
                        content_type='application/json')
    resp['Cache-Control'] = 'no-store'
    return resp


@require_safe
def health(request):
    """Is the analytics backend reachable? Used by the admin panel, not the page."""
    try:
        with urllib.request.urlopen(UMAMI_URL + '/api/heartbeat', timeout=3) as r:
            ok = r.status == 200
    except (urllib.error.URLError, OSError, TimeoutError):
        ok = False
    return HttpResponse(json.dumps({'ok': ok}), content_type='application/json')


# ---------------------------------------------------------------------------
# Which numbers a visit belongs in
#
# Two Umami website records, so the two populations never mix:
#
#   UMAMI_WEBSITE_ID       the public audience
#   UMAMI_TEAM_WEBSITE_ID  signed-in collaborators
#
# Separate *sites* rather than one site with tagged sessions, because a tag
# still lands in the default Overview and would have to be filtered out by
# hand every time — which is exactly the obscuring this is meant to prevent.
# With separate ids the public dashboard cannot contain a collaborator
# session at all.
#
# The resolution order:
#   1. Opt-out cookie wins over everything — whoever it is said no. It is the
#      only exclusion, and it is per-browser, at /traffic/optout/.
#   2. Anyone signed in → the collaborator site. Every account here is an
#      editor, a viewer, or Hig; there is no public sign-up. Hig is included
#      deliberately (asked for 2026-09-10): the operator's own use of the
#      editing tools is part of what that dashboard is for, and separating it
#      out would mean the busiest user is invisible in it.
#   3. Everyone else → the public site.
#
# Returning '' means no tracker tag is emitted at all, so an excluded visit
# makes no analytics request rather than sending one that is filtered later.
#
# Worth remembering: the collaborator group is tiny (2 accounts as of
# 2026-09), so region plus time-of-day identifies who is who. That dashboard
# is "what these named people did", not an aggregate — treat it accordingly,
# and tell collaborators it exists.
# ---------------------------------------------------------------------------
OPTOUT_COOKIE = 'ls_no_track'
_OPTOUT_MAX_AGE = 60 * 60 * 24 * 730          # two years


def site_for(request):
    """The Umami website id this request belongs to, or '' for don't track."""
    if request.COOKIES.get(OPTOUT_COOKIE) == '1':
        return ''
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return getattr(settings, 'UMAMI_TEAM_WEBSITE_ID', '')
    return getattr(settings, 'UMAMI_WEBSITE_ID', '')


def website_id(request):
    """Template context: which site to report to, and where the dashboard is."""
    return {
        'ANALYTICS_SITE_ID': site_for(request),
        'UMAMI_PUBLIC_URL': getattr(settings, 'UMAMI_PUBLIC_URL', ''),
    }


@csrf_exempt
def optout(request):
    """Show, set, or clear this browser's analytics opt-out.

    Deliberately open to everyone, not just admins: this is the site's
    do-not-measure control, and gating it behind a login would mean the only
    people who could use it are the ones already excluded.
    """
    if request.method == 'POST':
        turn_on = request.POST.get('optout') == '1'
        resp = redirect('/traffic/optout/')
        if turn_on:
            resp.set_cookie(OPTOUT_COOKIE, '1', max_age=_OPTOUT_MAX_AGE,
                            samesite='Lax', secure=not settings.DEBUG)
        else:
            resp.delete_cookie(OPTOUT_COOKIE)
        return resp

    on = request.COOKIES.get(OPTOUT_COOKIE) == '1'
    user = getattr(request, 'user', None)
    signed_in = user is not None and user.is_authenticated
    if signed_in and not on:
        state = ('You are signed in, so this browsing is counted on the '
                 '<strong>collaborators</strong> dashboard rather than the '
                 'public one.')
    elif on:
        state = 'This browser is <strong>excluded</strong> from the analytics.'
    else:
        state = 'This browser is <strong>counted</strong> in the analytics.'

    return HttpResponse(
        '<!doctype html><meta charset="utf-8"><title>Analytics opt-out</title>'
        '<meta name="robots" content="noindex, nofollow">'
        '<div style="font:14px/1.55 system-ui;margin:3rem auto;max-width:34rem;color:#333">'
        '<h1 style="font-size:18px;font-weight:600">Analytics opt-out</h1>'
        '<p>landslidescience.org counts visits with a self-hosted, cookieless '
        'analytics tool. No personal data is stored and nothing is shared with '
        'anyone else.</p>'
        '<p>' + state + '</p>'
        '<form method="post" style="margin-top:1.2rem">'
        '<input type="hidden" name="optout" value="' + ('0' if on else '1') + '">'
        '<button type="submit" style="font:inherit;padding:7px 14px;border-radius:4px;'
        'border:1px solid #5D4037;background:' + ('#fff' if on else '#5D4037') + ';'
        'color:' + ('#5D4037' if on else '#fff') + ';cursor:pointer">'
        + ('Start counting this browser' if on else "Don't count this browser") +
        '</button></form>'
        '<p style="margin-top:1.4rem;font-size:12.5px;color:#777">Stored as a single '
        'cookie on this device, kept for two years. Clearing your cookies resets it.</p>'
        '<p><a href="/" style="color:#5D4037">← landslidescience.org</a></p></div>',
        content_type='text/html')


# ---------------------------------------------------------------------------
# Dashboard access — one login, not two
#
# Umami's own login form is switched OFF (`DISABLE_LOGIN=1` makes /login return
# 403), so nobody signs in to Umami directly and there is no second password to
# hand out, rotate, or leak. The only door is this view: it checks the caller
# against *Django's* auth, mints a dashboard token server-side, and hands it to
# Umami's built-in `/sso` route, which stores it client-side and lands on the
# dashboard.
#
# The token is minted for `UMAMI_BRIDGE_USER`, a Umami account with the
# `team-view-only` role on the team that owns the website. So the token that
# reaches the browser can read the analytics and nothing else — it cannot
# create, reconfigure, or delete a site. The full-privilege `admin` account
# still exists for maintenance but is never used by a browser.
#
# Known and accepted: /sso carries the token as a query parameter, so it lands
# in that browser's history. That is Umami's own hand-off design, and the
# read-only scope is what keeps the consequence small.
# ---------------------------------------------------------------------------

def _bridge_token():
    """Mint a fresh view-only Umami token. None if that isn't possible.

    Deliberately not cached: an admin opens this a few times a day, the call is
    to a container on the same host, and a stale cached token is a confusing
    failure ("Access denied" on a page that worked yesterday) for no gain.
    """
    user = getattr(settings, 'UMAMI_BRIDGE_USER', '')
    password = getattr(settings, 'UMAMI_BRIDGE_PASSWORD', '')
    if not (user and password):
        return None
    body = json.dumps({'username': user, 'password': password}).encode()
    req = urllib.request.Request(
        UMAMI_URL + '/api/auth/login', data=body,
        headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return json.loads(r.read()).get('token')
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
        log.warning('analytics: could not mint a dashboard token: %s', e)
        return None


def _can_view_traffic(user):
    return user.is_superuser or is_site_admin(user)


@login_required
@require_safe
def dashboard(request):
    """Sign the current Django admin into the Umami dashboard and go there."""
    if not _can_view_traffic(request.user):
        return HttpResponseForbidden('Traffic analytics are restricted to site admins.')

    public = (getattr(settings, 'UMAMI_PUBLIC_URL', '') or '').rstrip('/')
    site_id = getattr(settings, 'UMAMI_WEBSITE_ID', '')
    if not public or not site_id:
        return _plain('Analytics are not configured in this environment '
                      '(UMAMI_PUBLIC_URL / UMAMI_WEBSITE_ID unset).')

    token = _bridge_token()
    if not token:
        return _plain('The analytics service is not reachable right now. '
                      'Try again in a moment.')

    # Umami's /sso validates that `url` is a same-site absolute path, so the
    # destination cannot be turned into an open redirect from here.
    target = '/websites/' + site_id
    return redirect('{}/sso?token={}&url={}'.format(
        public,
        urllib.parse.quote(token, safe=''),
        urllib.parse.quote(target, safe=''),
    ))


def _plain(message):
    return HttpResponse(
        '<!doctype html><meta charset="utf-8">'
        '<div style="font:14px system-ui;margin:3rem auto;max-width:34rem;color:#333">'
        '<h1 style="font-size:17px">Traffic analytics</h1><p>' + message + '</p>'
        '<p><a href="/inventory/manage/" style="color:#5D4037">← Back to Manage</a></p></div>',
        content_type='text/html', status=503)
