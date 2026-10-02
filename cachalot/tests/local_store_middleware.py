import datetime
from unittest import skipIf
from uuid import UUID
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.db.models.functions import Now
from django.test import TransactionTestCase

from django.db import DEFAULT_DB_ALIAS

from .models import Test, TestChild, TestParent, UnmanagedModel
from ..cache import cachalot_caches
from ..local_store import store
from ..settings import cachalot_settings
from .test_utils import TestUtilsMixin


class LocalStoreTestCase(TestUtilsMixin, TransactionTestCase):
    """
    Local store middleware test.
    """

    def setUp(self):
        super(LocalStoreTestCase, self).setUp()

        self.group = Group.objects.create(name='test_group')
        self.group__permissions = list(Permission.objects.all()[:3])
        self.group.permissions.add(*self.group__permissions)
        self.user = User.objects.create_user('user')
        self.user__permissions = list(Permission.objects.all()[3:6])
        self.user.groups.add(self.group)
        self.user.user_permissions.add(*self.user__permissions)
        self.admin = User.objects.create_superuser('admin', 'admin@test.me',
                                                   'password')
        self.t1__permission = (Permission.objects.order_by('?')
                               .select_related('content_type')[0])
        self.t1 = Test.objects.create(
            name='test1', owner=self.user,
            date='1789-07-14', datetime='1789-07-14T16:43:27',
            permission=self.t1__permission)
        self.t2 = Test.objects.create(
            name='test2', owner=self.admin, public=True,
            date='1944-06-06', datetime='1944-06-06T06:35:00')

    def test_empty(self):
        store.clear()
        self.assertFalse(store.is_uncachable())

        with self.assertNumQueries(0):
            data1 = list(Test.objects.none())

        self.assertFalse(store.get_request_tables())

        with self.assertNumQueries(0):
            data2 = list(Test.objects.none())
        self.assertListEqual(data2, data1)
        self.assertListEqual(data2, [])

        self.assertFalse(store.get_request_tables())
        self.assertFalse(store.is_uncachable())

    def test_exists(self):
        store.clear()
        self.assertFalse(store.is_uncachable())
        with self.assertNumQueries(1):
            n1 = Test.objects.exists()
        with self.assertNumQueries(0):
            n2 = Test.objects.exists()
        self.assertEqual(
            store.get_request_tables(),
            {
                "default": ["cachalot_test"],
            }
        )
        self.assertEqual(n2, n1)
        self.assertTrue(n2)
        self.assertFalse(store.is_uncachable())

    def test_test_parent(self):
        store.clear()
        self.assertFalse(store.is_uncachable())
        child = TestChild.objects.create(name='child')
        qs = TestChild.objects.filter(name='child')
        self.assert_query_cached(qs)

        self.assertEqual(
            store.get_request_tables(),
            {
                # TestChild model inherits from TestParent
                "default": ["cachalot_testchild", "cachalot_testparent"],
            }
        )
        parent = TestParent.objects.all().first()
        parent.name = 'another name'
        parent.save()
        self.assertEqual(
            store.get_request_tables(),
            {
                "default": ["cachalot_testchild", "cachalot_testparent"],
            }
        )

        child = TestChild.objects.all().first()
        self.assertEqual(child.name, 'another name')

        # very hard to test something so volatile..
        store.get_request_tables_hash()
        self.assertFalse(store.is_uncachable())

    def test_uncachable(self):
        """
        Test uncachable query flags.
        """
        store.clear()
        self.assertFalse(store.is_uncachable())

        list(User.objects.filter(last_login__lte=Now()))
        self.assertEqual(store.get_request_tables(), {})
        self.assertTrue(store.is_uncachable())

    def test_request_tables_hash_nekoliduje_pri_chybejici_generaci(self):
        """
        Chybejici generace nesmi z hashe tise vypadnout.

        Driv se takova tabulka proste preskocila, takze hash DVOU tabulek,
        z nichz jedne generace chybela, vysel stejne jako hash te druhe
        samotne. Dva ruzne stavy pod jednim klicem znamenaji, ze konzument
        (cache odpovedi v ``tlp.common.middleware``) muze na jeden hash
        dostat odpoved patrici k jinemu stavu dat. Generace se proto
        materializuje a do hashe prispeje vzdy.

        Vyhozeni z cache neni teoreticke — memcached pod tlakem klice
        zahazuje a ctecí cesta cachalotu si je sama nezaklada pri kazdem
        pouziti tohohle hashe.
        """
        store.clear()
        cache = cachalot_caches.get_cache()

        # Obe tabulky nejdriv precteme, aby jejich generace existovaly.
        list(Test.objects.all())
        list(TestParent.objects.all())

        jen_jedna = {DEFAULT_DB_ALIAS: [Test._meta.db_table]}
        obe = {
            DEFAULT_DB_ALIAS: [
                Test._meta.db_table,
                TestParent._meta.db_table,
            ],
        }

        # Generace druhe tabulky zmizi z cache.
        klic_druhe = cachalot_settings.CACHALOT_TABLE_KEYGEN(
            DEFAULT_DB_ALIAS, TestParent._meta.db_table,
        )
        cache.delete(klic_druhe)

        hash_jedne = store.get_request_tables_hash(jen_jedna)
        hash_obou = store.get_request_tables_hash(obe)

        self.assertNotEqual(
            hash_jedne, hash_obou,
            "hash dvou tabulek s chybejici generaci splynul s hashem jedne",
        )
        # Materializovana generace musi v cache zustat, jinak by se tyz
        # posun opakoval pri kazdem dalsim vypoctu.
        self.assertIsNotNone(cache.get(klic_druhe))

    def test_request_tables_hash_se_posune_po_zapisu(self):
        """
        Po zapisu do tabulky se hash zmenit MUSI — jinak by cache
        odpovedi drzela zastaraly obsah.
        """
        store.clear()
        list(Test.objects.all())
        pred = store.get_request_tables_hash()

        Test.objects.create(name="novy zaznam")

        store.clear()
        list(Test.objects.all())
        po = store.get_request_tables_hash()
        self.assertNotEqual(pred, po, "hash se po zapisu neposunul")
