"""Keep this process's response cache in step with edits made by other processes."""


class CacheStampMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from . import views   # lazy: views imports models, apps may not be ready at load
        views._sync_cache_stamp()
        return self.get_response(request)
