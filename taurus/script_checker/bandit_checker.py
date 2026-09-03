import shutil
import subprocess
import tempfile
import os
import json

from .base import BaseChecker
from .result import CheckResult, CheckIssue, SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO


BANDIT_FIX_SUGGESTIONS = {
    'B101': '避免使用 assert 语句做生产环境校验，改用显式异常判断',
    'B102': '避免使用 exec()，可能导致代码注入风险',
    'B103': '设置合理的文件权限，避免使用 0o777 或 0o666',
    'B104': '避免硬编码绑定所有接口（0.0.0.0），建议绑定指定 IP',
    'B105': '硬编码密码，改用环境变量或配置中心读取',
    'B106': '硬编码密码，改用环境变量或配置中心读取',
    'B107': '硬编码密码，改用环境变量或配置中心读取',
    'B108': '避免在 /tmp 下创建硬编码文件，改用 tempfile 模块',
    'B110': 'except 块不应为空，至少应记录日志',
    'B112': '避免使用 try/except/pass 静默吞掉异常',
    'B201': '避免使用 flask_debug，生产环境应关闭 debug 模式',
    'B301': '避免使用 pickle 加载不可信数据，改用 json',
    'B302': '避免使用 marshal 加载不可信数据，改用 json',
    'B303': '避免使用 MD5/SHA1 等弱哈希算法，改用 SHA-256',
    'B304': '避免使用不安全的随机数生成器，改用 secrets 模块',
    'B305': '避免使用不安全的加密模式，改用 GCM 或 CBC+IV',
    'B306': 'mktemp 不安全，改用 tempfile.mkstemp()',
    'B307': '避免使用 eval()，可能导致代码注入风险',
    'B308': '避免使用 telnetlib，改用 SSH 或安全传输协议',
    'B309': '避免使用 ftplib 明文传输，改用 SFTP',
    'B310': 'urllib 证书验证被跳过，存在中间人攻击风险',
    'B311': '使用 secrets 模块生成随机数，而非 random 模块',
    'B312': '避免使用 telnet 明文通信，改用 SSH',
    'B313': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B314': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B315': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B316': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B317': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B318': 'XML 解析未禁用外部实体，存在 XXE 攻击风险',
    'B319': '证书验证被禁用，存在中间人攻击风险',
    'B320': '使用 Django 的安全方法而非原始方法',
    'B321': 'requests 未验证证书，存在中间人攻击风险',
    'B322': '避免使用 input()（Python 2 中不安全）',
    'B323': '避免使用不安全的 HTTP 连接，改用 HTTPS',
    'B401': '避免使用 ftplib 明文传输，改用 SFTP',
    'B402': '避免使用 telnet 明文通信，改用 SSH',
    'B403': '避免使用 pickle 序列化不可信数据',
    'B404': '避免使用 subprocess 调用 shell，如必须使用请校验参数',
    'B405': '避免使用 os.system 执行命令，改用 subprocess.run',
    'B406': '避免使用 os.popen 执行命令，改用 subprocess.run',
    'B407': '避免使用 commands 模块（已废弃），改用 subprocess',
    'B408': 'import 了不安全的模块，请评估风险',
    'B409': 'import 了不安全的模块，请评估风险',
    'B410': 'import 了不安全的模块，请评估风险',
    'B411': 'import 了不安全的模块，请评估风险',
    'B412': 'import 了不安全的模块，请评估风险',
    'B413': 'import 了不安全的模块，请评估风险',
    'B501': '避免使用带有 shell=True 的 subprocess，存在命令注入风险',
    'B502': 'subprocess 调用中 shell=True 且参数来自用户输入，存在命令注入',
    'B503': '避免使用 shell=True，改为列表形式传参',
    'B504': 'SSL 版本不安全，存在降级攻击风险',
    'B505': '弱加密算法 Cipher 被使用，改用 AES-GCM 等安全算法',
    'B506': '避免使用 yaml.load，改用 yaml.safe_load',
    'B507': 'SSH 连接未验证主机密钥，存在中间人攻击风险',
    'B508': '避免使用 paramiko 不安全的主机密钥验证',
    'B601': '避免使用 paramiko 不安全的主机密钥验证',
    'B602': 'subprocess 调用 shell=True 存在命令注入风险',
    'B603': '避免使用 shell=True 执行命令，改用列表参数',
    'B604': '避免使用 shell=True 执行命令，改用列表参数',
    'B605': '命令拼接后执行，存在命令注入风险',
    'B606': '命令拼接后执行，存在命令注入风险',
    'B607': '命令部分来自用户输入，存在命令注入风险',
    'B608': 'SQL 拼接，存在 SQL 注入风险，使用参数化查询',
    'B609': 'SQL 拼接，存在 SQL 注入风险，使用参数化查询',
    'B610': '避免使用 os.system 执行拼接后的命令',
    'B611': 'SQL 拼接，存在 SQL 注入风险，使用参数化查询',
    'B701': 'Jinja2 模板使用 autoescape=False，存在 XSS 风险',
    'B702': 'Mako 模板未转义，存在 XSS 风险',
}


class BanditChecker(BaseChecker):
    """Bandit Python 安全detectEngine"""

    name = 'bandit'
    supported_types = ['Python', 'python', 'py']

    def is_available(self) -> bool:
        return shutil.which('bandit') is not None

    def check(self, content: str, script_type: str = '') -> CheckResult:
        result = CheckResult()

        if not self.is_available():
            return result

        if not self.supports(script_type) and script_type:
            return result

        result.tools_used.append(self.name)

        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
                f.write(content)
                temp_path = f.name

            try:
                proc = subprocess.run(
                    ['bandit', '-f', 'json', '-o', '-', temp_path],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )

                if proc.stdout:
                    try:
                        data = json.loads(proc.stdout)
                    except json.JSONDecodeError:
                        return result

                    results = data.get('results', [])
                    for item in results:
                        severity = self._map_severity(item.get('issue_severity', 'LOW'))
                        test_id = item.get('test_id', '')
                        issue_text = item.get('issue_text', '')

                        suggestion = BANDIT_FIX_SUGGESTIONS.get(
                            test_id,
                            item.get('issue_cwe', {}).get('link', '')
                        )

                        issue = CheckIssue(
                            tool=self.name,
                            severity=severity,
                            line=item.get('line_number', 0),
                            end_line=item.get('line_number', 0),
                            rule_id=test_id,
                            message=issue_text,
                            fix_suggestion=suggestion,
                            extra={
                                'confidence': item.get('issue_confidence', ''),
                                'more_info': item.get('more_info', ''),
                            }
                        )
                        result.add_issue(issue)
            finally:
                os.unlink(temp_path)

        except Exception:
            pass

        result.calculate_risk_level()
        return result

    def _map_severity(self, severity: str) -> str:
        mapping = {
            'HIGH': SEVERITY_ERROR,
            'MEDIUM': SEVERITY_WARNING,
            'LOW': SEVERITY_INFO,
        }
        return mapping.get(severity.upper(), SEVERITY_INFO)
