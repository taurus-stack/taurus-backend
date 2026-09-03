"""
permissionDecoratortest

test dvadmin.utils.permission_decorator module中的:
- require_perm Decorator基本functionality
- PermissionRegistry Register表行为
- require_viewset_perms classDecorator
- get_user_permission_codes 工具function
"""

import pytest
from django.test import TestCase

from dvadmin.utils.permission_decorator import (
    require_perm,
    require_viewset_perms,
    PermissionRegistry,
    PermissionEntry,
    get_user_permission_codes,
)


class PermissionDecoratorTest(TestCase):
    """permissionDecoratortest"""

    def setUp(self):
        """每 test前清EmptyRegister表"""
        PermissionRegistry.clear()

    def test_require_perm_decorator(self):
        """testpermissionDecorator基本functionality"""

        @require_perm(
            perm_code="test:resource:create",
            name="创建测试资源",
            module="test",
        )
        def test_func(self, request):
            return "success"

        self.assertTrue(hasattr(test_func, "_perm_code"))
        self.assertEqual(test_func._perm_code, "test:resource:create")
        self.assertEqual(test_func._perm_name, "创建测试资源")
        self.assertEqual(test_func._perm_module, "test")

        entries = PermissionRegistry.all_entries()
        self.assertIn("test:resource:create", entries)
        entry = entries["test:resource:create"]
        self.assertEqual(entry.code, "test:resource:create")
        self.assertEqual(entry.name, "创建测试资源")
        self.assertEqual(entry.module, "test")

    def test_multiple_permissions_on_same_function(self):
        """test同一function上heap叠Decorator不可行, 但多次调用 require_perm 可Register多 permission"""

        @require_perm(perm_code="test:resource:read", name="读取", module="test")
        def read_func(self, request):
            return "read"

        @require_perm(perm_code="test:resource:write", name="写入", module="test")
        def write_func(self, request):
            return "write"

        entries = PermissionRegistry.all_entries()
        self.assertEqual(len(entries), 2)
        self.assertIn("test:resource:read", entries)
        self.assertIn("test:resource:write", entries)

    def test_permission_registry(self):
        """testglobalpermissionRegister表"""

        @require_perm(perm_code="test:perm1", name="权限1", module="m1")
        def func1(self, request):
            pass

        @require_perm(perm_code="test:perm2", name="权限2", module="m2")
        def func2(self, request):
            pass

        registered = PermissionRegistry.all_entries()
        self.assertEqual(len(registered), 2)
        codes = list(registered.keys())
        self.assertIn("test:perm1", codes)
        self.assertIn("test:perm2", codes)
        self.assertEqual(registered["test:perm1"].name, "权限1")
        self.assertEqual(registered["test:perm2"].module, "m2")

    def test_require_perm_with_methods(self):
        """test带 HTTP method限定的permission"""

        @require_perm(
            perm_code="test:api:test",
            name="测试API",
            module="test",
            methods=["GET", "POST"],
        )
        def test_func(self, request):
            pass

        entry = PermissionRegistry.all_entries()["test:api:test"]
        self.assertEqual(entry.methods, {"GET", "POST"})

    def test_require_perm_with_roles(self):
        """test指定 roles 的permission"""

        @require_perm(
            perm_code="test:roles:test",
            name="角色测试",
            module="test",
            roles=["admin", "operator"],
        )
        def test_func(self, request):
            pass

        entry = PermissionRegistry.all_entries()["test:roles:test"]
        self.assertEqual(entry.roles, ["admin", "operator"])
        self.assertEqual(test_func._perm_roles, ["admin", "operator"])

    def test_default_name_uses_perm_code(self):
        """test默认Nameuse perm_code"""

        @require_perm(perm_code="test:default:name")
        def test_func(self, request):
            pass

        entry = PermissionRegistry.all_entries()["test:default:name"]
        self.assertEqual(entry.name, "test:default:name")

    def test_registry_merge_same_code(self):
        """test同一 perm_code 多次Register会MergeMessage"""

        @require_perm(perm_code="test:merge", name="第一个", methods=["GET"])
        def func1(self, request):
            pass

        @require_perm(perm_code="test:merge", module="merged", methods=["POST"])
        def func2(self, request):
            pass

        entries = PermissionRegistry.all_entries()
        self.assertEqual(len(entries), 1)
        entry = entries["test:merge"]
        self.assertEqual(entry.name, "第一个")
        self.assertEqual(entry.module, "merged")
        self.assertEqual(entry.methods, {"GET", "POST"})

    def test_decorator_preserves_function_behavior(self):
        """testDecoratorkeepfunction行为"""

        class FakeUser:
            is_superuser = True
            is_authenticated = True

        class FakeRequest:
            user = FakeUser()

        @require_perm(perm_code="test:behavior", name="测试行为", module="test")
        def test_func(self, request, x, y):
            return x + y

        result = test_func(None, FakeRequest(), 3, 5)
        self.assertEqual(result, 8)


class ViewSetPermsDecoratorTest(TestCase):
    """ViewSet 批量permissionDecoratortest"""

    def setUp(self):
        PermissionRegistry.clear()

    def test_require_viewset_perms_decorator(self):
        """test ViewSet LevelDecorator"""

        class FakeRequest:
            class user:
                is_superuser = True
                is_authenticated = True

        @require_viewset_perms(
            list="user:list",
            create="user:create",
            update="user:update",
            module="user",
        )
        class FakeViewSet:
            def list(self, request):
                return "list"

            def create(self, request):
                return "create"

            def update(self, request, pk=None):
                return "update"

            def custom_action(self, request):
                return "custom"

        req = FakeRequest()
        self.assertEqual(FakeViewSet().list(req), "list")
        self.assertEqual(FakeViewSet().create(req), "create")
        self.assertEqual(FakeViewSet().update(req, pk=1), "update")

        entries = PermissionRegistry.all_entries()
        self.assertEqual(len(entries), 3)
        self.assertIn("user:list", entries)
        self.assertIn("user:create", entries)
        self.assertIn("user:update", entries)
        self.assertEqual(entries["user:list"].module, "user")
        self.assertEqual(entries["user:create"].methods, {"POST"})


class PermissionEntryTest(TestCase):
    """PermissionEntry 数据classtest"""

    def test_entry_defaults(self):
        """test PermissionEntry 默认值"""
        entry = PermissionEntry(code="test:entry")
        self.assertEqual(entry.code, "test:entry")
        self.assertEqual(entry.name, "")
        self.assertEqual(entry.module, "")
        self.assertEqual(entry.methods, set())
        self.assertEqual(entry.roles, [])

    def test_entry_immutable_fields(self):
        """test PermissionEntry Field赋值"""
        entry = PermissionEntry(
            code="x:y",
            name="name",
            module="m",
            description="desc",
            methods={"GET"},
            roles=["admin"],
        )
        self.assertEqual(entry.code, "x:y")
        self.assertEqual(entry.name, "name")
        self.assertEqual(entry.module, "m")
        self.assertEqual(entry.description, "desc")
        self.assertEqual(entry.methods, {"GET"})
        self.assertEqual(entry.roles, ["admin"])


class PermissionRegistryClearTest(TestCase):
    """Register表清Emptytest"""

    def setUp(self):
        PermissionRegistry.clear()

    def test_clear_wipes_entries_and_mappings(self):
        @require_perm(perm_code="clear:1", module="m")
        def f(self, request):
            pass

        PermissionRegistry.bind_view_perm("VC", "act", "clear:1")
        self.assertEqual(len(PermissionRegistry.all_entries()), 1)
        self.assertIsNotNone(PermissionRegistry.get_view_perm("VC", "act"))

        PermissionRegistry.clear()

        self.assertEqual(len(PermissionRegistry.all_entries()), 0)
        self.assertIsNone(PermissionRegistry.get_view_perm("VC", "act"))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])