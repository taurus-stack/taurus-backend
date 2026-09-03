"""扫描所有已RegistryAdapter的 docstring 和源码, GenerateError码参考document(Markdown).

扫描两处Error码声明:
  1. docstring 中的 ``- Exxxx: Description`` 或 ``Exxxx Description`` Format
  2. 源码字符串中的 ``"Exxxx: Description"`` / ``'Exxxx: Description'``(如 add_error / error_message)

用法:
  python manage.py generate_error_code_ref
  python manage.py generate_error_code_ref --output docs/workflow/error-code-reference.md
"""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from pathlib import Path

from django.core.management.base import BaseCommand

from taurus.workflow.engine.registry import get_registry

# docstring 中的Error码:match "- Exxxx: Description" / "  Exxxx Description" / "Exxxx:Description"
_DOC_ERROR_RE = re.compile(
    r"(?:^|\n)\s*[-*]?\s*(E\d{4})\s*[:：]?\s*(.+?)\s*(?:\n|$)",
)

# 源码字符串中的Error码:match "Exxxx: Description" / 'Exxxx: Description'
_CODE_ERROR_RE = re.compile(
    r"""['"](?P<code>E\d{4})\s*[:：]\s*(?P<msg>[^'"]+?)['"]""",
)


class Command(BaseCommand):
    help = '扫描所有适配器 docstring 和源码，生成错误码参考文档（Markdown）'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output', '-o',
            default=None,
            help='输出文件路径（默认输出到 stdout）',
        )

    def handle(self, *args, **options):
        registry = get_registry()
        adapter_types = registry.list_all_adapter_types()

        # 用 (code, node_type) 去重, keep首次出现的Description
        seen: set[tuple[str, str]] = set()
        entries: list[dict] = []

        for nt in adapter_types:
            cls = registry.get_class(nt)
            module = sys.modules.get(cls.__module__)
            if module is None:
                module = importlib.import_module(cls.__module__)
            display_name = getattr(cls, 'display_name', nt)

            # 1) 扫描 docstring(Modules + class)
            doc = (module.__doc__ or '') + '\n' + (cls.__doc__ or '')
            for m in _DOC_ERROR_RE.finditer(doc):
                code = m.group(1)
                msg = m.group(2).strip().rstrip('-*').strip()
                if not msg:
                    continue
                key = (code, nt)
                if key not in seen:
                    seen.add(key)
                    entries.append({
                        'code': code,
                        'message': msg,
                        'node_type': nt,
                        'display_name': display_name,
                    })

            # 2) 扫描源码字符串
            try:
                source = inspect.getsource(module)
            except (OSError, TypeError):
                source = ''
            for m in _CODE_ERROR_RE.finditer(source):
                code = m.group('code')
                msg = m.group('msg').strip()
                if not msg:
                    continue
                key = (code, nt)
                if key not in seen:
                    seen.add(key)
                    entries.append({
                        'code': code,
                        'message': msg,
                        'node_type': nt,
                        'display_name': display_name,
                    })

        entries.sort(key=lambda e: (e['code'], e['node_type']))

        lines = [
            '# WorkflowError码参考',
            '',
            f'> 自动生成自 {len(adapter_types)} 个已注册适配器（共 {len(entries)} 条错误码）。',
            '>',
            '> 扫描范围：模块/类 docstring + 源码字符串（add_error / error_message）。',
            '',
            '| 错误码 | 适配器 | 说明 |',
            '|--------|--------|------|',
        ]
        for e in entries:
            # 转义 markdown 表格中的管道符
            msg = e['message'].replace('|', '\\|')
            lines.append(f"| {e['code']} | {e['display_name']}（{e['node_type']}） | {msg} |")
        lines.append('')

        content = '\n'.join(lines)

        output = options.get('output')
        if output:
            Path(output).parent.mkdir(parents=True, exist_ok=True)
            Path(output).write_text(content, encoding='utf-8')
            self.stdout.write(self.style.SUCCESS(
                f'已生成错误码参考文档：{output}（{len(entries)} 条）'
            ))
        else:
            self.stdout.write(content)