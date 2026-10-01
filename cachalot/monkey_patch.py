import re
from collections.abc import Iterable
from functools import wraps
from time import time

from django.core.exceptions import EmptyResultSet
from django.db.backends.utils import CursorWrapper
from django.db.models.signals import post_migrate
from django.db.models.sql.compiler import (
    SQLCompiler,
    SQLInsertCompiler,
    SQLUpdateCompiler,
    SQLDeleteCompiler,
    MULTI,
)
from django.db.transaction import Atomic, get_connection

from .api import invalidate, LOCAL_STORAGE
from .cache import cachalot_caches
from .local_store import store
from .settings import cachalot_settings, ITERABLES
from .utils import (
    _get_table_cache_keys, _get_tables_from_sql, _invalidate_tables,
    UncachableQuery, is_cachable, filter_cachable,
    gen_random_key,
)


WRITE_COMPILERS = (SQLInsertCompiler, SQLUpdateCompiler, SQLDeleteCompiler)

_monkey_select_hook = None

def register_monkey_select_hook(func):
    global _monkey_select_hook
    _monkey_select_hook = func

SQL_DATA_CHANGE_RE = re.compile(
    '|'.join([
        fr'(\W|\A){re.escape(keyword)}(\W|\Z)'
        for keyword in ['update', 'insert', 'delete', 'alter', 'create', 'drop']
    ]),
    flags=re.IGNORECASE,
)

def _unset_raw_connection(original):
    def inner(compiler, *args, **kwargs):
        compiler.connection.raw = False
        try:
            return original(compiler, *args, **kwargs)
        finally:
            compiler.connection.raw = True
    return inner


def _get_result_or_execute_query(execute_query_func, cache,
                                 cache_key, table_cache_keys):
    try:
        data = cache.get_many(table_cache_keys + [cache_key])
    except KeyError:
        data = None

    if not data:
        data = {}

    # Chybejici generace tabulky se driv nahrazovala konstantou "_|_". Tim se
    # z NEZNAMEHO stavu stal konkretni znamy stav, a to porad stejny - dve ruzne
    # mezery (napr. dve vyhozeni z memcached pod tlakem) proto daly identicky
    # table_hash a zaznam ulozeny behem prvni se prijal jako platny behem druhe,
    # i kdyz se data mezi nimi zmenila.
    #
    # Misto zastupne hodnoty generaci rovnou MATERIALIZUJEME. Tim z kodu mizi
    # cely ten nejednoznacny pripad: hash je vzdy odvozeny ze skutecnych
    # nahodnych hodnot, takze stejny vyjde jen pri opravdu stejnem stavu.
    # Je to tataz vlastnost, kterou ma johnny-cache diky generaci primo v klici,
    # ale bez jeho ceny - ten potrebuje druhy round trip na KAZDE cteni, kdezto
    # tady se platí jen v te vzacne chvili, kdy generace chybi.
    #
    # Zamerne add(), ne set(): zapis ctenare nesmi prepsat hodnotu, kterou
    # mezitim ulozila invalidace. Tim by se generace vratila na starsi stav
    # a zastaraly zaznam by se stal znovu dosazitelnym. Po add() generace
    # precteme znovu, abychom pracovali s tim, co skutecne vyhralo.
    chybejici_klice = [key for key in table_cache_keys if key not in data]
    if chybejici_klice:
        rnd = gen_random_key()
        for key in chybejici_klice:
            cache.add(key, (time(), rnd), cachalot_settings.CACHALOT_TIMEOUT)
        data.update(cache.get_many(chybejici_klice))
        if any(key not in data for key in table_cache_keys):
            # generaci se nepodarilo ustalit (vypadek cache) - bez spolehlive
            # kotvy radeji necachujeme vubec, nez abychom hadali
            return execute_query_func()

    multi_key = [data[key][1] for key in sorted(table_cache_keys)]

    table_hash = gen_random_key("|".join(multi_key))

    try:
        old_table_hash, timestamp, result = data.pop(cache_key)
        if table_hash == old_table_hash:
            return result
    except (KeyError, TypeError, ValueError):
        # In case `cache_key` is not in `data` or contains bad data,
        # we simply run the query and cache again the results.
        pass

    result = execute_query_func()
    if result.__class__ not in ITERABLES and isinstance(result, Iterable):
        result = list(result)

    now = time()
    cache.set(cache_key, (table_hash, now, result), cachalot_settings.CACHALOT_TIMEOUT)

    return result


def _patch_compiler(original):
    @wraps(original)
    @_unset_raw_connection
    def inner(compiler, *args, **kwargs):
        execute_query_func = lambda: original(compiler, *args, **kwargs)
        # Checks if utils/cachalot_disabled
        if not getattr(LOCAL_STORAGE, "cachalot_enabled", True):
            return execute_query_func()

        db_alias = compiler.using
        if db_alias not in cachalot_settings.CACHALOT_DATABASES \
                or isinstance(compiler, WRITE_COMPILERS):
            return execute_query_func()

        if args:
            result_type = args[0]
        else:
            result_type = MULTI

        if _monkey_select_hook:
            _monkey_select_hook(compiler, result_type)

        try:
            cache_key = cachalot_settings.CACHALOT_QUERY_KEYGEN(compiler)
            table_cache_keys = _get_table_cache_keys(compiler)
        except UncachableQuery:
            store.mark_as_uncachable()
            return execute_query_func()
        except EmptyResultSet:
            return execute_query_func()

        return _get_result_or_execute_query(
            execute_query_func,
            cachalot_caches.get_cache(db_alias=db_alias),
            cache_key, table_cache_keys)

    return inner


def _patch_write_compiler(original):
    @wraps(original)
    @_unset_raw_connection
    def inner(write_compiler, *args, **kwargs):
        db_alias = write_compiler.using
        table = write_compiler.query.get_meta().db_table
        cachable = is_cachable(table)
        if cachable:
            # Upstream chovani: zneplatnit PRED zapisem, aby se od jeho zacatku
            # neservirovala znamo-stara data.
            invalidate(table, db_alias=db_alias,
                       cache_alias=cachalot_settings.CACHALOT_CACHE)

        result = original(write_compiler, *args, **kwargs)

        if cachable:
            # ...a JESTE JEDNOU po zapisu. Bez tohohle zustava diraa, kterou se
            # da projit: mezi invalidaci a zapisem precte ctenar uz NOVOU
            # generaci, dotazem dostane jeste STARA data a ulozi si je pod ni.
            # Takovy zaznam pak zustava platny az do DALSIHO zapisu do tabulky -
            # u tabulky, do ktere se pise jednou za dvacet minut, je to dvacet
            # minut zastaralych odpovedi.
            #
            # Presne tohle zpusobilo duplicitni integrace.Vystraha v SYPOSu:
            # SIVS task radek vlozil, soubezny pozadavek z portalu si pod novou
            # generaci ulozil jeste prazdny vysledek, a dalsi beh tasku proto
            # radek nenasel a zalozil druhy.
            #
            # Druha invalidace tenhle zaznam znepristupni. Prechodne okno mezi
            # zapisem a touhle invalidaci zustava - to je vlastnost cache-aside
            # navrhu a neodstrani ho zadne poradi - ale zastaralost uz neprezije
            # zapis. Test: test_app app/test/cachalot_stale_read.py
            #
            # Zamerne _invalidate_tables() a ne invalidate(): jen zvedne
            # generace tabulky, ale NEPOSILA post_invalidation. Signal uz odesel
            # pri prvni invalidaci a je to udalost "do tabulky se zapsalo" -
            # poslat ho podruhe by rozbilo odberatele (a chyta to i
            # cachalot.tests.signals).
            _invalidate_tables(
                cachalot_caches.get_cache(
                    cachalot_settings.CACHALOT_CACHE, db_alias=db_alias
                ),
                db_alias,
                [table],
            )

        return result

    return inner


def _patch_orm():
    if cachalot_settings.CACHALOT_ENABLED:
        SQLCompiler.execute_sql = _patch_compiler(SQLCompiler.execute_sql)
    for compiler in WRITE_COMPILERS:
        compiler.execute_sql = _patch_write_compiler(compiler.execute_sql)


def _unpatch_orm():
    if hasattr(SQLCompiler.execute_sql, '__wrapped__'):
        SQLCompiler.execute_sql = SQLCompiler.execute_sql.__wrapped__
    for compiler in WRITE_COMPILERS:
        compiler.execute_sql = compiler.execute_sql.__wrapped__


def _patch_cursor():
    def _patch_cursor_execute(original):
        @wraps(original)
        def inner(cursor, sql, *args, **kwargs):
            try:
                return original(cursor, sql, *args, **kwargs)
            finally:
                connection = cursor.db
                if getattr(connection, 'raw', True):
                    if isinstance(sql, bytes):
                        sql = sql.decode('utf-8')
                    sql = sql.lower()
                    if SQL_DATA_CHANGE_RE.search(sql):
                        tables = filter_cachable(
                            _get_tables_from_sql(connection, sql))
                        if tables:
                            invalidate(
                                *tables, db_alias=connection.alias,
                                cache_alias=cachalot_settings.CACHALOT_CACHE)

        return inner

    if cachalot_settings.CACHALOT_INVALIDATE_RAW:
        CursorWrapper.execute = _patch_cursor_execute(CursorWrapper.execute)
        CursorWrapper.executemany = _patch_cursor_execute(CursorWrapper.executemany)


def _unpatch_cursor():
    if hasattr(CursorWrapper.execute, '__wrapped__'):
        CursorWrapper.execute = CursorWrapper.execute.__wrapped__
        CursorWrapper.executemany = CursorWrapper.executemany.__wrapped__


def _patch_atomic():
    def patch_enter(original):
        @wraps(original)
        def inner(self):
            cachalot_caches.enter_atomic(self.using)
            original(self)

        return inner

    def patch_exit(original):
        @wraps(original)
        def inner(self, exc_type, exc_value, traceback):
            needs_rollback = get_connection(self.using).needs_rollback
            try:
                original(self, exc_type, exc_value, traceback)
            finally:
                cachalot_caches.exit_atomic(
                    self.using, exc_type is None and not needs_rollback)

        return inner

    Atomic.__enter__ = patch_enter(Atomic.__enter__)
    Atomic.__exit__ = patch_exit(Atomic.__exit__)


def _unpatch_atomic():
    Atomic.__enter__ = Atomic.__enter__.__wrapped__
    Atomic.__exit__ = Atomic.__exit__.__wrapped__


def _invalidate_on_migration(sender, **kwargs):
    invalidate(*sender.get_models(), db_alias=kwargs['using'],
               cache_alias=cachalot_settings.CACHALOT_CACHE)


def patch():
    post_migrate.connect(_invalidate_on_migration)

    _patch_cursor()
    _patch_atomic()
    _patch_orm()


def unpatch():
    post_migrate.disconnect(_invalidate_on_migration)

    _unpatch_cursor()
    _unpatch_atomic()
    _unpatch_orm()
