"""
Permission code model - directly associated with role

No dependency on menu, permission codes directly associated with role, implementing more flexible permission management.
"""

from django.db import models
from dvadmin.utils.models import CoreModel, table_prefix


class PermissionCode(CoreModel):
    """
    Permission code table
    
    Stores all permission codes registered via decorator registry, independent of menu system.
    """
    code = models.CharField(
        max_length=100,
        unique=True,
        verbose_name="Permission code",
        help_text="Permission code, e.g. 'user:create'"
    )
    name = models.CharField(
        max_length=100,
        verbose_name="Permission name",
        help_text="Permission name, for display"
    )
    module = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Belonging modules",
        help_text="Belonging modules, for group management"
    )
    description = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Description",
        help_text="Permission description"
    )
    status = models.BooleanField(
        default=True,
        verbose_name="Status",
        help_text="Is enabled"
    )
    
    class Meta:
        db_table = table_prefix + "system_permission_code"
        verbose_name = "Permission code table"
        verbose_name_plural = verbose_name
        ordering = ("module", "code")
    
    def __str__(self):
        return f"{self.code} - {self.name}"


class RolePermission(CoreModel):
    """
    Role permission association table
    
    Directly associate permission codes with roles, independent of menus.
    """
    role = models.ForeignKey(
        to="Role",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="role_permissions",
        verbose_name="Role",
        help_text="Associated role"
    )
    permission = models.ForeignKey(
        to="PermissionCode",
        on_delete=models.CASCADE,
        db_constraint=False,
        related_name="permission_roles",
        verbose_name="Permission",
        help_text="Associated permission code"
    )
    
    class Meta:
        db_table = table_prefix + "system_role_permission"
        verbose_name = "Role-permission association table"
        verbose_name_plural = verbose_name
        ordering = ("role", "permission")
        unique_together = [("role", "permission")]
    
    def __str__(self):
        return f"{self.role.name} - {self.permission.code}"