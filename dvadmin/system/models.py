import hashlib
import os

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from application import dispatch
from dvadmin.utils.models import CoreModel, table_prefix


class Role(CoreModel):
    name = models.CharField(max_length=64, verbose_name="Role name", help_text="Role name")
    key = models.CharField(max_length=64, unique=True, verbose_name="Permission key", help_text="Permission key")
    sort = models.IntegerField(default=1, verbose_name="Role order", help_text="Role order")
    status = models.BooleanField(default=True, verbose_name="Role status", help_text="Role status")

    class Meta:
        db_table = table_prefix + "system_role"
        verbose_name = "Role table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)


class CustomUserManager(UserManager):

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        user = super(CustomUserManager, self).create_superuser(username, email, password, **extra_fields)
        user.set_password(password)
        try:
            user.role.add(Role.objects.get(key="admin"))
            user.save(using=self._db)
            return user
        except ObjectDoesNotExist:
            user.delete()
            raise ValidationError("Role `admin` does not exist, creation failed, please run python manage.py init first")


class Users(CoreModel, AbstractUser):
    username = models.CharField(max_length=150, unique=True, db_index=True, verbose_name="User account",
                                help_text="User account")
    email = models.EmailField(max_length=255, verbose_name="Email", null=True, blank=True, help_text="Email")
    mobile = models.CharField(max_length=255, verbose_name="Phone", null=True, blank=True, help_text="Phone")
    avatar = models.CharField(max_length=255, verbose_name="Avatar", null=True, blank=True, help_text="Avatar")
    name = models.CharField(max_length=40, verbose_name="Name", help_text="Name")
    GENDER_CHOICES = (
        (0, "Unknown"),
        (1, "Male"),
        (2, "Female"),
    )
    gender = models.IntegerField(
        choices=GENDER_CHOICES, default=0, verbose_name="Gender", null=True, blank=True, help_text="Gender"
    )
    USER_TYPE = (
        (0, "Backend user"),
        (1, "Frontend user"),
    )
    user_type = models.IntegerField(
        choices=USER_TYPE, default=0, verbose_name="User type", null=True, blank=True, help_text="User type"
    )
    post = models.ManyToManyField(to="Post", blank=True, verbose_name="Associated position", db_constraint=False,
                                  help_text="Associated position")
    role = models.ManyToManyField(to="Role", blank=True, verbose_name="Associated role", db_constraint=False,
                                  help_text="Associated role")
    dept = models.ForeignKey(
        to="Dept",
        verbose_name="Belonging dept",
        on_delete=models.PROTECT,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="Associated dept",
    )
    login_error_count = models.IntegerField(default=0, verbose_name="Login error count", help_text="Login error count")
    objects = CustomUserManager()

    def set_password(self, raw_password):
        super().set_password(hashlib.md5(raw_password.encode(encoding="UTF-8")).hexdigest())

    class Meta:
        db_table = table_prefix + "system_users"
        verbose_name = "User table"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class Post(CoreModel):
    name = models.CharField(null=False, max_length=64, verbose_name="Position name", help_text="Position name")
    code = models.CharField(max_length=32, verbose_name="Position code", help_text="Position code")
    sort = models.IntegerField(default=1, verbose_name="Position order", help_text="Position order")
    STATUS_CHOICES = (
        (0, "Separated"),
        (1, "Active"),
    )
    status = models.IntegerField(choices=STATUS_CHOICES, default=1, verbose_name="Position status", help_text="Position status")

    class Meta:
        db_table = table_prefix + "system_post"
        verbose_name = "Position table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)


class Dept(CoreModel):
    name = models.CharField(max_length=64, verbose_name="Dept name", help_text="Dept name")
    key = models.CharField(max_length=64, unique=True, null=True, blank=True, verbose_name="Association key", help_text="Association key")
    sort = models.IntegerField(default=1, verbose_name="Display order", help_text="Display order")
    owner = models.CharField(max_length=32, verbose_name="Owner", null=True, blank=True, help_text="Owner")
    phone = models.CharField(max_length=32, verbose_name="Contact phone", null=True, blank=True, help_text="Contact phone")
    email = models.EmailField(max_length=32, verbose_name="Email", null=True, blank=True, help_text="Email")
    status = models.BooleanField(default=True, verbose_name="Dept status", null=True, blank=True, help_text="Dept status")
    parent = models.ForeignKey(
        to="Dept",
        on_delete=models.CASCADE,
        default=None,
        verbose_name="Parent dept",
        db_constraint=False,
        null=True,
        blank=True,
        help_text="Parent dept",
    )

    @classmethod
    def recursion_all_dept(cls, dept_id: int, dept_all_list=None, dept_list=None):
        """
        Recursively get all subordinate depts of a dept
        :param dept_id: ID to get
        :param dept_all_list: All list
        :param dept_list: Recursive list
        :return:
        """
        if not dept_all_list:
            dept_all_list = Dept.objects.values("id", "parent")
        if dept_list is None:
            dept_list = [dept_id]
        for ele in dept_all_list:
            if ele.get("parent") == dept_id:
                dept_list.append(ele.get("id"))
                cls.recursion_all_dept(ele.get("id"), dept_all_list, dept_list)
        return list(set(dept_list))

    class Meta:
        db_table = table_prefix + "system_dept"
        verbose_name = "Department table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)


class Menu(CoreModel):
    parent = models.ForeignKey(
        to="Menu",
        on_delete=models.CASCADE,
        verbose_name="Parent menu",
        null=True,
        blank=True,
        db_constraint=False,
        help_text="Parent menu",
    )
    icon = models.CharField(max_length=64, verbose_name="Menu icon", null=True, blank=True, help_text="Menu icon")
    name = models.CharField(max_length=64, verbose_name="Menu name", help_text="Menu name")
    sort = models.IntegerField(default=1, verbose_name="Display order", null=True, blank=True, help_text="Display order")
    ISLINK_CHOICES = (
        (0, "No"),
        (1, "Yes"),
    )
    is_link = models.BooleanField(default=False, verbose_name="Is external link", help_text="Is external link")
    link_url = models.CharField(max_length=255, verbose_name="Link URL", null=True, blank=True, help_text="Link URL")
    is_catalog = models.BooleanField(default=False, verbose_name="Is directory", help_text="Is directory")
    web_path = models.CharField(max_length=128, verbose_name="Route path", null=True, blank=True, help_text="Route path")
    component = models.CharField(max_length=128, verbose_name="Component path", null=True, blank=True, help_text="Component path")
    component_name = models.CharField(max_length=50, verbose_name="Component name", null=True, blank=True,
                                      help_text="Component name")
    status = models.BooleanField(default=True, blank=True, verbose_name="Menu status", help_text="Menu status")
    cache = models.BooleanField(default=False, blank=True, verbose_name="Is page cached", help_text="Is page cached")
    visible = models.BooleanField(default=True, blank=True, verbose_name="Whether displayed in sidebar",
                                  help_text="Whether displayed in sidebar")
    is_iframe = models.BooleanField(default=False, blank=True, verbose_name="Display outside framework", help_text="Display outside framework")
    is_affix = models.BooleanField(default=False, blank=True, verbose_name="Is pinned", help_text="Is pinned")

    @classmethod
    def get_all_parent(cls, id: int, all_list=None, nodes=None):
        """
        Recursively get all levels of a given ID
        :param id: Parameter ID
        :param all_list: All list
        :param nodes: Recursive list
        :return: nodes
        """
        if not all_list:
            all_list = Menu.objects.values("id", "name", "parent")
        if nodes is None:
            nodes = []
        for ele in all_list:
            if ele.get("id") == id:
                parent_id = ele.get("parent")
                if parent_id is not None:
                    cls.get_all_parent(parent_id, all_list, nodes)
                nodes.append(ele)
        return nodes
    class Meta:
        db_table = table_prefix + "system_menu"
        verbose_name = "Menu table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)

class MenuField(CoreModel):
    model = models.CharField(max_length=64, verbose_name='Table name')
    menu = models.ForeignKey(to='Menu', on_delete=models.CASCADE, verbose_name='Menu', db_constraint=False)
    field_name = models.CharField(max_length=64, verbose_name='Model field name')
    title = models.CharField(max_length=64, verbose_name='Field display name')
    class Meta:
        db_table = table_prefix + "system_menu_field"
        verbose_name = "Menu field table"
        verbose_name_plural = verbose_name
        ordering = ("id",)

class FieldPermission(CoreModel):
    role = models.ForeignKey(to='Role', on_delete=models.CASCADE, verbose_name='Role', db_constraint=False)
    field = models.ForeignKey(to='MenuField', on_delete=models.CASCADE,related_name='menu_field', verbose_name='Field', db_constraint=False)
    is_query = models.BooleanField(default=1, verbose_name='Is queryable')
    is_create = models.BooleanField(default=1, verbose_name='Is creatable')
    is_update = models.BooleanField(default=1, verbose_name='Is updatable')

    class Meta:
        db_table = table_prefix + "system_field_permission"
        verbose_name = "Field permission table"
        verbose_name_plural = verbose_name
        ordering = ("id",)


class MenuButton(CoreModel):
    menu = models.ForeignKey(
        to="Menu",
        db_constraint=False,
        related_name="menuPermission",
        on_delete=models.CASCADE,
        verbose_name="Associated menu",
        help_text="Associated menu",
    )
    name = models.CharField(max_length=64, verbose_name="Name", help_text="Name")
    value = models.CharField(unique=True, max_length=64, verbose_name="Permission value", help_text="Permission value")
    api = models.CharField(max_length=200, verbose_name="API endpoint", help_text="API endpoint")
    METHOD_CHOICES = (
        (0, "GET"),
        (1, "POST"),
        (2, "PUT"),
        (3, "DELETE"),
    )
    method = models.IntegerField(default=0, verbose_name="API request method", null=True, blank=True,
                                 help_text="API request method")

    class Meta:
        db_table = table_prefix + "system_menu_button"
        verbose_name = "Menu permission table"
        verbose_name_plural = verbose_name
        ordering = ("-name",)


class RoleMenuPermission(CoreModel):
    role = models.ForeignKey(
        to="Role",
        db_constraint=False,
        related_name="role_menu",
        on_delete=models.CASCADE,
        verbose_name="Associated role",
        help_text="Associated role",
    )
    menu = models.ForeignKey(
        to="Menu",
        db_constraint=False,
        related_name="role_menu",
        on_delete=models.CASCADE,
        verbose_name="Associated menu",
        help_text="Associated menu",
    )

    class Meta:
        db_table = table_prefix + "role_menu_permission"
        verbose_name = "Role menu permission table"
        verbose_name_plural = verbose_name
        # ordering = ("-create_datetime",)


class RoleMenuButtonPermission(CoreModel):
    role = models.ForeignKey(
        to="Role",
        db_constraint=False,
        related_name="role_menu_button",
        on_delete=models.CASCADE,
        verbose_name="Associated role",
        help_text="Associated role",
    )
    menu_button = models.ForeignKey(
        to="MenuButton",
        db_constraint=False,
        related_name="menu_button_permission",
        on_delete=models.CASCADE,
        verbose_name="Associated menu button",
        help_text="Associated menu button",
        null=True,
        blank=True
    )
    DATASCOPE_CHOICES = (
        (0, "Self data only"),
        (1, "Current dept and subordinates"),
        (2, "Current dept only"),
        (3, "All data"),
        (4, "Custom data"),
    )
    data_range = models.IntegerField(default=0, choices=DATASCOPE_CHOICES, verbose_name="Data permission scope",
                                     help_text="Data permission scope")
    dept = models.ManyToManyField(to="Dept", blank=True, verbose_name="Data permission - associated dept", db_constraint=False,
                                  help_text="Data permission - associated dept")

    class Meta:
        db_table = table_prefix + "role_menu_button_permission"
        verbose_name = "Role button permission table"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class Dictionary(CoreModel):
    TYPE_LIST = (
        (0, "text"),
        (1, "number"),
        (2, "date"),
        (3, "datetime"),
        (4, "time"),
        (5, "files"),
        (6, "boolean"),
        (7, "images"),
    )
    label = models.CharField(max_length=100, blank=True, null=True, verbose_name="Dictionary label", help_text="Dictionary label")
    value = models.CharField(max_length=200, blank=True, null=True, verbose_name="Dictionary key", help_text="Dictionary key/actual value")
    parent = models.ForeignKey(
        to="self",
        related_name="sublist",
        db_constraint=False,
        on_delete=models.PROTECT,
        blank=True,
        null=True,
        verbose_name="Parent",
        help_text="Parent",
    )
    type = models.IntegerField(choices=TYPE_LIST, default=0, verbose_name="Data value type", help_text="Data value type")
    color = models.CharField(max_length=20, blank=True, null=True, verbose_name="Color", help_text="Color")
    is_value = models.BooleanField(default=False, verbose_name="Is a value",
                                   help_text="Whether it is a value, Used to store specific values")
    status = models.BooleanField(default=True, verbose_name="Status", help_text="Status")
    sort = models.IntegerField(default=1, verbose_name="Display order", null=True, blank=True, help_text="Display order")
    remark = models.CharField(max_length=2000, blank=True, null=True, verbose_name="Remark", help_text="Remark")

    class Meta:
        db_table = table_prefix + "system_dictionary"
        verbose_name = "Dictionary table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        super().save(force_insert, force_update, using, update_fields)
        dispatch.refresh_dictionary()  # Refresh dictionary config on update

    def delete(self, using=None, keep_parents=False):
        res = super().delete(using, keep_parents)
        dispatch.refresh_dictionary()
        return res


class OperationLog(CoreModel):
    request_modular = models.CharField(max_length=64, verbose_name="Request module", null=True, blank=True,
                                       help_text="Request module")
    request_path = models.CharField(max_length=400, verbose_name="Request URL", null=True, blank=True,
                                    help_text="Request URL")
    request_body = models.TextField(verbose_name="Request parameters", null=True, blank=True, help_text="Request parameters")
    request_method = models.CharField(max_length=8, verbose_name="Request method", null=True, blank=True,
                                      help_text="Request method")
    request_msg = models.TextField(verbose_name="Operation description", null=True, blank=True, help_text="Operation description")
    request_ip = models.CharField(max_length=32, verbose_name="Request IP address", null=True, blank=True,
                                  help_text="Request IP address")
    request_browser = models.CharField(max_length=64, verbose_name="Request browser", null=True, blank=True,
                                       help_text="Request browser")
    response_code = models.CharField(max_length=32, verbose_name="Response status code", null=True, blank=True,
                                     help_text="Response status code")
    request_os = models.CharField(max_length=64, verbose_name="Operating system", null=True, blank=True, help_text="Operating system")
    json_result = models.TextField(verbose_name="Return message", null=True, blank=True, help_text="Return message")
    status = models.BooleanField(default=False, verbose_name="Response status", help_text="Response status")

    class Meta:
        db_table = table_prefix + "system_operation_log"
        verbose_name = "Operation log"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


def media_file_name(instance, filename):
    h = instance.md5sum
    basename, ext = os.path.splitext(filename)
    return os.path.join("files", h[:1], h[1:2], h + ext.lower())


class FileList(CoreModel):
    name = models.CharField(max_length=200, null=True, blank=True, verbose_name="Name", help_text="Name")
    url = models.FileField(upload_to=media_file_name, null=True, blank=True,)
    file_url = models.CharField(max_length=255, blank=True, verbose_name="File URL", help_text="File URL")
    engine = models.CharField(max_length=100, default='local', blank=True, verbose_name="Engine", help_text="Engine")
    mime_type = models.CharField(max_length=100, blank=True, verbose_name="MIME type", help_text="MIME type")
    size = models.CharField(max_length=36, blank=True, verbose_name="File size", help_text="File size")
    md5sum = models.CharField(max_length=36, blank=True, verbose_name="File MD5", help_text="File MD5")

    def save(self, *args, **kwargs):
        if not self.md5sum:  # file is new
            md5 = hashlib.md5()
            for chunk in self.url.chunks():
                md5.update(chunk)
            self.md5sum = md5.hexdigest()
        if not self.size:
            self.size = self.url.size
        if not self.file_url:
            url = media_file_name(self, self.name)
            self.file_url = f'media/{url}'
        super(FileList, self).save(*args, **kwargs)

    class Meta:
        db_table = table_prefix + "system_file_list"
        verbose_name = "File management"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class Area(CoreModel):
    name = models.CharField(max_length=100, verbose_name="Name", help_text="Name")
    code = models.CharField(max_length=20, verbose_name="Area code", help_text="Area code", unique=True, db_index=True)
    level = models.BigIntegerField(verbose_name="Area level (1 Province 2 City 3 District 4 Township)",
                                   help_text="Area level (1 Province 2 City 3 District 4 Township)")
    pinyin = models.CharField(max_length=255, verbose_name="Pinyin", help_text="Pinyin")
    initials = models.CharField(max_length=20, verbose_name="Initials", help_text="Initials")
    enable = models.BooleanField(default=True, verbose_name="Is enabled", help_text="Is enabled")
    pcode = models.ForeignKey(
        to="self",
        verbose_name="Parent area code",
        to_field="code",
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="Parent area code",
    )

    class Meta:
        db_table = table_prefix + "system_area"
        verbose_name = "Area table"
        verbose_name_plural = verbose_name
        ordering = ("code",)

    def __str__(self):
        return f"{self.name}"


class ApiWhiteList(CoreModel):
    url = models.CharField(max_length=200, help_text="URL address", verbose_name="URL")
    METHOD_CHOICES = (
        (0, "GET"),
        (1, "POST"),
        (2, "PUT"),
        (3, "DELETE"),
    )
    method = models.IntegerField(default=0, verbose_name="API request method", null=True, blank=True,
                                 help_text="API request method")
    enable_datasource = models.BooleanField(default=True, verbose_name="Enable data permission", help_text="Enable data permission",
                                            blank=True)

    class Meta:
        db_table = table_prefix + "api_white_list"
        verbose_name = "API whitelist"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class SystemConfig(CoreModel):
    parent = models.ForeignKey(
        to="self",
        verbose_name="Parent",
        on_delete=models.CASCADE,
        db_constraint=False,
        null=True,
        blank=True,
        help_text="Parent",
    )
    title = models.CharField(max_length=50, verbose_name="Title", help_text="Title")
    key = models.CharField(max_length=100, verbose_name="Key", help_text="Key", db_index=True)
    value = models.JSONField(max_length=100, verbose_name="Value", help_text="Value", null=True, blank=True)
    sort = models.IntegerField(default=0, verbose_name="Sort order", help_text="Sort order", blank=True)
    status = models.BooleanField(default=True, verbose_name="Enabled status", help_text="Enabled status")
    data_options = models.JSONField(verbose_name="Data options", help_text="Data options", null=True, blank=True)
    FORM_ITEM_TYPE_LIST = (
        (0, "text"),
        (1, "datetime"),
        (2, "date"),
        (3, "textarea"),
        (4, "select"),
        (5, "checkbox"),
        (6, "radio"),
        (7, "img"),
        (8, "file"),
        (9, "switch"),
        (10, "number"),
        (11, "array"),
        (12, "imgs"),
        (13, "foreignkey"),
        (14, "manytomany"),
        (15, "time"),
    )
    form_item_type = models.IntegerField(
        choices=FORM_ITEM_TYPE_LIST, verbose_name="Form type", help_text="Form type", default=0, blank=True
    )
    rule = models.JSONField(null=True, blank=True, verbose_name="Validation rules", help_text="Validation rules")
    placeholder = models.CharField(max_length=50, null=True, blank=True, verbose_name="Placeholder", help_text="Placeholder")
    setting = models.JSONField(null=True, blank=True, verbose_name="Config", help_text="Config")

    class Meta:
        db_table = table_prefix + "system_config"
        verbose_name = "System config table"
        verbose_name_plural = verbose_name
        ordering = ("sort",)
        unique_together = (("key", "parent_id"),)

    def __str__(self):
        return f"{self.title}"

    def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
        super().save(force_insert, force_update, using, update_fields)
        dispatch.refresh_system_config()  # Refresh system config on update

    def delete(self, using=None, keep_parents=False):
        res = super().delete(using, keep_parents)
        dispatch.refresh_system_config()
        return res


class LoginLog(CoreModel):
    LOGIN_TYPE_CHOICES = ((1, "Normal login"), (2, "WeChat QR login"),)
    username = models.CharField(max_length=32, verbose_name="Login username", null=True, blank=True, help_text="Login username")
    ip = models.CharField(max_length=32, verbose_name="Login IP", null=True, blank=True, help_text="Login IP")
    agent = models.TextField(verbose_name="Agent info", null=True, blank=True, help_text="Agent info")
    browser = models.CharField(max_length=200, verbose_name="Browser name", null=True, blank=True, help_text="Browser name")
    os = models.CharField(max_length=200, verbose_name="Operating system", null=True, blank=True, help_text="Operating system")
    continent = models.CharField(max_length=50, verbose_name="Continent", null=True, blank=True, help_text="Continent")
    country = models.CharField(max_length=50, verbose_name="Country", null=True, blank=True, help_text="Country")
    province = models.CharField(max_length=50, verbose_name="Province", null=True, blank=True, help_text="Province")
    city = models.CharField(max_length=50, verbose_name="City", null=True, blank=True, help_text="City")
    district = models.CharField(max_length=50, verbose_name="District", null=True, blank=True, help_text="District")
    isp = models.CharField(max_length=50, verbose_name="ISP", null=True, blank=True, help_text="ISP")
    area_code = models.CharField(max_length=50, verbose_name="Area code", null=True, blank=True, help_text="Area code")
    country_english = models.CharField(max_length=50, verbose_name="English full name", null=True, blank=True,
                                       help_text="English full name")
    country_code = models.CharField(max_length=50, verbose_name="Short code", null=True, blank=True, help_text="Short code")
    longitude = models.CharField(max_length=50, verbose_name="Longitude", null=True, blank=True, help_text="Longitude")
    latitude = models.CharField(max_length=50, verbose_name="Latitude", null=True, blank=True, help_text="Latitude")
    login_type = models.IntegerField(default=1, choices=LOGIN_TYPE_CHOICES, verbose_name="Login type",
                                     help_text="Login type")

    class Meta:
        db_table = table_prefix + "system_login_log"
        verbose_name = "Login log"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class MessageCenter(CoreModel):
    title = models.CharField(max_length=100, verbose_name="Title", help_text="Title")
    content = models.TextField(verbose_name="Content", help_text="Content")
    target_type = models.IntegerField(default=0, verbose_name="Target type", help_text="Target type")
    target_user = models.ManyToManyField(to=Users, related_name='user', through='MessageCenterTargetUser',
                                         through_fields=('messagecenter', 'users'), blank=True, verbose_name="Target users",
                                         help_text="Target users")
    target_dept = models.ManyToManyField(to=Dept, blank=True, db_constraint=False,
                                         verbose_name="Target depts", help_text="Target depts")
    target_role = models.ManyToManyField(to=Role, blank=True, db_constraint=False,
                                         verbose_name="Target roles", help_text="Target roles")

    class Meta:
        db_table = table_prefix + "message_center"
        verbose_name = "Message center"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


class MessageCenterTargetUser(CoreModel):
    users = models.ForeignKey(Users, related_name="target_user", on_delete=models.CASCADE, db_constraint=False,
                              verbose_name="Associated user table", help_text="Associated user table")
    messagecenter = models.ForeignKey(MessageCenter, on_delete=models.CASCADE, db_constraint=False,
                                      verbose_name="Associated message center table", help_text="Associated message center table")
    is_read = models.BooleanField(default=False, blank=True, null=True, verbose_name="Is read", help_text="Is read")

    class Meta:
        db_table = table_prefix + "message_center_target_user"
        verbose_name = "Message center target user table"
        verbose_name_plural = verbose_name


class UserPreference(CoreModel):
    user = models.OneToOneField(to=Users, on_delete=models.CASCADE, verbose_name="User", help_text="User")
    theme = models.CharField(max_length=20, default='light', verbose_name="Theme mode", help_text="Theme mode: light/dark/auto")
    primary_color = models.CharField(max_length=20, default='#409EFF', verbose_name="Primary color", help_text="Primary color")
    language = models.CharField(max_length=10, default='zh-CN', verbose_name="Language", help_text="Language setting")
    compact = models.BooleanField(default=False, verbose_name="Compact mode", help_text="Compact mode")
    timezone = models.CharField(max_length=50, default='Asia/Shanghai', verbose_name="Timezone", help_text="Timezone")
    date_format = models.CharField(max_length=20, default='YYYY-MM-DD', verbose_name="Date format", help_text="Date format")
    time_format = models.CharField(max_length=10, default='24h', verbose_name="Time format", help_text="Time format: 24h/12h")
    notify_message = models.BooleanField(default=True, verbose_name="In-site message notification", help_text="In-site message notification")
    notify_email = models.BooleanField(default=True, verbose_name="Email notification", help_text="Email notification")
    notify_desktop = models.BooleanField(default=False, verbose_name="Browser desktop notification", help_text="Browser desktop notification")
    sound_enabled = models.BooleanField(default=True, verbose_name="Sound prompt", help_text="Sound prompt")
    default_page = models.CharField(max_length=100, default='/dashboard', verbose_name="Default homepage", help_text="Default homepage path")
    page_size = models.IntegerField(default=20, verbose_name="List page size", help_text="Items per page in list")

    class Meta:
        db_table = table_prefix + "user_preference"
        verbose_name = "User preference settings"
        verbose_name_plural = verbose_name


class UserFavorite(CoreModel):
    user = models.ForeignKey(to=Users, on_delete=models.CASCADE, verbose_name="User", help_text="User", related_name="favorites")
    title = models.CharField(max_length=200, verbose_name="Title", help_text="Favorite title")
    category = models.CharField(max_length=50, null=True, blank=True, verbose_name="Category", help_text="Category")
    url = models.CharField(max_length=500, null=True, blank=True, verbose_name="Link URL", help_text="Link URL")
    description = models.TextField(null=True, blank=True, verbose_name="Description", help_text="Description")

    class Meta:
        db_table = table_prefix + "user_favorite"
        verbose_name = "User favorites"
        verbose_name_plural = verbose_name
        ordering = ("-create_datetime",)


# Import permission code models (directly associated with role, no dependency on menu)
from dvadmin.system.permission_models import PermissionCode, RolePermission

__all__ = [
    'Role', 'Users', 'Post', 'Dept', 'Menu', 'MenuField', 'FieldPermission',
    'MenuButton', 'RoleMenuPermission', 'RoleMenuButtonPermission',
    'Dictionary', 'OperationLog', 'FileList', 'Area', 'ApiWhiteList',
    'SystemConfig', 'LoginLog', 'MessageCenter', 'MessageCenterTargetUser',
    'PermissionCode', 'RolePermission', 'UserPreference', 'UserFavorite',
]