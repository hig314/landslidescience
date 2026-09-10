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
import urllib.request

from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_safe

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


def website_id(request):
    """Template context: the Umami site id + dashboard URL, '' when unset."""
    return {
        'UMAMI_WEBSITE_ID': getattr(settings, 'UMAMI_WEBSITE_ID', ''),
        'UMAMI_PUBLIC_URL': getattr(settings, 'UMAMI_PUBLIC_URL', ''),
    }
