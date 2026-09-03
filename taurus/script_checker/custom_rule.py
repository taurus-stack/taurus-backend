import re
import time
from typing import List, Tuple

from .base import BaseChecker
from .result import CheckResult, CheckIssue


DEFAULT_RULES: List[Tuple[str, str, str, str, str]] = [
    ('CUSTOM_RM_RF', 'rm -rf 强制删除', 'rm -rf', 'keyword', 'error', 'all', '请确认删除目标，建议加上安全判断'),
    ('CUSTOM_RM_R', 'rm -r 递归删除', 'rm -r ', 'keyword', 'warning', 'all', '请确认删除目标路径'),
    ('CUSTOM_SUDO', 'sudo 提权命令', 'sudo ', 'keyword', 'warning', 'shell', '请确认提权必要性'),
    ('CUSTOM_SU_ROOT', '切换 root 用户', 'su root', 'keyword', 'error', 'shell', '禁止直接切换 root'),
    ('CUSTOM_MKFS', '格式化磁盘命令', 'mkfs', 'keyword', 'error', 'shell', '格式化操作危险，请确认'),
    ('CUSTOM_DD', 'dd 磁盘操作', 'dd if=', 'keyword', 'error', 'shell', 'dd 操作可能损坏磁盘数据'),
    ('CUSTOM_DISK_WRITE', '直接写入磁盘', '> /dev/sd', 'keyword', 'error', 'shell', '直接写磁盘极度危险'),
    ('CUSTOM_SHUTDOWN', '关机命令', 'shutdown', 'keyword', 'error', 'shell', '关机操作需审批'),
    ('CUSTOM_REBOOT', '重启命令', 'reboot', 'keyword', 'warning', 'shell', '重启操作需确认'),
    ('CUSTOM_HALT', '停机命令', 'halt', 'keyword', 'error', 'shell', '停机操作需审批'),
    ('CUSTOM_INIT0', 'init 0 关机', 'init 0', 'keyword', 'error', 'shell', '关机操作需审批'),
    ('CUSTOM_KILLALL', 'killall 批量杀进程', 'killall', 'keyword', 'warning', 'shell', '可能影响其他业务'),
    ('CUSTOM_CHMOD_777', 'chmod 777 开放全部权限', 'chmod 777', 'keyword', 'warning', 'all', '权限过大，建议收窄'),
    ('CUSTOM_CHOWN_ROOT', '递归修改所有者为 root', 'chown -r root', 'keyword', 'warning', 'shell', '可能导致应用异常'),
    ('CUSTOM_SQL_DROP_TABLE', 'SQL DROP TABLE 删表', 'drop table', 'keyword', 'error', 'sql', '删表不可恢复，请确认'),
    ('CUSTOM_SQL_DROP_DATABASE', 'SQL DROP DATABASE 删库', 'drop database', 'keyword', 'error', 'sql', '删库极度危险'),
    ('CUSTOM_SQL_DELETE', 'SQL DELETE 删除数据', 'delete from', 'keyword', 'warning', 'sql', '请确认 WHERE 条件是否完整'),
    ('CUSTOM_SQL_TRUNCATE', 'SQL TRUNCATE 清空表', 'truncate', 'keyword', 'error', 'sql', '清空不可恢复，请确认'),
    ('CUSTOM_SQL_UPDATE_1_1', 'SQL 无条件全表更新', 'update .* set .* where 1=1', 'regex', 'error', 'sql', '全表更新极度危险'),
    ('CUSTOM_SQL_DROP_COLUMN', 'SQL DROP COLUMN 删除列', 'drop column', 'keyword', 'warning', 'sql', '删列不可恢复，请确认'),
    ('CUSTOM_SQL_ALTER_DROP', 'SQL ALTER TABLE DROP', 'alter table drop', 'keyword', 'warning', 'sql', '删列不可恢复'),
]


class CustomRuleChecker(BaseChecker):
    """自Definition规则detectserver - 从数据libraryread规则, supportDynamic Config"""

    name = 'custom_rule'
    supported_types = []

    _rules_cache = None
    _rules_cache_time = 0
    _cache_ttl = 60

    def is_available(self) -> bool:
        return True

    @classmethod
    def _load_rules(cls):
        """从数据libraryload规则, 带cache"""
        now = time.time()
        if cls._rules_cache is not None and (now - cls._rules_cache_time) < cls._cache_ttl:
            return cls._rules_cache

        try:
            from taurus.models import ScriptCheckRule
            rules_qs = ScriptCheckRule.objects.filter(is_active=True).order_by('sort_order', '-id')
            rules = []
            for rule in rules_qs:
                rules.append({
                    'rule_key': rule.rule_key,
                    'name': rule.name,
                    'description': rule.description,
                    'pattern': rule.pattern,
                    'match_type': rule.match_type,
                    'severity': rule.severity,
                    'scope': rule.scope,
                    'fix_suggestion': rule.fix_suggestion,
                })
            if rules:
                cls._rules_cache = rules
                cls._rules_cache_time = now
                return rules
        except Exception:
            pass

        # 数据library不可用时Revert到内置默认规则
        rules = []
        for rule_key, name, pattern, match_type, severity, scope, fix in DEFAULT_RULES:
            rules.append({
                'rule_key': rule_key,
                'name': name,
                'description': name,
                'pattern': pattern,
                'match_type': match_type,
                'severity': severity,
                'scope': scope,
                'fix_suggestion': fix,
            })
        cls._rules_cache = rules
        cls._rules_cache_time = now
        return rules

    @classmethod
    def invalidate_cache(cls):
        """使规则Cache invalidation(规则变更后调用)"""
        cls._rules_cache = None
        cls._rules_cache_time = 0

    def _rule_matches_scope(self, rule: dict, script_type: str) -> bool:
        """check规则YesNo适用于currentScript type"""
        scope = rule.get('scope', 'all')
        if scope == 'all':
            return True
        if not script_type:
            return True
        return script_type.lower() == scope.lower()

    def check(self, content: str, script_type: str = '') -> CheckResult:
        result = CheckResult()
        result.tools_used.append(self.name)

        rules = self._load_rules()
        content_lower = content.lower()

        for rule in rules:
            if not self._rule_matches_scope(rule, script_type):
                continue

            pattern = rule['pattern']
            match_type = rule.get('match_type', 'keyword')
            severity = rule.get('severity', 'warning')
            desc = rule.get('description') or rule.get('name', '')
            fix = rule.get('fix_suggestion', '')
            rule_key = rule.get('rule_key', '')

            try:
                if match_type == 'regex':
                    matches = list(re.finditer(pattern, content_lower, re.IGNORECASE))
                else:
                    matches = []
                    idx = 0
                    while True:
                        pos = content_lower.find(pattern.lower(), idx)
                        if pos == -1:
                            break
                        matches.append(pos)
                        idx = pos + 1

                if not matches:
                    continue

                for match in matches:
                    if match_type == 'regex':
                        line_num = content_lower.count('\n', 0, match.start()) + 1
                    else:
                        line_num = content_lower.count('\n', 0, match) + 1

                    issue = CheckIssue(
                        tool=self.name,
                        severity=severity,
                        line=line_num,
                        rule_id=rule_key,
                        message=desc,
                        fix_suggestion=fix,
                    )
                    result.add_issue(issue)
            except re.error:
                continue

        result.calculate_risk_level()
        return result
