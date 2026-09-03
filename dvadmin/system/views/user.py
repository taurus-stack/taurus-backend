import hashlib

from django.contrib.auth.hashers import make_password, check_password
from django_restql.fields import DynamicSerializerMethodField
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from django.db import connection
from django.db.models import Q
from application import dispatch
from dvadmin.system.models import Users, Role, Dept, UserPreference, UserFavorite
from dvadmin.system.views.role import RoleSerializer
from dvadmin.utils.json_response import ErrorResponse, DetailResponse, SuccessResponse
from dvadmin.utils.permission_decorator import require_viewset_perms, require_perm
from dvadmin.utils.serializers import CustomModelSerializer
from dvadmin.utils.validator import CustomUniqueValidator
from dvadmin.utils.viewset import CustomModelViewSet


def recursion(instance, parent, result):
    new_instance = getattr(instance, parent, None)
    res = []
    data = getattr(instance, result, None)
    if data:
        res.append(data)
    if new_instance:
        array = recursion(new_instance, parent, result)
        res += array
    return res


class UserSerializer(CustomModelSerializer):
    """
    User management - Serializer
    """
    dept_name = serializers.CharField(source='dept.name', read_only=True)
    role_info = DynamicSerializerMethodField()
    dept_name_all = serializers.SerializerMethodField()

    class Meta:
        model = Users
        read_only_fields = ["id"]
        exclude = ["password"]
        extra_kwargs = {
            "post": {"required": False},
            "mobile": {"required": False},
        }

    def get_dept_name_all(self, instance):
        dept_name_all = recursion(instance.dept, "parent", "name")
        dept_name_all.reverse()
        return "/".join(dept_name_all)

    def get_role_info(self, instance, parsed_query):
        roles = instance.role.all()
        # You can do what ever you want in here
        # `parsed_query` param is passed to BookSerializer to allow further querying
        serializer = RoleSerializer(
            roles,
            many=True,
            parsed_query=parsed_query
        )
        return serializer.data


class UserCreateSerializer(CustomModelSerializer):
    """
    User create - Serializer
    """

    username = serializers.CharField(
        max_length=50,
        validators=[
            CustomUniqueValidator(queryset=Users.objects.all(), message="Account must be unique")
        ],
    )
    password = serializers.CharField(
        required=False,
    )

    def validate_password(self, value):
        """
        Validate password
        """
        md5 = hashlib.md5()
        md5.update(value.encode('utf-8'))
        md5_password = md5.hexdigest()
        return make_password(md5_password)

    def save(self, **kwargs):
        data = super().save(**kwargs)
        data.dept_belong_id = data.dept_id
        data.save()
        data.post.set(self.initial_data.get("post", []))
        return data

    class Meta:
        model = Users
        fields = "__all__"
        read_only_fields = ["id"]
        extra_kwargs = {
            "post": {"required": False},
            "mobile": {"required": False},
        }


class UserUpdateSerializer(CustomModelSerializer):
    """
    User update - Serializer
    """

    username = serializers.CharField(
        max_length=50,
        validators=[
            CustomUniqueValidator(queryset=Users.objects.all(), message="Account must be unique")
        ],
    )

    def validate_is_active(self, value):
        """
        Change active status
        """
        print(111, value)
        if value:
            self.initial_data["login_error_count"] = 0
        return value

    def save(self, **kwargs):
        data = super().save(**kwargs)
        data.dept_belong_id = data.dept_id
        data.save()
        data.post.set(self.initial_data.get("post", []))
        return data

    class Meta:
        model = Users
        read_only_fields = ["id", "password"]
        fields = "__all__"
        extra_kwargs = {
            "post": {"required": False, "read_only": True},
            "mobile": {"required": False},
        }


class UserInfoUpdateSerializer(CustomModelSerializer):
    """
    User info update - Serializer
    """
    mobile = serializers.CharField(
        max_length=50,
        validators=[
            CustomUniqueValidator(queryset=Users.objects.all(), message="Phone number must be unique")
        ],
        allow_blank=True
    )

    def update(self, instance, validated_data):
        return super().update(instance, validated_data)

    class Meta:
        model = Users
        fields = ['email', 'mobile', 'avatar', 'name', 'gender']
        extra_kwargs = {
            "post": {"required": False, "read_only": True},
            "mobile": {"required": False},
        }


class ExportUserProfileSerializer(CustomModelSerializer):
    """
    UserExport Serializationserver
    """

    last_login = serializers.DateTimeField(
        format="%Y-%m-%d %H:%M:%S", required=False, read_only=True
    )
    is_active = serializers.SerializerMethodField(read_only=True)
    dept_name = serializers.CharField(source="dept.name", default="")
    dept_owner = serializers.CharField(source="dept.owner", default="")
    gender = serializers.CharField(source="get_gender_display", read_only=True)

    def get_is_active(self, instance):
        return "Enabled" if instance.is_active else "Disabled"

    class Meta:
        model = Users
        fields = (
            "username",
            "name",
            "email",
            "mobile",
            "gender",
            "is_active",
            "last_login",
            "dept_name",
            "dept_owner",
        )


class UserProfileImportSerializer(CustomModelSerializer):
    password = serializers.CharField(read_only=True, required=False)

    def save(self, **kwargs):
        data = super().save(**kwargs)
        password = hashlib.new(
            "md5", str(self.initial_data.get("password", "admin123456")).encode(encoding="UTF-8")
        ).hexdigest()
        data.set_password(password)
        data.save()
        return data

    class Meta:
        model = Users
        exclude = (
            "post",
            "user_permissions",
            "groups",
            "is_superuser",
            "date_joined",
        )


class UserViewSet(CustomModelViewSet):
    """
    User interface
    list: List query
    create: Create
    update: Modify
    retrieve: Retrieve single
    destroy: Delete
    """

    queryset = Users.objects.exclude(is_superuser=1).all()
    serializer_class = UserSerializer
    create_serializer_class = UserCreateSerializer
    update_serializer_class = UserUpdateSerializer
    filter_fields = ["name", "username", "gender", "is_active", "dept", "user_type"]
    search_fields = ["username", "name", "dept__name", "role__name"]
    # Export
    export_field_label = {
        "username": "Username",
        "name": "User name",
        "email": "Email",
        "mobile": "Mobile phone",
        "gender": "Gender",
        "is_active": "Account status",
        "last_login": "Last login time",
        "dept_name": "Department name",
        "dept_owner": "Department owner",
    }
    export_serializer_class = ExportUserProfileSerializer
    # Import
    import_serializer_class = UserProfileImportSerializer
    import_field_dict = {
        "username": "Login account",
        "name": "User name",
        "email": "Email",
        "mobile": "Mobile phone",
        "gender": {
            "title": "Gender",
            "choices": {
                "data": {"Unknown": 2, "Male": 1, "Female": 0},
            }
        },
        "is_active": {
            "title": "Account status",
            "choices": {
                "data": {"Enabled": True, "Disabled": False},
            }
        },
        "dept": {"title": "Department", "choices": {"queryset": Dept.objects.filter(status=True), "values_name": "name"}},
        "role": {"title": "Role", "choices": {"queryset": Role.objects.filter(status=True), "values_name": "name"}},
    }

    def perform_create(self, serializer):
        # === 配额校验：社区版用户上限（排除 superuser） ===
        from taurus.editions.loader import check_quota as _check_quota
        _check_quota('max_users', Users.objects.exclude(is_superuser=1).count(), '用户账号')
        super().perform_create(serializer)

    @action(methods=["GET"], detail=False, permission_classes=[IsAuthenticated])
    def user_info(self, request):
        """Fetch current user info"""
        user = request.user
        result = {
            "id": user.id,
            "username": user.username,
            "name": user.name,
            "mobile": user.mobile,
            "user_type": user.user_type,
            "gender": user.gender,
            "email": user.email,
            "avatar": user.avatar,
            "dept": user.dept_id,
            "is_superuser": user.is_superuser,
            "role": user.role.values_list('id', flat=True),
        }
        if hasattr(connection, 'tenant'):
            result['tenant_id'] = connection.tenant and connection.tenant.id
            result['tenant_name'] = connection.tenant and connection.tenant.name
        dept = getattr(user, 'dept', None)
        if dept:
            result['dept_info'] = {
                'dept_id': dept.id,
                'dept_name': dept.name
            }
        else:
            result['dept_info'] = {
                'dept_id': None,
                'dept_name': "No department"
            }
        role = getattr(user, 'role', None)
        if role:
            result['role_info'] = role.values('id', 'name', 'key')
        return DetailResponse(data=result, msg="Retrieved successfully")

    @action(methods=["PUT"], detail=False, permission_classes=[IsAuthenticated])
    def update_user_info(self, request):
        """Modify current user info"""
        serializer = UserInfoUpdateSerializer(request.user, data=request.data, request=request)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return DetailResponse(data=None, msg="Updated successfully")

    @action(methods=["GET", "PUT"], detail=False, permission_classes=[IsAuthenticated])
    def preference(self, request):
        """Fetch or Update current user preference settings"""
        user = request.user
        if request.method == "GET":
            pref, created = UserPreference.objects.get_or_create(user=user)
            data = {
                "theme": pref.theme,
                "primaryColor": pref.primary_color,
                "language": pref.language,
                "compact": pref.compact,
                "timezone": pref.timezone,
                "dateFormat": pref.date_format,
                "timeFormat": pref.time_format,
                "notifyMessage": pref.notify_message,
                "notifyEmail": pref.notify_email,
                "notifyDesktop": pref.notify_desktop,
                "soundEnabled": pref.sound_enabled,
                "defaultPage": pref.default_page,
                "pageSize": pref.page_size,
            }
            return DetailResponse(data=data, msg="Retrieved successfully")
        elif request.method == "PUT":
            pref, created = UserPreference.objects.get_or_create(user=user)
            data = request.data
            pref.theme = data.get("theme", pref.theme)
            pref.primary_color = data.get("primaryColor", pref.primary_color)
            pref.language = data.get("language", pref.language)
            pref.compact = data.get("compact", pref.compact)
            pref.timezone = data.get("timezone", pref.timezone)
            pref.date_format = data.get("dateFormat", pref.date_format)
            pref.time_format = data.get("timeFormat", pref.time_format)
            pref.notify_message = data.get("notifyMessage", pref.notify_message)
            pref.notify_email = data.get("notifyEmail", pref.notify_email)
            pref.notify_desktop = data.get("notifyDesktop", pref.notify_desktop)
            pref.sound_enabled = data.get("soundEnabled", pref.sound_enabled)
            pref.default_page = data.get("defaultPage", pref.default_page)
            pref.page_size = data.get("pageSize", pref.page_size)
            pref.save()
            return DetailResponse(data=None, msg="Saved successfully")

    @action(methods=["GET", "POST", "DELETE"], detail=False, permission_classes=[IsAuthenticated])
    def favorites(self, request):
        """Get, add or delete current user favorites"""
        user = request.user
        if request.method == "GET":
            favorites = UserFavorite.objects.filter(user=user).order_by("-create_datetime")
            data = [{
                "id": fav.id,
                "title": fav.title,
                "category": fav.category,
                "url": fav.url,
                "description": fav.description,
                "create_datetime": fav.create_datetime.strftime("%Y-%m-%d %H:%M:%S") if fav.create_datetime else "",
            } for fav in favorites]
            return SuccessResponse(data=data, msg="Retrieved successfully")
        elif request.method == "POST":
            title = request.data.get("title")
            if not title:
                return ErrorResponse(msg="Title cannot be empty")
            fav = UserFavorite.objects.create(
                user=user,
                title=title,
                category=request.data.get("category"),
                url=request.data.get("url"),
                description=request.data.get("description"),
            )
            return DetailResponse(data={"id": fav.id}, msg="Added successfully")
        elif request.method == "DELETE":
            fav_id = request.data.get("id")
            if not fav_id:
                return ErrorResponse(msg="Favorite ID cannot be empty")
            try:
                fav = UserFavorite.objects.get(id=fav_id, user=user)
                fav.delete()
                return DetailResponse(data=None, msg="Deleted successfully")
            except UserFavorite.DoesNotExist:
                return ErrorResponse(msg="Favorite does not exist")

    @action(methods=["PUT"], detail=False, permission_classes=[IsAuthenticated])
    def change_password(self, request, *args, **kwargs):
        """Change password"""
        data = request.data
        old_pwd = data.get("oldPassword")
        print(old_pwd)
        new_pwd = data.get("newPassword")
        new_pwd2 = data.get("newPassword2")
        if old_pwd is None or new_pwd is None or new_pwd2 is None:
            return ErrorResponse(msg="Parameters cannot be empty")
        if new_pwd != new_pwd2:
            return ErrorResponse(msg="Passwords do not match")
        verify_password = check_password(old_pwd, request.user.password)
        if not verify_password:
            old_pwd_md5 = hashlib.md5(old_pwd.encode(encoding='UTF-8')).hexdigest()
            verify_password = check_password(str(old_pwd_md5), request.user.password)
        if verify_password:
            request.user.password = make_password(hashlib.md5(new_pwd.encode(encoding='UTF-8')).hexdigest())
            request.user.save()
            return DetailResponse(data=None, msg="Updated successfully")
        else:
            return ErrorResponse(msg="Old password is incorrect")

    @action(methods=["PUT"], detail=True, permission_classes=[IsAuthenticated])
    def reset_to_default_password(self, request,pk):
        """Restore default password"""
        if not self.request.user.is_superuser:
            return ErrorResponse(msg="Only super administrators can reset passwords")
        instance = Users.objects.filter(id=pk).first()
        if instance:
            default_password = dispatch.get_system_config_values("base.default_password")
            md5_pwd = hashlib.md5(default_password.encode(encoding='UTF-8')).hexdigest()
            instance.password = make_password(md5_pwd)
            instance.save()
            return DetailResponse(data=None, msg="Password reset successfully")
        else:
            return ErrorResponse(msg="User not found")

    @action(methods=["PUT"], detail=True)
    def reset_password(self, request, pk):
        """
        Reset password
        """
        if not self.request.user.is_superuser:
            return ErrorResponse(msg="Only super administrators can reset passwords")
        instance = Users.objects.filter(id=pk).first()
        data = request.data
        new_pwd = data.get("newPassword")
        new_pwd2 = data.get("newPassword2")
        if instance:
            if new_pwd != new_pwd2:
                return ErrorResponse(msg="Passwords do not match")
            else:
                instance.password = make_password(hashlib.md5(new_pwd.encode(encoding='UTF-8')).hexdigest())
                instance.save()
                return DetailResponse(data=None, msg="Updated successfully")
        else:
            return ErrorResponse(msg="User not found")

    def list(self, request, *args, **kwargs):
        dept_id = request.query_params.get('dept')
        show_all = request.query_params.get('show_all')
        if not dept_id:
            dept_id = ''
        if not show_all:
            show_all = 0
        if int(show_all):
            all_did = [dept_id]
            def inner(did):
                sub = Dept.objects.filter(parent_id=did)
                if not sub.exists():
                    return
                for i in sub:
                    all_did.append(i.pk)
                    inner(i)
            if dept_id != '':
                inner(dept_id)
                searchs = [
                    Q(**{f+'__icontains':i})
                    for f in self.search_fields
                ] if (i:=request.query_params.get('search')) else []
                q_obj = []
                if searchs:
                    q = searchs[0]
                    for i in searchs[1:]:
                        q |= i
                    q_obj.append(Q(q))
                queryset = Users.objects.filter(*q_obj, dept_id__in=all_did)
            else:
                queryset = self.filter_queryset(self.get_queryset())
        else:
            queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True, request=request)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True, request=request)
        return SuccessResponse(data=serializer.data, msg="Retrieved successfully")

    @action(methods=["GET"], detail=False, permission_classes=[IsAuthenticated])
    def user_basic_info(self, request):
        """
        Batch fetch user basic info by ID (id / username / name).
        Used by frontend user search to display selected user names, avoiding bare numeric IDs.
        Does not go through complex dept/super admin filters, only basic permission check (login required).
        Parameters: ids=1,2,3 or id=1&id=2&id=3
        """
        raw_ids = request.query_params.getlist('id')
        ids_str = request.query_params.get('ids') or ''
        if not raw_ids and ids_str:
            raw_ids = [x for x in str(ids_str).split(',') if x.strip()]
        cleaned_ids = []
        for x in raw_ids:
            try:
                cleaned_ids.append(int(str(x).strip()))
            except (TypeError, ValueError):
                continue
        if not cleaned_ids:
            return SuccessResponse(data=[], msg="ok")
        cleaned_ids = list({x for x in cleaned_ids if x > 0})
        rows = (
            Users.objects.filter(id__in=cleaned_ids)
            .values('id', 'username', 'name')
        )
        id_map = {str(r['id']): r for r in rows}
        result = []
        for uid in cleaned_ids:
            found = id_map.get(str(uid))
            if found:
                result.append({
                    'id': found['id'],
                    'username': found['username'] or f'ID:{found["id"]}',
                    'name': found['name'] or '',
                })
            else:
                result.append({
                    'id': uid,
                    'username': f'ID:{uid}',
                    'name': '',
                })
        return DetailResponse(data=result, msg="Retrieved successfully")