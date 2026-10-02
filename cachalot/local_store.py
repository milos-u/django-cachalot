from threading import local
from time import time

from .cache import cachalot_caches
from .keys import gen_random_key
from .settings import cachalot_settings

class LocalStore(local):
    """
    Per-thread local storage.
    """
    def __init__(self):
        super(LocalStore, self).__init__()
        self.clear()

    def clear(self):
        self.request_tables = {}
        self.uncachable = False

    def add_table(self, db_alias, table_name):
        if db_alias not in self.request_tables:
            self.request_tables[db_alias] = []
        if table_name not in self.request_tables[db_alias]:
            self.request_tables[db_alias].append(table_name)

    def mark_as_uncachable(self):
        """
        Marks current store state as a result
        of UncachableQuery.
        """
        self.uncachable = True

    def is_uncachable(self):
        """
        Marks current store state as a result
        of UncachableQuery.
        """
        return bool(self.uncachable)

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

        Chybejici generace se MATERIALIZUJE, stejne jako to dela ctecí
        cesta v ``monkey_patch._get_result_or_execute_query``. Driv se
        tady proste preskocila, takze hash slozeny ze dvou tabulek vysel
        stejne jako hash z jedne — a jakmile generace pozdeji vznikla,
        hash se posunul a zaznam ulozeny pod tim predchozim uz nikdo
        nenasel. Konzument tohohle hashe (cache odpovedi v
        ``tlp.common.middleware``) pak misto trefy do cache ukladal novy
        zaznam.

        ``add()``, ne ``set()``: zapis ctenare nesmi prepsat hodnotu,
        kterou mezitim ulozila invalidace. Po zapisu se generace ctou
        znovu, aby se pracovalo s tim, co skutecne vyhralo.
        """
        if request_tables is None:
            request_tables = self.request_tables
        table_keys = []
        for db_alias in sorted(request_tables.keys()):
            cache = cachalot_caches.get_cache(db_alias=db_alias)
            tables = request_tables[db_alias]
            keys = self.get_table_cache_keys(db_alias, tables)
            if not keys:
                continue
            data = cache.get_many(keys)
            chybejici_klice = [key for key in keys if key not in data]
            if chybejici_klice:
                rnd = gen_random_key()
                for key in chybejici_klice:
                    cache.add(
                        key, (time(), rnd), cachalot_settings.CACHALOT_TIMEOUT
                    )
                data.update(cache.get_many(chybejici_klice))
            for key in keys:
                if key in data:
                    table_keys.append(data[key][1])
        return "|".join(table_keys)

store = LocalStore()
