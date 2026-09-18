#!/bin/sh
# Does the lidar page's inline script actually RUN?
#
#   tools/web/boot_check.sh [url]        (default: dev, signed in as an admin)
#
# A syntax check answers a different question, and the difference has bitten:
# `var VIS` was declared at the bottom of the file and used during restore at
# the top. `var` hoists the name but not the value, so VIS was undefined at
# first call, the boot script died, and the page sat on "loading catalog..."
# for ever -- with a perfectly clean syntax check, because the fault was
# ordering rather than grammar.
#
# This renders the page, pulls out the inline script, and executes the whole
# top-level body against stubs for the DOM, MapLibre, pmtiles and DemShade.
# It cannot draw anything; it does not need to. Everything that runs at load
# runs here, which is where ordering faults live.
set -eu
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT=${TMPDIR:-/tmp}/boot_check_page.html
cd "$ROOT"
docker compose exec -T web python - > "$OUT" <<'PY'
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "landslidescience.settings"); django.setup()
from django.test import Client
from django.test.utils import override_settings
from django.contrib.auth import get_user_model
U = get_user_model()
with override_settings(ALLOWED_HOSTS=["testserver", "*"]):
    c = Client()
    u = U.objects.filter(is_superuser=True).first() or U.objects.first()
    if u: c.force_login(u)
    print(c.get("/lidar/").content.decode())
PY
python3 - "$OUT" <<'PY'
import re, sys, os
h = open(sys.argv[1]).read()
b = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', h, re.S)
open("/tmp/boot_check_inline.js", "w").write(max(b, key=len))
PY
osascript -l JavaScript "$ROOT/tools/web/boot_check.js"
