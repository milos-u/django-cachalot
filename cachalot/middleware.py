from django.utils.deprecation import MiddlewareMixin

from .local_store import store

class LocalStoreClearMiddleware(MiddlewareMixin):
    """
    This middleware clears the localstore cache in `local_store.store`
    at the end of every request.
    """
    def process_exception(self, *args, **kwargs):
        store.clear()
        raise

    def process_response(self, req, resp):
        store.clear()
        return resp
