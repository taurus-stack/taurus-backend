#!/usr/bin/env python3
"""
修复 SQL dump file中超过 MySQL 64 字符限制的标识符
"""
import re
from pathlib import Path

# 超长标识符map(原Name -> 缩短后的Name)
LONG_IDENTIFIERS = {
    # taurus_inventory_created_by_id_b2b94921_fk_taurus_system_users_id (71 chars)
    'taurus_inventory_created_by_id_b2b94921_fk_taurus_system_users_id': 
        'taurus_inventory_created_by_id_b2b94921_fk_taurus_sys_users_id',  # 64 chars
    
    # taurus_role_menu_button_perm_rolemenubuttonpermission_id_07ce4d48 (66 chars)
    'taurus_role_menu_button_perm_rolemenubuttonpermission_id_07ce4d48':
        'taurus_role_menu_btn_perm_rolemenubuttonperm_id_07ce4d48',  # 60 chars
    
    # taurus_schedule_template_id_e45804c5_fk_taurus_script_template_id (68 chars)
    'taurus_schedule_template_id_e45804c5_fk_taurus_script_template_id':
        'taurus_schedule_template_id_e45804c5_fk_taurus_script_tmpl_id',  # 63 chars
    
    # taurus_workflow_step_execution_host_id_b7778450_fk_taurus_host_id (67 chars)
    'taurus_workflow_step_execution_host_id_b7778450_fk_taurus_host_id':
        'taurus_workflow_step_exec_host_id_b7778450_fk_taurus_host_id',  # 64 chars
}

def fix_sql_file(filepath):
    """修复单  SQL file中的超长标识符"""
    print(f"处理文件: {filepath}")
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    changes = []
    
    for old_name, new_name in LONG_IDENTIFIERS.items():
        if old_name in content:
            count = content.count(old_name)
            content = content.replace(old_name, new_name)
            changes.append(f"  {old_name} ({len(old_name)}字符) -> {new_name} ({len(new_name)}字符) [替换 {count} 处]")
    
    if changes:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print('\n'.join(changes))
        print(f"✓ 已修复\n")
    else:
        print("✓ 无需修复\n")

def main():
    db_dir = Path(__file__).parent
    sql_files = list(db_dir.glob('taurus_backend.dump*.sql'))
    
    if not sql_files:
        print("未找到 taurus_backend.dump*.sql 文件")
        return
    
    print(f"找到 {len(sql_files)} 个 SQL 文件\n")
    
    for sql_file in sql_files:
        fix_sql_file(sql_file)
    
    print("所有文件处理完成！")

if __name__ == '__main__':
    main()
