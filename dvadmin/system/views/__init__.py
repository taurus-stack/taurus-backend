from dvadmin.system.views.user import UserViewSet
from dvadmin.system.views.role import RoleViewSet
from dvadmin.system.views.menu import MenuViewSet
from dvadmin.system.views.menu_button import MenuButtonViewSet
from dvadmin.system.views.dept import DeptViewSet
from dvadmin.system.views.dictionary import DictionaryViewSet, InitDictionaryViewSet, HostTypeDictionaryViewSet
from dvadmin.system.views.area import AreaViewSet
from dvadmin.system.views.file_list import FileViewSet
from dvadmin.system.views.operation_log import OperationLogViewSet
from dvadmin.system.views.login_log import LoginLogViewSet
from dvadmin.system.views.system_config import SystemConfigViewSet, InitSettingsViewSet
from dvadmin.system.views.api_white_list import ApiWhiteListViewSet
from dvadmin.system.views.role_menu import RoleMenuPermissionViewSet
from dvadmin.system.views.role_menu_button_permission import RoleMenuButtonPermissionViewSet
from dvadmin.system.views.menu_field import MenuFieldViewSet
from dvadmin.system.views.bootstrap import BootstrapViewSet
from dvadmin.system.views.login import LoginView, CaptchaView, LoginTokenView, LogoutView, ApiLogin
from dvadmin.system.views.message_center import MessageCenterViewSet
from dvadmin.system.views.permission_code import PermissionCodeViewSet, RolePermissionViewSet
from dvadmin.system.views.clause import PrivacyView, TermsServiceView