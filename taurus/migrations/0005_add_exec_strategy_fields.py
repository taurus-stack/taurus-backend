from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("taurus", "0004_rename_taurus_ops_status_idx_taurus_ops__status_2a3453_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="opsexecutionapproval",
            name="exec_mode",
            field=models.CharField(default="parallel", max_length=20, verbose_name="执行模式"),
        ),
        migrations.AddField(
            model_name="opsexecutionapproval",
            name="concurrency",
            field=models.IntegerField(default=10, verbose_name="并发数"),
        ),
        migrations.AddField(
            model_name="opsexecutionapproval",
            name="target_hosts_count",
            field=models.IntegerField(default=1, verbose_name="目标主机数"),
        ),
    ]