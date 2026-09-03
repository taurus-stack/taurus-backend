from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('taurus', '0002_alter_use_shell_default_true'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OpsExecutionApproval',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('create_datetime', models.DateTimeField(auto_now_add=True, null=True, verbose_name='创建时间')),
                ('update_datetime', models.DateTimeField(auto_now=True, null=True, verbose_name='更新时间')),
                ('creator', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='创建人')),
                ('modifier', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='修改人')),
                ('creator_name', models.CharField(blank=True, null=True, max_length=100, verbose_name='创建人名称')),
                ('modifier_name', models.CharField(blank=True, null=True, max_length=100, verbose_name='修改人名称')),
                ('description', models.CharField(blank=True, default='', max_length=255, verbose_name='描述')),
                ('batch_id', models.CharField(blank=True, max_length=64, null=True, verbose_name='批次ID')),
                ('status', models.CharField(choices=[('pending', '待审批'), ('approved', '已通过'), ('rejected', '已驳回'), ('cancelled', '已撤回'), ('executing', '执行中'), ('done', '已完成'), ('failed', '执行失败')], default='pending', max_length=20, verbose_name='审批状态')),
                ('submitter_name', models.CharField(blank=True, max_length=100, null=True, verbose_name='提交人名称')),
                ('submit_desc', models.TextField(blank=True, null=True, verbose_name='提交说明')),
                ('approver_name', models.CharField(blank=True, max_length=100, null=True, verbose_name='审批人名称')),
                ('approve_reason', models.TextField(blank=True, null=True, verbose_name='审批意见')),
                ('approve_time', models.DateTimeField(blank=True, null=True, verbose_name='审批时间')),
                ('finish_time', models.DateTimeField(blank=True, null=True, verbose_name='完成时间')),
                ('script_type', models.CharField(blank=True, max_length=20, null=True, verbose_name='脚本类型')),
                ('script_content', models.TextField(blank=True, null=True, verbose_name='脚本内容')),
                ('args', models.JSONField(blank=True, default=list, verbose_name='参数')),
                ('working_directory', models.CharField(blank=True, max_length=500, null=True, verbose_name='工作目录')),
                ('timeout_seconds', models.IntegerField(default=300, verbose_name='超时时间(秒)')),
                ('environment', models.JSONField(blank=True, default=dict, verbose_name='环境变量')),
                ('merge_streams', models.BooleanField(default=False, verbose_name='合并输出流')),
                ('load_profile', models.CharField(default='false', max_length=10, verbose_name='环境加载模式')),
                ('privileged', models.BooleanField(default=False, verbose_name='特权执行')),
                ('su_user', models.CharField(blank=True, max_length=64, null=True, verbose_name='su目标用户')),
                ('host', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exec_approvals', to='taurus.host', verbose_name='目标主机')),
                ('submitter', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='submitted_exec_approvals', to=settings.AUTH_USER_MODEL, verbose_name='提交人')),
                ('approver', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_exec_approvals', to=settings.AUTH_USER_MODEL, verbose_name='审批人')),
                ('ops_execution', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approval_records', to='taurus.opsexecution', verbose_name='关联执行记录')),
            ],
            options={
                'verbose_name': '执行任务审批',
                'verbose_name_plural': '执行任务审批',
                'ordering': ['-create_datetime'],
                'db_table': settings.TABLE_PREFIX + 'ops_execution_approval',
            },
        ),
        migrations.AddIndex(
            model_name='opsexecutionapproval',
            index=models.Index(fields=['status'], name='taurus_ops_status_idx'),
        ),
        migrations.AddIndex(
            model_name='opsexecutionapproval',
            index=models.Index(fields=['submitter'], name='taurus_ops_submit_idx'),
        ),
        migrations.AddIndex(
            model_name='opsexecutionapproval',
            index=models.Index(fields=['batch_id'], name='taurus_ops_batch_idx'),
        ),
    ]