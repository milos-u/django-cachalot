# coding: utf-8

from threading import local

from .cache import cachalot_caches
from .settings import cachalot_settings

class LocalStore(local):
    """
    Per-thread local storage.
    """
    def __init__(self):
        super(LocalStore, self).__init__()
        self.request_tables = {}

    def add_table(self, table_name, db_alias):
        if db_alias not in self.request_tables:
            self.request_tables[db_alias] = []
        if table_name not in self.request_tables[db_alias]:
            self.request_tables[db_alias].append(table_name)

    def clear(self):
        self.request_tables = {}

    def get_request_tables(self):
        return self.request_tables

    def get_table_cache_keys(self, db_alias):
        get_table_cache_key = cachalot_settings.CACHALOT_TABLE_KEYGEN
        if db_alias not in self.request_tables:
            return []
        ret = []
        for table_name in self.request_tables[db_alias]:
            ret.append(get_table_cache_key(db_alias, table_name))
        return ret

    def get_last_timestamp(self, request_tables):
        """
        Vrati timestamp posledni modifikace dat v tabulkach,
        ktere jsou soucasti aktualni transakce.
        """
        table_keys = []
        max_timestamp = None
        for db_alias in request_tables.keys():
            cache = cachalot_caches.get_cache(db_alias=db_alias)
            keys = self.get_table_cache_keys(db_alias)
            if keys:
                data = cache.get_many(table_cache_keys)
                if data:
                    max_timestamp = max_timestamp or max(data.values())
        return max_timestamp

store = LocalStore()
