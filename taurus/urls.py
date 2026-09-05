from django.urls import path, include
from rest_framework.routers import SimpleRouter
from taurus.views import (
    WorkflowViewSet, WorkflowCategoryViewSet, WorkflowExecutionViewSet,
    WorkflowApproveViewSet,
    WorkflowApprovalRuleViewSet, WorkflowApprovalRuleNodeViewSet, WorkflowApprovalInstanceViewSet,
    ScheduleViewSet,
    ScheduleExecutionViewSet, HostViewSet,
    RegistrationTokenViewSet, HostHeartbeatViewSet, HeartbeatServerViewSet,
    ProgramInstallConfigViewSet, ProgramInstallPolicyViewSet, ProgramCommandViewSet, SupervisorViewSet,
    HostLogViewSet, ManagedProgramViewSet, LogCommandViewSet,
    ProgramInstallTemplateViewSet, ProgramHostBindingViewSet,
    OpsViewSet, OpsExecutionViewSet, OpsExecutionApprovalViewSet,
    ScriptCategoryViewSet, ScriptViewSet, ScriptVersionViewSet, ScriptPermissionViewSet,
    ScriptTaskViewSet, ScriptTaskExecutionViewSet, ScriptAuditViewSet, ScriptApproveViewSet,
    ScriptApprovalRuleViewSet, ScriptApprovalRuleNodeViewSet, ScriptApprovalInstanceViewSet,
    ScriptCheckRuleViewSet,
    SharePermissionDefViewSet, ShareLinkViewSet,
    TaskCenterViewSet,
    ContactLeadViewSet,
    LightweightUserOptionsView,
    workflow_callback_view,
    _WFAPP_EE_OK, _SCRIPT_EE_OK,
)
from taurus.views_edition import EditionInfoView, EditionFeaturesView, EditionDescribeView

router = SimpleRouter()

# Workflow management
router.register('workflow', WorkflowViewSet, basename='workflow')
router.register('workflow-category', WorkflowCategoryViewSet, basename='workflow-category')
router.register('workflow-execution', WorkflowExecutionViewSet, basename='workflow-execution')
if _WFAPP_EE_OK:
    router.register('workflow-approve', WorkflowApproveViewSet, basename='workflow-approve')
    router.register('workflow-approval-rule', WorkflowApprovalRuleViewSet, basename='workflow-approval-rule')
    router.register('workflow-approval-node', WorkflowApprovalRuleNodeViewSet, basename='workflow-approval-node')
    router.register('workflow-approval-instance', WorkflowApprovalInstanceViewSet, basename='workflow-approval-instance')

# Scheduled task management
router.register('schedule', ScheduleViewSet, basename='schedule')
router.register('schedule-execution', ScheduleExecutionViewSet, basename='schedule-execution')

# Host management
router.register('host', HostViewSet, basename='host')

# Supervisor management (Register, Heartbeat, Download, etc., no auth required)
router.register('supervisor', SupervisorViewSet, basename='supervisor')

# Registration token management
router.register('registration-token', RegistrationTokenViewSet, basename='registration-token')

# Heartbeat record management
router.register('heartbeat', HostHeartbeatViewSet, basename='heartbeat')

# Heartbeat service management
router.register('heartbeat-server', HeartbeatServerViewSet, basename='heartbeat-server')

# Program install template management
router.register('program-install-template', ProgramInstallTemplateViewSet, basename='program-install-template')

# Host program binding management
router.register('program-host-binding', ProgramHostBindingViewSet, basename='program-host-binding')

# Program install config management
router.register('program-install-config', ProgramInstallConfigViewSet, basename='program-install-config')

# Program install policy management
router.register('program-install-policy', ProgramInstallPolicyViewSet, basename='program-install-policy')

# Program command management
router.register('program-command', ProgramCommandViewSet, basename='program-command')

# Host log management
router.register('host-log', HostLogViewSet, basename='host-log')

# Managed program management
router.register('managed-program', ManagedProgramViewSet, basename='managed-program')

# Log collection command management
router.register('log-command', LogCommandViewSet, basename='log-command')

# Ops center (command execution, script execution, file upload)
router.register('ops', OpsViewSet, basename='ops')

# Ops execution records
router.register('ops-execution', OpsExecutionViewSet, basename='ops-execution')

# Ops execution task approval
router.register('ops-execution-approval', OpsExecutionApprovalViewSet, basename='ops-execution-approval')

# Script library management
# Script category directory
router.register('script-category', ScriptCategoryViewSet, basename='script-category')

router.register('script', ScriptViewSet, basename='script')

# Script version management
router.register('script-version', ScriptVersionViewSet, basename='script-version')

# Script permission config management
router.register('script-permission', ScriptPermissionViewSet, basename='script-permission')

# Script scheduled task management
router.register('script-task', ScriptTaskViewSet, basename='script-task')
router.register('script-task-execution', ScriptTaskExecutionViewSet, basename='script-task-execution')

# Script audit log management
router.register('script-audit', ScriptAuditViewSet, basename='script-audit')

if _SCRIPT_EE_OK:
    # Script approval management
    router.register('script-approve', ScriptApproveViewSet, basename='script-approve')
    # Script approval rules (rule-driven approval)
    router.register('script-approval-rule', ScriptApprovalRuleViewSet, basename='script-approval-rule')
    router.register('script-approval-node', ScriptApprovalRuleNodeViewSet, basename='script-approval-node')
    router.register('script-approval-instance', ScriptApprovalInstanceViewSet, basename='script-approval-instance')
    # Script check rules
    router.register('script-check-rule', ScriptCheckRuleViewSet, basename='script-check-rule')
    # Sharing: permission dictionary & share link
    router.register('share-perm-def', SharePermissionDefViewSet, basename='share-perm-def')
    router.register('share-link', ShareLinkViewSet, basename='share-link')

# Task center (aggregate script scheduled tasks + scheduled workflows)
router.register('task-center', TaskCenterViewSet, basename='task-center')

# Contact lead (taurus-portal public contact form submissions)
router.register('contact-lead', ContactLeadViewSet, basename='contact-lead')

urlpatterns = [
    path('', include(router.urls)),
    # HTTP callback endpoint (external system async drives workflow resume)
    path('workflow/callback/<str:token>/', workflow_callback_view, name='workflow-callback'),
    # Program install template custom action
    path('program-install-template/<int:pk>/apply_to_hosts/', ProgramInstallTemplateViewSet.as_view({'post': 'apply_to_hosts'})),
    # Host program binding custom actions
    path('program-host-binding/<int:pk>/install/', ProgramHostBindingViewSet.as_view({'post': 'install'})),
    path('program-host-binding/<int:pk>/uninstall/', ProgramHostBindingViewSet.as_view({'post': 'uninstall'})),
    # Program install config custom actions
    path('program-install-config/batch_create/', ProgramInstallConfigViewSet.as_view({'post': 'batch_create'})),
    path('program-install-config/<int:pk>/redispatch/', ProgramInstallConfigViewSet.as_view({'post': 'redispatch'})),
    # Program install policy custom actions
    path('program-install-policy/<int:pk>/apply/', ProgramInstallPolicyViewSet.as_view({'post': 'apply'})),
    path('program-install-policy/<int:pk>/preview_hosts/', ProgramInstallPolicyViewSet.as_view({'get': 'preview_hosts'})),
    path('program-install-policy/<int:pk>/upgrade_version/', ProgramInstallPolicyViewSet.as_view({'post': 'upgrade_version'})),
    # Program command custom actions
    path('program-command/batch_create/', ProgramCommandViewSet.as_view({'post': 'batch_create'})),
    # Managed program custom actions
    path('managed-program/<int:pk>/start/', ManagedProgramViewSet.as_view({'post': 'start'})),
    path('managed-program/<int:pk>/stop/', ManagedProgramViewSet.as_view({'post': 'stop'})),
    path('managed-program/<int:pk>/restart/', ManagedProgramViewSet.as_view({'post': 'restart'})),
    path('managed-program/<int:pk>/remove/', ManagedProgramViewSet.as_view({'post': 'remove'})),
    # Script library custom actions
    path('script/<int:pk>/toggle-status/', ScriptViewSet.as_view({'post': 'toggle_status'})),
    path('script/<int:pk>/copy/', ScriptViewSet.as_view({'post': 'copy_script'})),
    path('script/stats/', ScriptViewSet.as_view({'get': 'get_stats'})),
    path('script/my_script_info/', ScriptViewSet.as_view({'get': 'my_script_info'})),
    path('script/categories/', ScriptViewSet.as_view({'get': 'get_categories'})),
    path('script/<int:pk>/rollback/', ScriptViewSet.as_view({'post': 'rollback_version'})),
    # Script share permission related
    path('script/<int:pk>/shares/', ScriptViewSet.as_view({'get': 'shares', 'post': 'shares'})),
    path('script/<int:pk>/shares/<int:share_id>/', ScriptViewSet.as_view({'put': 'share_detail', 'delete': 'share_detail'})),
    path('script/<int:pk>/effective-perms/', ScriptViewSet.as_view({'get': 'effective_perms'})),
    # Script scheduled task custom actions
    path('script-task/<int:pk>/toggle/', ScriptTaskViewSet.as_view({'post': 'toggle_enabled'})),
]

if _SCRIPT_EE_OK:
    urlpatterns += [
        # Script approval custom actions
        path('script-approve/<int:pk>/approve/', ScriptApproveViewSet.as_view({'post': 'approve'})),
        path('script-approve/<int:pk>/reject/', ScriptApproveViewSet.as_view({'post': 'reject'})),
    ]

urlpatterns += [
    # Workflow orchestration custom actions
    path('workflow/<int:pk>/toggle-status/', WorkflowViewSet.as_view({'post': 'toggle_status'})),
    path('workflow/<int:pk>/copy/', WorkflowViewSet.as_view({'post': 'copy_workflow'})),
    path('workflow/<int:pk>/submit-approve/', WorkflowViewSet.as_view({'post': 'submit_approve'})),
    path('workflow/<int:pk>/risk-assessment/', WorkflowViewSet.as_view({'get': 'risk_assessment', 'post': 'risk_assessment'})),
    path('workflow/stats/', WorkflowViewSet.as_view({'get': 'get_stats'})),
    # Workflow share permission related (community 版可用)
    path('workflow/<int:pk>/shares/', WorkflowViewSet.as_view({'get': 'shares', 'post': 'shares'})),
    path('workflow/<int:pk>/shares/<int:share_id>/', WorkflowViewSet.as_view({'put': 'share_detail', 'delete': 'share_detail'})),
    path('workflow/<int:pk>/effective-perms/', WorkflowViewSet.as_view({'get': 'effective_perms'})),
]

if _WFAPP_EE_OK:
    urlpatterns += [
        # Workflow orchestration approval custom actions
        path('workflow-approve/<int:pk>/approve/', WorkflowApproveViewSet.as_view({'post': 'approve'})),
        path('workflow-approve/<int:pk>/reject/', WorkflowApproveViewSet.as_view({'post': 'reject'})),
    ]

urlpatterns += [
    # -------- Edition Gate (M1)：edition 能力查询 --------
    path('edition/info/', EditionInfoView.as_view()),
    path('edition/features/', EditionFeaturesView.as_view()),
    path('edition/describe/', EditionDescribeView.as_view()),
    # -------- Lightweight user picker (M3：工作流审批人 / 通知人选择器不需要 user:list 权限) --------
    path('user-options/', LightweightUserOptionsView.as_view()),
]