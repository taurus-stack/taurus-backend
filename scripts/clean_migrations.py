#!/usr/bin/env python3
"""
cleanup Django migrations MigrationfileScript

functionality:
  1. 扫描项目下所有 migrations directory
  2. DeleteMigrationfile(keep __init__.py)
  3. cleanup __pycache__
  4. Optional:Delete后re-Generate 0001_initial.py(--reset)

用法:
  # Preview模式(不实际Delete, 只列出将要cleanup的file)
  python scripts/clean_migrations.py --dry-run

  # cleanup所有Migrationfile
  python scripts/clean_migrations.py

  # cleanup后re-Generate初始Migration
  python scripts/clean_migrations.py --reset

  # 只cleanup指定 app
  python scripts/clean_migrations.py --app taurus

  # cleanup并Update数据library(Delete django_migrations record, --reset 时use)
  python scripts/clean_migrations.py --reset --flush-db
"""

import argparse
import os
import shutil
import subprocess
import sys


def find_migrations_dirs(base_dir: str, app_filter: str | None = None) -> list[str]:
    """扫描 base_dir 下所有 migrations directory"""
    result = []
    for root, dirs, _files in os.walk(base_dir):
        if "migrations" in dirs:
            mig_dir = os.path.join(root, "migrations")
            if app_filter:
                app_name = os.path.basename(root)
                if app_name != app_filter:
                    continue
            result.append(mig_dir)
    return sorted(result)


def collect_deletable_files(mig_dir: str) -> list[str]:
    """收集可Delete的Migrationfile(排除 __init__.py)"""
    result = []
    for fname in sorted(os.listdir(mig_dir)):
        fpath = os.path.join(mig_dir, fname)
        if fname == "__init__.py":
            continue
        if fname == "__pycache__":
            continue
        if fname.endswith(".py") or fname.endswith(".pyc"):
            result.append(fpath)
    return result


def clean_migrations(
    base_dir: str,
    app_filter: str | None = None,
    dry_run: bool = False,
    reset: bool = False,
    flush_db: bool = False,
) -> None:
    mig_dirs = find_migrations_dirs(base_dir, app_filter)

    if not mig_dirs:
        print("未找到任何 migrations 目录")
        return

    total_files = 0
    total_pycache = 0

    for mig_dir in mig_dirs:
        rel_dir = os.path.relpath(mig_dir, base_dir)
        app_name = os.path.basename(os.path.dirname(mig_dir))
        files = collect_deletable_files(mig_dir)

        if not files:
            pycache = os.path.join(mig_dir, "__pycache__")
            if os.path.isdir(pycache):
                if dry_run:
                    print(f"[预览] 删除目录: {rel_dir}/__pycache__")
                    total_pycache += 1
                else:
                    shutil.rmtree(pycache)
                    print(f"[删除] 目录: {rel_dir}/__pycache__")
                    total_pycache += 1
            continue

        print(f"\n{'[预览]' if dry_run else '[清理]'} {rel_dir} ({len(files)} 个文件):")
        for fpath in files:
            rel_path = os.path.relpath(fpath, base_dir)
            if dry_run:
                print(f"  [预览] {rel_path}")
            else:
                os.remove(fpath)
                print(f"  [删除] {rel_path}")
            total_files += 1

        pycache = os.path.join(mig_dir, "__pycache__")
        if os.path.isdir(pycache):
            if dry_run:
                print(f"  [预览] {rel_dir}/__pycache__/")
                total_pycache += 1
            else:
                shutil.rmtree(pycache)
                print(f"  [删除] {rel_dir}/__pycache__/")
                total_pycache += 1

    action = "预览删除" if dry_run else "已删除"
    print(f"\n{'='*50}")
    print(f"共 {action} {total_files} 个迁移文件, {total_pycache} 个 __pycache__ 目录")

    if dry_run:
        print("\n这是预览模式，未实际删除任何文件。去掉 --dry-run 参数执行实际删除。")
        return

    if flush_db and reset:
        print("\n[数据库] 清理 django_migrations 表记录...")
        apps = [os.path.basename(os.path.dirname(d)) for d in mig_dirs]
        for app in apps:
            cmd = [
                sys.executable, "manage.py", "shell", "-c",
                f"from django.db import connection; "
                f"cursor = connection.cursor(); "
                f"cursor.execute(\"DELETE FROM django_migrations WHERE app = '{app}'\"); "
                f"print(f'Deleted migrations for {app}')",
            ]
            try:
                subprocess.run(cmd, cwd=base_dir, check=True, capture_output=True, text=True)
                print(f"  [OK] {app}")
            except subprocess.CalledProcessError as e:
                print(f"  [FAIL] {app}: {e.stderr.strip()}")

    if reset:
        print("\n[重建] 重新生成初始迁移...")
        cmd = [sys.executable, "manage.py", "makemigrations"]
        if app_filter:
            cmd.append(app_filter)
        try:
            result = subprocess.run(cmd, cwd=base_dir, check=True, capture_output=True, text=True)
            print(result.stdout)
            if result.stderr:
                print(result.stderr)
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] makemigrations 失败: {e.stderr.strip()}")
            sys.exit(1)

        print("\n[迁移] 执行 migrate --fake...")
        cmd = [sys.executable, "manage.py", "migrate", "--fake"]
        try:
            result = subprocess.run(cmd, cwd=base_dir, check=True, capture_output=True, text=True)
            print(result.stdout)
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] migrate --fake 失败: {e.stderr.strip()}")
            sys.exit(1)

        print("\n[完成] 迁移文件已重建并 fake 标记。")


def main():
    parser = argparse.ArgumentParser(
        description="清理 Django migrations 迁移文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式，只列出将要删除的文件，不实际删除",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="清理后重新生成 0001_initial.py 并 fake 标记",
    )
    parser.add_argument(
        "--flush-db", action="store_true",
        help="删除 django_migrations 表中对应记录（需配合 --reset）",
    )
    parser.add_argument(
        "--app", type=str, default=None,
        help="只清理指定 app 的迁移文件",
    )
    parser.add_argument(
        "--base-dir", type=str, default=None,
        help="项目根目录，默认为脚本所在目录的上一级",
    )

    args = parser.parse_args()

    if args.base_dir:
        base_dir = os.path.abspath(args.base_dir)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    if not os.path.isfile(os.path.join(base_dir, "manage.py")):
        print(f"错误: {base_dir} 下未找到 manage.py，请确认项目根目录")
        sys.exit(1)

    clean_migrations(
        base_dir=base_dir,
        app_filter=args.app,
        dry_run=args.dry_run,
        reset=args.reset,
        flush_db=args.flush_db,
    )


if __name__ == "__main__":
    main()