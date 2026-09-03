import shutil
import subprocess
import tempfile
import os
import json

from .base import BaseChecker
from .result import CheckResult, CheckIssue, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO


SEMGREP_FIX_SUGGESTIONS = {
    'python.lang.security': 'Python 安全问题，请参考 Semgrep 规则详情',
    'shell.lang.security': 'Shell 安全问题，请参考 Semgrep 规则详情',
    'javascript.lang.security': 'JavaScript 安全问题，请参考 Semgrep 规则详情',
    'typescript.lang.security': 'TypeScript 安全问题，请参考 Semgrep 规则详情',
    'go.lang.security': 'Go 安全问题，请参考 Semgrep 规则详情',
    'java.lang.security': 'Java 安全问题，请参考 Semgrep 规则详情',
}


class SemgrepChecker(BaseChecker):
    """Semgrep 多语言通用detectEngine"""

    name = 'semgrep'
    supported_types = []

    def is_available(self) -> bool:
        return shutil.which('semgrep') is not None

    def check(self, content: str, script_type: str = '') -> CheckResult:
        result = CheckResult()

        if not self.is_available():
            return result

        result.tools_used.append(self.name)

        file_suffix = self._get_file_suffix(script_type)
        if not file_suffix:
            return result

        config = self._get_config(script_type)
        if not config:
            return result

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix=file_suffix, delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                cmd = [
                    'semgrep', '--config', config,
                    '--json', '--no-rewrite-rule-ids',
                    '--quiet',
                    temp_path,
                ]
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env={**os.environ, 'SEMGREP_SEND_METRICS': 'off'},
                )

                if proc.stdout:
                    try:
                        data = json.loads(proc.stdout)
                    except json.JSONDecodeError:
                        return result

                    results = data.get('results', [])
                    for item in results:
                        severity = self._map_severity(item.get('extra', {}).get('severity', 'WARNING'))
                        rule_id = item.get('check_id', '')
                        message = item.get('extra', {}).get('message', '')
                        fix = item.get('extra', {}).get('fix', '')

                        fix_suggestion = self._get_suggestion(rule_id)
                        if fix and not fix_suggestion:
                            fix_suggestion = f'建议修改为：{fix[:80]}'

                        start = item.get('start', {})
                        end = item.get('end', {})

                        issue = CheckIssue(
                            tool=self.name,
                            severity=severity,
                            line=start.get('line', 0),
                            column=start.get('col'),
                            end_line=end.get('line'),
                            rule_id=rule_id.split('.')[-1] if '.' in rule_id else rule_id,
                            message=message,
                            fix_suggestion=fix_suggestion,
                            extra={'full_rule_id': rule_id}
                        )
                        result.add_issue(issue)
            finally:
                os.unlink(temp_path)

        except Exception:
            pass

        result.calculate_risk_level()
        return result

    def _get_file_suffix(self, script_type: str) -> str:
        mapping = {
            'python': '.py',
            'shell': '.sh',
            'bash': '.sh',
            'javascript': '.js',
            'typescript': '.ts',
            'go': '.go',
            'java': '.java',
            'sql': '.sql',
        }
        return mapping.get(script_type.lower(), '')

    def _get_config(self, script_type: str) -> str:
        mapping = {
            'python': 'p/python',
            'shell': 'p/bash',
            'bash': 'p/bash',
            'javascript': 'p/javascript',
            'typescript': 'p/typescript',
            'go': 'p/go',
            'java': 'p/java',
        }
        return mapping.get(script_type.lower(), '')

    def _map_severity(self, severity: str) -> str:
        mapping = {
            'ERROR': SEVERITY_ERROR,
            'WARNING': SEVERITY_WARNING,
            'INFO': SEVERITY_INFO,
        }
        return mapping.get(severity.upper(), SEVERITY_INFO)

    def _get_suggestion(self, rule_id: str) -> str:
        for prefix, suggestion in SEMGREP_FIX_SUGGESTIONS.items():
            if prefix in rule_id:
                return suggestion
        return ''
