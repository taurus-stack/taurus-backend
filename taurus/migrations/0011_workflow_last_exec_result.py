"""新增 Workflow.last_exec_result 和 next_exec_time 字段

Revision ID: 0011
Created at: 2025-07-10

"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('taurus', '0010_workflow_global_timeout_sec'),
    ]

    operations = [
        migrations.AddField(
            model_name='workflow',
            name='last_exec_result',
            field=models.CharField(blank=True, max_length=20, null=True, verbose_name='最后执行结果'),
        ),
        migrations.AddField(
            model_name='workflow',
            name='next_exec_time',
            field=models.DateTimeField(blank=True, null=True, verbose_name='下次执行时间'),
        ),
    ]
