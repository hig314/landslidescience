"""Pre-launch preview password barrier.

While the inventory data is in review, gate /inventory/* behind a shared
password set in INVENTORY_PREVIEW_PASSWORD. After successful entry the
session is flagged and the user passes through transparently.

To remove the barrier post-launch: unset INVENTORY_PREVIEW_PASSWORD.
Logged-in users (any auth) bypass the barrier entirely.
"""
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from urllib.parse import urlencode


SESSION_KEY = 'inventory_preview_ok'


class InventoryPreviewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._should_block(request):
            preview_url = reverse('inventory:preview_login')
            qs = urlencode({'next': request.get_full_path()})
            return redirect(f'{preview_url}?{qs}')
        return self.get_response(request)

    @staticmethod
    def _should_block(request):
        # No barrier configured — public mode.
        if not settings.INVENTORY_PREVIEW_PASSWORD:
            return False
        # Only gate the inventory namespace.
        if not request.path.startswith('/inventory/'):
            return False
        # Don't gate the preview-login page itself (would loop).
        if request.path == reverse('inventory:preview_login'):
            return False
        # Authenticated users skip the barrier.
        if request.user.is_authenticated:
            return False
        # Already entered the preview password.
        if request.session.get(SESSION_KEY):
            return False
        return True
