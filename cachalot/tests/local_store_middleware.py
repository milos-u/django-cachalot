import datetime
from unittest import skipIf
from uuid import UUID
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.test import TransactionTestCase

from .models import Test, TestChild, TestParent, UnmanagedModel
from ..local_store import store
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

        with self.assertNumQueries(0):
            data1 = list(Test.objects.none())

        self.assertFalse(store.get_request_tables())

        with self.assertNumQueries(0):
            data2 = list(Test.objects.none())
        self.assertListEqual(data2, data1)
        self.assertListEqual(data2, [])

        self.assertFalse(store.get_request_tables())

    def test_exists(self):
        store.clear()
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

    def test_test_parent(self):
        store.clear()
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
