from .settings import cachalot_settings


class AtomicCache(dict):
    def __init__(self, parent_cache, db_alias):
        super().__init__()
        self.parent_cache = parent_cache
        self.db_alias = db_alias
        self.to_be_invalidated = set()

    def set(self, k, v, timeout):
        self[k] = v

    def add(self, k, v, timeout):
        """Zapis jen kdyz klic jeste neexistuje - ani v bufferu, ani v parentu.

        Potrebuje to ctecí cesta pri materializaci chybejici generace tabulky:
        tam se NESMI prepsat hodnota, kterou mezitim ulozila invalidace, jinak
        by se generace vratila na starsi stav a zaznam ulozeny pod ni by se stal
        znovu dosazitelnym, prestoze data uz jsou dal.
        """
        if k in self:
            return False
        if self.parent_cache.get_many([k]):
            return False
        self[k] = v
        return True

    def get_many(self, keys):
        data = {k: self[k] for k in keys if k in self}
        missing_keys = set(keys)
        missing_keys.difference_update(data)
        data.update(self.parent_cache.get_many(missing_keys))
        return data

    def set_many(self, data, timeout):
        self.update(data)

    def commit(self):
        # We import this here to avoid a circular import issue.
        from .utils import _invalidate_tables

        if self:
            self.parent_cache.set_many(
                self, cachalot_settings.CACHALOT_TIMEOUT)
        # The previous `set_many` is not enough.  The parent cache needs to be
        # invalidated in case another transaction occurred in the meantime.
        _invalidate_tables(self.parent_cache, self.db_alias,
                           self.to_be_invalidated)
