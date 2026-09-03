"""
Permission code管理System testScript

test内容:
1. Modelcreate和Query
2. Decoratorfunctionality
3. API interface
4. permission分配逻辑
"""
import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from dvadmin.system.models import Role, PermissionCode, RolePermission
from dvadmin.utils.permission_decorator import require_perm, PermissionRegistry


User = get_user_model()


class PermissionCodeModelTest(TestCase):
    """Permission codeModeltest"""
    
    def setUp(self):
        """test数据准备"""
        self.role = Role.objects.create(
            name='测试角色',
            key='test_role',
            status=True
        )
        
    def test_create_permission_code(self):
        """testcreatePermission code"""
        perm = PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user',
            description='创建新用户',
            status=True
        )
        
        self.assertEqual(perm.code, 'user:create')
        self.assertEqual(perm.name, '创建用户')
        self.assertEqual(perm.module, 'user')
        self.assertTrue(perm.status)
        
    def test_permission_code_unique_constraint(self):
        """testPermission codeUnique性约束"""
        PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user'
        )
        
        with self.assertRaises(Exception):
            PermissionCode.objects.create(
                code='user:create',
                name='创建用户2',
                module='user'
            )
            
    def test_role_permission_association(self):
        """testRolepermissionassociate"""
        perm = PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user'
        )
        
        # createRolepermissionassociate
        role_perm = RolePermission.objects.create(
            role=self.role,
            permission=perm
        )
        
        # validateassociate
        self.assertEqual(role_perm.role, self.role)
        self.assertEqual(role_perm.permission, perm)
        
        # validate反向Query
        self.assertIn(perm, [rp.permission for rp in self.role.role_permissions.all()])
        self.assertIn(self.role, [pr.role for pr in perm.permission_roles.all()])


class PermissionDecoratorTest(TestCase):
    """permissionDecoratortest"""
    
    def setUp(self):
        """cleanupRegister表"""
        PermissionRegistry.clear()
        
    def test_require_perm_decorator(self):
        """test @require_perm Decorator"""
        @require_perm('test:action', name='测试操作', module='test')
        def test_action(request):
            return 'success'
            
        # validateDecoratorattribute
        self.assertTrue(hasattr(test_action, '_perm_code'))
        self.assertEqual(test_action._perm_code, 'test:action')
        self.assertEqual(test_action._perm_name, '测试操作')
        self.assertEqual(test_action._perm_module, 'test')
        
    def test_permission_registry(self):
        """testpermissionRegister表"""
        @require_perm('test:action1', name='测试操作1', module='test')
        def action1(request):
            pass
            
        @require_perm('test:action2', name='测试操作2', module='test')
        def action2(request):
            pass
            
        # validateRegister表
        entries = PermissionRegistry.all_entries()
        self.assertEqual(len(entries), 2)
        self.assertIn('test:action1', entries)
        self.assertIn('test:action2', entries)
        
    def test_permission_check_logic(self):
        """testpermissioncheck逻辑"""
        # createtest数据
        user = User.objects.create_user(
            username='testuser',
            password='testpass'
        )
        
        role = Role.objects.create(
            name='测试角色',
            key='test_role'
        )
        user.role.add(role)
        
        perm = PermissionCode.objects.create(
            code='test:action',
            name='测试操作',
            module='test'
        )
        
        # 未分配permission时, shouldnopermission
        has_perm = RolePermission.objects.filter(
            role=role,
            permission=perm
        ).exists()
        self.assertFalse(has_perm)
        
        # 分配permission后, should有permission
        RolePermission.objects.create(role=role, permission=perm)
        has_perm = RolePermission.objects.filter(
            role=role,
            permission=perm
        ).exists()
        self.assertTrue(has_perm)


class PermissionCodeAPITest(TestCase):
    """Permission code API test"""
    
    def setUp(self):
        """test数据准备"""
        self.client = APIClient()

        # 先createAdministratorRole(create_superuser depend on)
        Role.objects.get_or_create(
            name='管理员',
            defaults={'key': 'admin', 'status': True}
        )
        
        # create超级User
        self.admin_user = User.objects.create_superuser(
            username='admin',
            password='admin123'
        )
        
        # create普通User
        self.normal_user = User.objects.create_user(
            username='user',
            password='user123'
        )
        
        self.role = Role.objects.create(
            name='测试角色',
            key='test_role'
        )
        self.normal_user.role.add(self.role)
        
    def test_list_permission_codes(self):
        """testFetchPermission codelist"""
        # createtest数据
        PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user'
        )
        PermissionCode.objects.create(
            code='user:list',
            name='查看用户',
            module='user'
        )
        
        # Administrator访问
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get('/api/system/permission_code/')
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['data']), 2)
        
    def test_get_role_permissions(self):
        """testFetchRolepermission"""
        # createPermission code
        perm1 = PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user'
        )
        perm2 = PermissionCode.objects.create(
            code='user:list',
            name='查看用户',
            module='user'
        )
        
        # 只分配一 permission
        RolePermission.objects.create(
            role=self.role,
            permission=perm1
        )
        
        # 访问 API
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(
            f'/api/system/permission_code/get_role_permissions/?role_id={self.role.id}'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # validatereturn数据结构
        self.assertIn('data', response.data)
        
    def test_set_role_permissions(self):
        """testsettingRolepermission"""
        # createPermission code
        perm1 = PermissionCode.objects.create(
            code='user:create',
            name='创建用户',
            module='user'
        )
        perm2 = PermissionCode.objects.create(
            code='user:list',
            name='查看用户',
            module='user'
        )
        
        # settingpermission
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.put(
            '/api/system/permission_code/set_role_permissions/',
            {
                'role_id': self.role.id,
                'permission_codes': ['user:create', 'user:list']
            },
            format='json'
        )
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # validatepermission已分配
        role_perms = RolePermission.objects.filter(role=self.role)
        self.assertEqual(role_perms.count(), 2)


class PermissionIntegrationTest(TestCase):
    """permission系统Integration test"""
    
    def setUp(self):
        """test数据准备"""
        self.client = APIClient()
        
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass'
        )
        
        self.role = Role.objects.create(
            name='测试角色',
            key='test_role'
        )
        self.user.role.add(self.role)
        
    def test_full_permission_flow(self):
        """test完整权Rate limit程"""
        # 1. createPermission code
        perm = PermissionCode.objects.create(
            code='test:action',
            name='测试操作',
            module='test'
        )
        
        # 2. validateUsernopermission
        has_perm = RolePermission.objects.filter(
            role=self.role,
            permission=perm
        ).exists()
        self.assertFalse(has_perm)
        
        # 3. 分配permission
        RolePermission.objects.create(
            role=self.role,
            permission=perm
        )
        
        # 4. validateUser有permission
        has_perm = RolePermission.objects.filter(
            role=self.role,
            permission=perm
        ).exists()
        self.assertTrue(has_perm)
        
        # 5. validatecanviaRoleQuerypermission
        user_perms = PermissionCode.objects.filter(
            permission_roles__role=self.role
        )
        self.assertIn(perm, user_perms)
        
        # 6. validatecanviapermissionQueryRole
        perm_roles = Role.objects.filter(
            role_permissions__permission=perm
        )
        self.assertIn(self.role, perm_roles)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])