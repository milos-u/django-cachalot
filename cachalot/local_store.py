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

    def add_table(self, db_alias, table_name):
        if db_alias not in self.request_tables:
            self.request_tables[db_alias] = []
        if table_name not in self.request_tables[db_alias]:
            self.request_tables[db_alias].append(table_name)

    def clear(self):
        self.request_tables = {}

    def get_request_tables(self):
        return self.request_tables

    def get_table_cache_keys(self, db_alias, tables):
        get_table_cache_key = cachalot_settings.CACHALOT_TABLE_KEYGEN
        ret = []
        for table_name in sorted(tables):
            ret.append(get_table_cache_key(db_alias, table_name))
        return ret

    def get_request_tables_hash(self, request_tables=None):
        """
        Return aggregated hash of provided tables.
        """
        if request_tables is None:
            request_tables = self.request_tables
        table_keys = []
        for db_alias in sorted(request_tables.keys()):
            cache = cachalot_caches.get_cache(db_alias=db_alias)
            tables = request_tables[db_alias]
            keys = self.get_table_cache_keys(db_alias, tables)
            if keys:
                data = cache.get_many(keys)
                if data:
                    for (timestamp, table_key) in data.values():
                        table_keys.append(table_key)
        return "|".join(table_keys)

store = LocalStore()
