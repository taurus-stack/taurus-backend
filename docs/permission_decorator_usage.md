# 权限装饰器使用指南

## 概述

权限装饰器系统允许开发者在视图方法上直接声明接口权限码，实现权限与角色的直接关联管理（不依赖菜单）。

**核心特性：**
- 权限码直接关联角色，无需通过菜单中转
- 支持装饰单个方法或整个 ViewSet
- 自动注册权限码到数据库
- 前端可视化管理权限分配

## 核心概念

### 1. 权限装饰器 `@require_perm`

在视图方法上使用装饰器声明该接口需要的权限码。

**参数说明：**
- `perm_code`: 权限码（唯一），如 `"user:create"`
- `name`: 权限名称，如 `"新增用户"`
- `module`: 所属模块，如 `"user"`，用于分组管理
- `description`: 权限描述
- `methods`: 限定的 HTTP 方法，如 `['POST']`
- `roles`: 自动关联的角色列表，如 `['admin', 'operator']`，运行 `register_perms` 时会自动创建这些角色并分配权限

### 2. ViewSet 批量权限装饰器 `@require_viewset_perms`

为整个 ViewSet 的标准 CRUD 方法批量添加权限。

**参数说明：**
- `list`, `retrieve`, `create`, `update`, `destroy`, `partial_update`: 各操作对应的权限码
- `module`: 所属模块

### 3. 权限注册流程

1. 在视图方法上使用 `@require_perm` 或 `@require_viewset_perms` 装饰器
2. 运行管理命令扫描并注册权限码到数据库
3. 在前端"权限码管理"页面查看和管理权限码
4. 在前端"角色管理"页面为角色分配接口权限

## 使用示例

### 示例 1: 装饰单个 action 方法

```python
from dvadmin.utils.permission_decorator import require_perm
from rest_framework.decorators import action

class UserViewSet(CustomModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    
    @require_perm('user:export', name='导出用户', module='user')
    @action(methods=['post'], detail=False)
    def export(self, request):
        """导出用户数据"""
        return SuccessResponse(data={"message": "导出成功"})
    
    @require_perm('user:import', name='导入用户', module='user')
    @action(methods=['post'], detail=False)
    def import_users(self, request):
        """导入用户数据"""
        return SuccessResponse(data={"message": "导入成功"})
```

### 示例 2: 装饰整个 ViewSet（批量添加权限）

```python
from dvadmin.utils.permission_decorator import require_viewset_perms

@require_viewset_perms(
    list='user:list',
    retrieve='user:retrieve',
    create='user:create',
    update='user:update',
    destroy='user:destroy',
    module='user',
)
class UserViewSet(CustomModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
```

这种方式会自动为标准的 CRUD 方法添加权限检查。

### 示例 3: 在普通视图函数上使用

```python
from rest_framework.decorators import api_view
from dvadmin.utils.permission_decorator import require_perm
from dvadmin.utils.json_response import SuccessResponse

@require_perm('dashboard:stats', name='获取仪表盘统计', module='dashboard')
@api_view(['GET'])
def dashboard_stats(request):
    """获取仪表盘统计数据"""
    data = {
        "user_count": 100,
        "role_count": 10,
        "menu_count": 50
    }
    return SuccessResponse(data=data)
```

## 管理命令

### 注册权限码

```bash
# 扫描所有应用并注册权限码
python manage.py register_perms

# 预览模式(不写入数据库)
python manage.py register_perms --dry-run

# 自动创建角色(根据 module 字段)
python manage.py register_perms --auto-create-roles

# 自动分配权限到对应角色
python manage.py register_perms --auto-create-roles --auto-assign
```

**命令说明:**
- `register_perms`: 扫描所有视图文件,收集使用 `@require_perm` 和 `@require_viewset_perms` 装饰器标记的权限码
- `--dry-run`: 仅显示将要注册的权限码,不实际写入数据库
- `--auto-create-roles`: 根据权限码的 `module` 字段自动创建对应的角色(如果角色不存在)
- `--auto-assign`: 自动将权限码分配给对应 module 的角色

### 权限码命名规范

建议使用 `模块:操作` 的格式,例如:
- `user:create` - 用户创建
- `user:list` - 用户列表
- `task:execute` - 任务执行
- `host:manage` - 主机管理

## 前端配置

### 1. 添加菜单

在后台管理系统的"菜单管理"中添加权限码管理菜单:

- **菜单名称**: 权限码管理
- **路由地址**: /system/permission
- **组件路径**: system/permission/index
- **权限标识**: permission:list

### 2. 角色管理页面

角色管理页面已自动集成了"接口权限"按钮,点击后会弹出权限码分配对话框:

- 按模块分组显示所有权限码
- 支持全选/取消全选某个模块的权限
- 实时显示已选中的权限数量

### 3. 权限码管理页面

权限码管理页面提供以下功能:

- 查看所有已注册的权限码
- 按模块、状态筛选权限码
- 手动新增、编辑、删除权限码
- 查看权限码的描述和使用位置

## API 接口说明

### 1. 获取角色的权限码列表

**接口**: `GET /api/system/permission_code/get_role_permissions/`

**参数**:
- `role_id`: 角色ID

**返回**:
```json
{
  "code": 2000,
  "data": [
    {
      "module": "user",
      "permissions": [
        {
          "id": 1,
          "code": "user:create",
          "name": "创建用户",
          "module": "user",
          "description": "创建新用户",
          "isCheck": true
        }
      ]
    }
  ]
}
```

### 2. 设置角色的权限码

**接口**: `PUT /api/system/permission_code/set_role_permissions/`

**请求体**:
```json
{
  "role_id": 1,
  "permission_codes": ["user:create", "user:list", "user:update"]
}
```

**返回**:
```json
{
  "code": 2000,
  "msg": "权限设置成功"
}
```

### 3. 获取权限码列表

**接口**: `GET /api/system/permission_code/`

**参数**:
- `page`: 页码
- `limit`: 每页数量
- `module`: 模块名称(可选)
- `status`: 状态(可选)

**返回**:
```json
{
  "code": 2000,
  "data": {
    "count": 10,
    "results": [
      {
        "id": 1,
        "code": "user:create",
        "name": "创建用户",
        "module": "user",
        "description": "创建新用户",
        "status": true
      }
    ]
  }
}
```

### 4. 获取所有模块列表

**接口**: `GET /api/system/permission_code/get_modules/`

**返回**:
```json
{
  "code": 2000,
  "data": ["user", "task", "host", "system"]
}
```

## 权限检查流程

1. **装饰器注册**: 在视图方法上使用 `@require_perm` 装饰器
2. **权限码收集**: 运行 `register_perms` 命令扫描并注册到 `PermissionCode` 表
3. **角色分配**: 在前端角色管理页面为角色分配权限码,保存到 `RolePermission` 表
4. **权限验证**: 装饰器在运行时检查用户角色是否拥有对应的权限码

**权限检查逻辑**:
```python
# 装饰器内部逻辑
def wrapper(self, request, *args, **kwargs):
    # 1. 超级管理员直接通过
    if request.user.is_superuser:
        return func(self, request, *args, **kwargs)
    
    # 2. 检查用户角色是否拥有该权限码
    user_roles = request.user.roles.all()
    has_permission = RolePermission.objects.filter(
        role__in=user_roles,
        permission__code=perm_code
    ).exists()
    
    if not has_permission:
        raise PermissionDenied(f"没有权限: {perm_code}")
    
    return func(self, request, *args, **kwargs)
```

## 与菜单权限的区别

| 特性 | 菜单权限 | 接口权限码 |
|------|---------|-----------|
| 关联方式 | 角色 → 菜单 → 按钮 | 角色 → 权限码 |
| 管理粒度 | 页面级别 | 接口级别 |
| 配置方式 | 菜单管理页面 | 装饰器 + 角色管理 |
| 适用场景 | 控制页面访问 | 控制接口调用 |
| 灵活性 | 需要手动配置 | 代码即配置 |

## 最佳实践

1. **权限码命名**: 使用 `模块:操作` 格式,保持语义清晰
2. **模块划分**: 按业务模块划分权限,便于管理和分配
3. **角色设计**: 根据职责设计角色,避免权限过于分散
4. **定期审查**: 定期检查角色权限分配,确保符合最小权限原则
5. **文档同步**: 在权限码的 description 中说明用途和使用场景

## 常见问题

### Q1: 装饰器不生效?

**检查项**:
- 是否运行了 `register_perms` 命令注册权限码
- 权限码是否在数据库 `PermissionCode` 表中
- 用户角色是否分配了该权限码
- 装饰器是否正确导入: `from dvadmin.utils.permission_decorator import require_perm`

### Q2: 如何批量给角色分配权限?

使用管理命令:
```bash
# 自动创建角色并分配权限
python manage.py register_perms --auto-create-roles --auto-assign
```

### Q3: 权限码和菜单权限可以同时使用吗?

可以。两种权限系统是独立的,互不影响:
- 菜单权限控制页面访问
- 接口权限码控制接口调用

### Q4: 如何查看某个角色有哪些权限?

在前端"角色管理"页面,点击角色的"接口权限"按钮,可以查看和修改该角色的权限码分配。

### Q5: 权限码可以动态添加吗?

可以。有两种方式:
1. 在代码中使用装饰器标记,然后运行 `register_perms` 命令
2. 在前端"权限码管理"页面手动添加

## 总结

权限装饰器系统提供了代码级别的权限控制能力,与菜单权限系统互补,共同构建完整的权限管理体系。

**核心优势**:
- ✅ 代码即配置,减少手动配置工作
- ✅ 权限码直接关联角色,无需通过菜单中转
- ✅ 支持批量操作和自动注册
- ✅ 前端可视化管理,操作便捷
- ✅ 与现有权限系统无缝集成
