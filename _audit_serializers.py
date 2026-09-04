"""Audit all Thin Wrapper Serializers for Meta.model=None bug when EE is absent."""
import os, sys, traceback
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'application.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import django
django.setup()

from taurus import serializers as ser_module
import inspect

# Grep: all classes defined in taurus.serializers that have a Meta inner class
# We will check Meta.model and flag those with None (or missing)

SERIALIZERS_TO_CHECK = [
    # M2.2 Workflow DAG
    'WorkflowDAGVersionSerializer',
    # M2.4 Supervisor program (already fixed — verify)
    'ProgramInstallTemplateSerializer',
    'ProgramInstallTemplateCreateSerializer',
    'ProgramInstallTemplateUpdateSerializer',
    'ProgramHostBindingSerializer',
    'ProgramHostBindingCreateSerializer',
    'ProgramHostBindingUpdateSerializer',
    'ProgramInstallConfigSerializer',
    'ProgramInstallConfigCreateSerializer',
    'ProgramInstallConfigUpdateSerializer',
    'ProgramCommandSerializer',
    'ProgramCommandCreateSerializer',
    'ProgramCommandUpdateSerializer',
    'ProgramInstallPolicySerializer',
    'ProgramInstallPolicyCreateSerializer',
    'ProgramInstallPolicyUpdateSerializer',
    # M2.5 HostLog / LogCommand
    'HostLogSerializer',
    'HostLogReceiveSerializer',
    'LogCommandSerializer',
    'LogCommandCreateSerializer',
    'LogCommandUpdateSerializer',
    # Ops approval
    'OpsExecutionApprovalSerializer',
    'OpsExecutionApprovalListSerializer',
    'OpsExecutionApprovalCreateSerializer',
    'OpsExecutionApprovalActionSerializer',
    # Script library EE
    'ScriptAuditSerializer',
    'ScriptApproveSerializer',
    'ScriptApprovalRuleSerializer',
    'ScriptApprovalNodeSerializer',
    'ScriptApprovalInstanceSerializer',
    'ScriptApprovalNodeExecutionSerializer',
    'ScriptCheckRuleSerializer',
    # Share permission
    'SharePermissionDefSerializer',
    'ScriptSharePermissionSerializer',
    'WorkflowSharePermissionSerializer',
    'SharePermissionBatchCreateSerializer',
    'ShareLinkSerializer',
    'ShareLinkActivateSerializer',
    'ShareLinkAccessLogSerializer',
    # Workflow approval
    'WorkflowApproveSerializer',
    'WorkflowApproveCreateSerializer',
    'WorkflowApproveApproveSerializer',
    'WorkflowApproveRejectSerializer',
    'WorkflowApproveCompatSerializer',
    'WorkflowApprovalRuleSerializer',
    'WorkflowApprovalNodeSerializer',
    'WorkflowApprovalInstanceSerializer',
    'WorkflowApprovalNodeExecutionSerializer',
    # Scheduler HA
    'ScheduleExecutionHASerializer',
    'ScriptTaskExecutionUnifiedSerializer',
    'SchedulerAlertRuleSerializer',
    'UnifiedScheduleListRequestSerializer',
    'UnifiedScheduleStatsSerializer',
    'SchedulerAlertEventSerializer',
    # Extension center placeholders
    'ContactLeadSerializer',
    'TaskCenterItemSerializer',
    'KnowledgeBasePlaceholderSerializer',
    'InspectionCenterPlaceholderSerializer',
    'ToolsCenterPlaceholderSerializer',
    'BackupRestoreCenterPlaceholderSerializer',
    'DownloadCenterPlaceholderSerializer',
]

problems = []
oks = []
for name in SERIALIZERS_TO_CHECK:
    cls = getattr(ser_module, name, None)
    if cls is None:
        problems.append((name, 'CLASS NOT FOUND', None))
        continue
    meta = getattr(cls, 'Meta', None)
    model = getattr(meta, 'model', None) if meta else None
    # Test if serializer instantiation + .fields access works (this triggers DRF get_field_info)
    try:
        inst = cls()
        _ = inst.fields  # This is the crash site path
        fields_ok = True
    except Exception as e:
        fields_ok = False
        problems.append((name, f'fields lookup ERROR: {type(e).__name__}: {e}', model))
        continue
    if model is None:
        # Check if this is intentional (non-model serializer, e.g. serializers.Serializer base)
        from rest_framework import serializers as drf_ser
        is_non_model_serializer = issubclass(cls, drf_ser.Serializer) and not issubclass(cls, drf_ser.ModelSerializer)
        if is_non_model_serializer:
            oks.append((name, f'OK (non-model Serializer)', model))
        else:
            problems.append((name, 'Meta.model = None (but ModelSerializer subclass)', model))
    else:
        oks.append((name, f'OK model={model.__name__}', model))

print('='*90)
print(f'CHECKED {len(SERIALIZERS_TO_CHECK)} SERIALIZERS — {len(problems)} PROBLEMS, {len(oks)} OK')
print('='*90)
if problems:
    print('\n[PROBLEMS — likely cause HTTP 400/500 when ViewSet has no EE gate]:')
    for p in problems:
        print(f'  ⚠️  {p[0]:<55s} | {p[1]} | model={p[2]}')
print()
print('[OK]:')
for o in oks:
    print(f'  ✅ {o[0]:<55s} | {o[1]}')