"""
M3.1 — 给 dvadmin 的 Menu 表加 requires_feature 字段。

该字段存一个逗号分隔的 FeatureCode 列表，前端路由初始化时
backEnd.ts._filterTree() 会读取它做菜单级 Edition 过滤。

跨 app migration：操作的是 dvadmin.system_menu 表，但放在 taurus
migration 目录里，这样 Open Core 对 dvadmin 第三方模块零侵入。
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("taurus", "0015_contact_lead"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE taurus_system_menu "
                        "ADD COLUMN requires_feature varchar(255) NULL "
                        "COMMENT 'M3.1 Edition Gate: comma-separated FeatureCode; display if any matches'"
                    ),
                    reverse_sql="ALTER TABLE taurus_system_menu DROP COLUMN requires_feature;",
                ),
            ],
            state_operations=[
                # 状态层假操作：dvadmin 的 Menu model 没这个 field，
                # 我们不打算改 dvadmin 源码，所以 state_operations 空着
                # 实际 serializer 层（WebRouterSerializer）会手动加这个输出
            ],
        ),
    ]
