"""
Scheduled task execution service
"""
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task
def execute_schedule_task(schedule_id):
    """
    Execute scheduled task
    """
    from taurus.models import Schedule, ScheduleExecution
    
    try:
        schedule = Schedule.objects.get(id=schedule_id)
        
        # Create execution record
        execution = ScheduleExecution.objects.create(
            schedule=schedule,
            status=1,  # Running
            start_time=timezone.now()
        )
        
        # Update last execution time
        schedule.last_run_time = timezone.now()
        schedule.save()
        
        logger.info(f"Starting scheduled task: {schedule.name}, type: {schedule.target_type}")
        
        if schedule.target_type == 'script':
            # Execute single script
            result = _execute_script(schedule, execution)
        elif schedule.target_type == 'workflow':
            # Execute workflow
            result = _execute_workflow(schedule, execution)
        else:
            raise Exception(f"Unknown target type: {schedule.target_type}")
        
        # Update execution record
        execution.status = 2  # Completed
        execution.end_time = timezone.now()
        execution.result = result
        execution.save()
        
        logger.info(f"Scheduled task completed: {schedule.name}")
        return result
        
    except Exception as e:
        logger.error(f"Scheduled task failed: {schedule.name}, error: {str(e)}", exc_info=True)
        
        # Update execution record
        if 'execution' in locals():
            execution.status = 3  # Failed
            execution.end_time = timezone.now()
            execution.error_message = str(e)
            execution.save()
        
        raise


def _execute_script(schedule, execution):
    """
    Execute single script
    """
    from taurus.models import Record, RecordDetail
    
    template = schedule.template
    hosts = schedule.hosts.all()
    
    if not template:
        raise Exception("Script template does not exist")
    
    if not hosts:
        raise Exception("No target hosts selected")
    
    results = []
    
    for host in hosts:
        try:
            # Create execution record
            record = Record.objects.create(
                uuid=f"schedule_{schedule.id}_{execution.id}_{host.id}",
                host=host,
                template=template,
                script_type=template.script_type,
                script_content=template.script_content,
                envs=str(schedule.envs) if schedule.envs else template.envs,
                args=str(schedule.args) if schedule.args else template.args,
                timeout=template.timeout,
                status=1,  # Running
                start_datetime=timezone.now()
            )
            
            # TODO: Call client to execute script via gRPC
            # Actual script execution logic to be implemented here
            # Temporarily marked as pending implementation
            result = {
                'stdout': '',
                'stderr': 'Script execution feature not yet implemented',
                'return_code': -1
            }
            
            # Save execution result
            RecordDetail.objects.create(
                record=record,
                seq=1,
                stdin='',
                stdout=result.get('stdout', ''),
                stderr=result.get('stderr', '')
            )
            
            # Update record status
            record.status = 3  # Failed (pending implementation)
            record.end_datetime = timezone.now()
            record.return_code = result.get('return_code', -1)
            record.save()
            
            results.append({
                'host_id': host.id,
                'host_ip': host.host_ip,
                'status': record.status,
                'return_code': result.get('return_code', -1)
            })
            
        except Exception as e:
            logger.error(f"Script execution failed on host {host.host_ip}: {str(e)}")
            results.append({
                'host_id': host.id,
                'host_ip': host.host_ip,
                'status': 3,
                'error': str(e)
            })
    
    return {'hosts': results}


def _execute_workflow(schedule, execution):
    from taurus.workflow.engine.runner import WorkflowRunner, WorkflowRunnerError

    workflow = schedule.workflow

    if not workflow:
        raise Exception("Workflow does not exist")

    if workflow.workflow_mode == 'dag':
        runner = WorkflowRunner()
        dag_version = schedule.dag_version or None
        try:
            trigger_result = runner.trigger_workflow(
                workflow,
                dag_version=dag_version,
                trigger_params=schedule.envs or {},
                trigger_type='schedule',
                user_id=schedule.creator_id if hasattr(schedule, 'creator_id') else None,
            )
        except WorkflowRunnerError as exc:
            raise Exception(str(exc)) from exc

        return {
            'status': 'running',
            'mode': 'dag',
            'workflow_execution_id': trigger_result.execution_id,
            'dag_version_id': trigger_result.dag_version_id,
            'initial_runnables': trigger_result.initial_runnables,
        }
    else:
        from taurus.models import WorkflowExecution, WorkflowStepExecution

        hosts = schedule.hosts.all()
        if not hosts:
            raise Exception("Linear workflow has no target hosts selected")

        workflow_execution = WorkflowExecution.objects.create(
            workflow=workflow,
            status=1,
            start_time=timezone.now(),
            context={'schedule_id': schedule.id},
            trigger_type='schedule',
        )

        steps = workflow.steps.all().order_by('step_order')
        for host in hosts:
            for step in steps:
                WorkflowStepExecution.objects.create(
                    execution=workflow_execution,
                    step=step,
                    host=host,
                    status=0,
                )

        # Synchronously update workflow execution count and last execution time
        workflow.exec_count = (workflow.exec_count or 0) + 1
        workflow.last_exec_time = timezone.now()
        workflow.save(update_fields=['exec_count', 'last_exec_time', 'update_datetime'])

        return {
            'status': 'running',
            'mode': 'linear',
            'workflow_execution_id': workflow_execution.pk,
        }


@shared_task
def check_host_heartbeat_timeout():
    """
    Check host heartbeat timeout, mark timed-out hosts as offline
    
    Timeout threshold: 90 seconds (3 heartbeat cycles)
    Executes every 60 seconds
    """
    from taurus.models import Host
    
    threshold = timezone.now() - timezone.timedelta(seconds=90)
    
    # Find online hosts whose last heartbeat was over 90 seconds ago
    offline_hosts = Host.objects.filter(
        online_status=1,  # Currently online
        last_heartbeat_at__lt=threshold,  # Last heartbeat over 90 seconds ago
        status=1  # Approved host
    )
    
    count = offline_hosts.count()
    
    if count > 0:
        # Batch update to offline status
        offline_hosts.update(online_status=0)
        
        host_list = offline_hosts.values_list('host_name', 'host_ip')
        logger.warning(f"Detected {count} hosts offline due to timeout: {list(host_list)}")
    
    return {'offline_count': count}


@shared_task
def cleanup_old_heartbeat_records(days=30):
    """
    Clean up old heartbeat records, keep recent N days of data
    
    Default keeps 30 days
    Recommended to execute once daily
    """
    from taurus.models import HostHeartbeat
    
    threshold = timezone.now() - timezone.timedelta(days=days)
    
    old_records = HostHeartbeat.objects.filter(timestamp__lt=threshold)
    count = old_records.count()
    
    if count > 0:
        old_records.delete()
        logger.info(f"Cleaned up {count} old heartbeat records (older than {days} days)")
    
    return {'deleted_count': count}