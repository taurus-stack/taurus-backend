from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("taurus", "0009_workflow_custom_approver_ids"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflow",
            name="global_timeout_sec",
            field=models.IntegerField(
                default=3600,
                help_text="对有超时需求的节点类型（命令/脚本/HTTP/审批等）生效，节点单独配置了超时时以节点配置为准",
                verbose_name="节点默认超时秒数（0=不设置默认值）",
            ),
        ),
    ]
