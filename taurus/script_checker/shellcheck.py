import shutil
import subprocess
import tempfile
import os
import json

from .base import BaseChecker
from .result import CheckResult, CheckIssue, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_STYLE


SC_FIX_SUGGESTIONS = {
    'SC2086': 'Change $var to "${var}" to prevent word splitting and glob expansion',
    'SC2046': 'Wrap $(cmd) in double quotes, e.g. "$(cmd)"',
    'SC2039': 'Script missing shebang or uses non-POSIX features, add #!/bin/bash',
    'SC2148': 'Add shebang on first line, e.g. #!/bin/bash',
    'SC1000': 'Dollar sign does not need escaping, remove redundant backslash',
    'SC1073': 'Syntax error, check bracket or quote pairing',
    'SC1072': 'Syntax error, check if/for/while structure completeness',
    'SC2002': 'No need for cat, redirect directly: command < file instead of cat file | command',
    'SC2005': 'No need for echo, use variable directly: var=$(cmd) instead of echo $(cmd)',
    'SC2035': 'Use ./* or -- prefix to avoid filenames starting with - being treated as options',
    'SC2044': 'Use glob instead of find output for filename iteration in for loops',
    'SC2089': 'Wrong array element reference, use "${array[@]}"',
    'SC2128': 'Wrong array reference, use "${array[@]}" to iterate all elements',
    'SC2154': 'Variable used before definition, check spelling or assignment',
    'SC2155': 'Separate declaration and assignment to avoid masking return value',
    'SC2164': 'Script continues on cd failure, use cd dir || exit 1',
    'SC2181': 'Check command return directly instead of $?, e.g. if cmd; then ...',
    'SC2015': 'Watch && || precedence, use if/else instead',
    'SC2001': 'Use ${var/old/new} for simple substitution instead of sed',
    'SC2016': 'Variables not expanded in single quotes, confirm if double quotes needed',
}


class ShellCheckChecker(BaseChecker):
    """ShellCheck detectEngine"""

    name = 'shellcheck'
    supported_types = ['Shell', 'Bash', 'sh', 'bash']

    def is_available(self) -> bool:
        return shutil.which('shellcheck') is not None

    def check(self, content: str, script_type: str = '') -> CheckResult:
        result = CheckResult()

        if not self.is_available():
            return result

        if not self.supports(script_type) and script_type:
            return result

        result.tools_used.append(self.name)

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.sh', delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                proc = subprocess.run(
                    ['shellcheck', '-f', 'json', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if proc.stdout:
                    try:
                        issues_data = json.loads(proc.stdout)
                    except json.JSONDecodeError:
                        return result

                    for item in issues_data:
                        level = item.get('level', 'info')
                        severity = self._map_severity(level)
                        code = f"SC{item.get('code', '')}"
                        issue = CheckIssue(
                            tool=self.name,
                            severity=severity,
                            line=item.get('line', 0),
                            column=item.get('column'),
                            end_line=item.get('endLine'),
                            rule_id=code,
                            message=item.get('message', ''),
                            fix_suggestion=self._get_fix_suggestion(code, item),
                        )
                        result.add_issue(issue)
            finally:
                os.unlink(temp_path)

        except Exception:
            pass

        result.calculate_risk_level()
        return result

    def _map_severity(self, level: str) -> str:
        mapping = {
            'error': SEVERITY_ERROR,
            'warning': SEVERITY_WARNING,
            'info': SEVERITY_INFO,
            'style': SEVERITY_STYLE,
        }
        return mapping.get(level, SEVERITY_INFO)

    def _get_fix_suggestion(self, code: str, item: dict) -> str:
        """Fetch修复建议:优先用内置建议, 有自动修复则appendInfo"""
        suggestion = SC_FIX_SUGGESTIONS.get(code, '')

        fix = item.get('fix')
        if fix and fix.get('replacements'):
            if suggestion:
                suggestion += '（Auto-fix available）'
            else:
                suggestion = 'Auto-fix available'

        return suggestion
