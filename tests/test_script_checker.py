"""
taurus/script_checker/ moduleUnit test

test内容:
1. CheckIssue / CheckResult 数据结构
2. BaseChecker base class
3. CustomRuleChecker 自Definition规则detect
4. ScriptCheckService Unified入口
"""
import pytest

from taurus.script_checker.result import (
    CheckIssue,
    CheckResult,
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    SEVERITY_INFO,
    SEVERITY_STYLE,
    RISK_LEVEL_HIGH,
    RISK_LEVEL_MEDIUM,
    RISK_LEVEL_LOW,
)
from taurus.script_checker.base import BaseChecker
from taurus.script_checker.custom_rule import CustomRuleChecker, DEFAULT_RULES
from taurus.script_checker.service import ScriptCheckService


class TestCheckIssue:
    def test_creation(self):
        issue = CheckIssue(
            tool="test",
            severity=SEVERITY_ERROR,
            line=10,
            message="test error",
        )
        assert issue.tool == "test"
        assert issue.severity == SEVERITY_ERROR
        assert issue.line == 10
        assert issue.message == "test error"

    def test_defaults(self):
        issue = CheckIssue(tool="t", severity=SEVERITY_WARNING, line=1)
        assert issue.column is None
        assert issue.end_line is None
        assert issue.rule_id == ""
        assert issue.fix_suggestion == ""
        assert issue.extra == {}

    def test_to_dict(self):
        issue = CheckIssue(
            tool="shellcheck",
            severity=SEVERITY_ERROR,
            line=5,
            column=10,
            rule_id="SC2086",
            message="Double quote to prevent globbing",
        )
        d = issue.to_dict()
        assert d["tool"] == "shellcheck"
        assert d["severity"] == SEVERITY_ERROR
        assert d["line"] == 5
        assert d["column"] == 10
        assert d["rule_id"] == "SC2086"

    def test_severity_display(self):
        assert CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1).severity_display == "错误"
        assert CheckIssue(tool="t", severity=SEVERITY_WARNING, line=1).severity_display == "警告"
        assert CheckIssue(tool="t", severity=SEVERITY_INFO, line=1).severity_display == "信息"
        assert CheckIssue(tool="t", severity=SEVERITY_STYLE, line=1).severity_display == "风格"
        assert CheckIssue(tool="t", severity="unknown", line=1).severity_display == "unknown"


class TestCheckResult:
    def test_defaults(self):
        result = CheckResult()
        assert result.risk_level == RISK_LEVEL_LOW
        assert result.issues == []
        assert result.tools_used == []
        assert result.error_count == 0
        assert result.warning_count == 0
        assert result.info_count == 0

    def test_add_error_issue(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1))
        assert result.error_count == 1
        assert result.warning_count == 0
        assert len(result.issues) == 1

    def test_add_warning_issue(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_WARNING, line=1))
        assert result.warning_count == 1
        assert result.error_count == 0

    def test_add_info_issue(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_INFO, line=1))
        assert result.info_count == 1

    def test_calculate_risk_level_low(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_INFO, line=1))
        assert result.calculate_risk_level() == RISK_LEVEL_LOW

    def test_calculate_risk_level_medium(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1))
        assert result.calculate_risk_level() == RISK_LEVEL_MEDIUM

    def test_calculate_risk_level_high(self):
        result = CheckResult()
        for _ in range(3):
            result.add_issue(CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1))
        assert result.calculate_risk_level() == RISK_LEVEL_HIGH

    def test_calculate_risk_level_high_mixed(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1))
        for _ in range(3):
            result.add_issue(CheckIssue(tool="t", severity=SEVERITY_WARNING, line=1))
        assert result.calculate_risk_level() == RISK_LEVEL_HIGH

    def test_calculate_risk_level_medium_warnings(self):
        result = CheckResult()
        for _ in range(3):
            result.add_issue(CheckIssue(tool="t", severity=SEVERITY_WARNING, line=1))
        assert result.calculate_risk_level() == RISK_LEVEL_MEDIUM

    def test_to_dict(self):
        result = CheckResult()
        result.add_issue(CheckIssue(tool="t", severity=SEVERITY_ERROR, line=1))
        result.tools_used.append("test-tool")
        d = result.to_dict()
        assert "risk_level" in d
        assert "total_count" in d
        assert d["total_count"] == 1
        assert d["error_count"] == 1
        assert "tools_used" in d
        assert "issues" in d


class TestBaseChecker:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseChecker()

    def test_supports_default_all(self):
        class ConcreteChecker(BaseChecker):
            name = "test"
            supported_types = []

            def is_available(self):
                return True

            def check(self, content, script_type=""):
                return None

        checker = ConcreteChecker()
        assert checker.supports("shell") is True
        assert checker.supports("python") is True
        assert checker.supports("") is True

    def test_supports_specific_types(self):
        class ConcreteChecker(BaseChecker):
            name = "test"
            supported_types = ["shell", "bash"]

            def is_available(self):
                return True

            def check(self, content, script_type=""):
                return None

        checker = ConcreteChecker()
        assert checker.supports("shell") is True
        assert checker.supports("bash") is True
        assert checker.supports("python") is False

    def test_supports_case_insensitive(self):
        class ConcreteChecker(BaseChecker):
            name = "test"
            supported_types = ["Shell"]

            def is_available(self):
                return True

            def check(self, content, script_type=""):
                return None

        checker = ConcreteChecker()
        assert checker.supports("shell") is True
        assert checker.supports("SHELL") is True


class TestCustomRuleChecker:
    def setup_method(self):
        CustomRuleChecker.invalidate_cache()

    def test_is_available(self):
        checker = CustomRuleChecker()
        assert checker.is_available() is True

    def test_detect_rm_rf(self):
        checker = CustomRuleChecker()
        result = checker.check("rm -rf /", "shell")
        assert result is not None
        assert result.error_count >= 1

    def test_detect_sudo(self):
        checker = CustomRuleChecker()
        result = checker.check("sudo apt install something", "shell")
        assert result is not None
        assert result.warning_count >= 1

    def test_detect_chmod_777(self):
        checker = CustomRuleChecker()
        result = checker.check("chmod 777 /tmp", "shell")
        assert result is not None
        assert result.warning_count >= 1

    def test_detect_sql_drop_table(self):
        checker = CustomRuleChecker()
        result = checker.check("DROP TABLE users;", "sql")
        assert result is not None
        assert result.error_count >= 1

    def test_clean_script_no_issues(self):
        checker = CustomRuleChecker()
        result = checker.check("echo 'hello world'", "shell")
        assert result is not None
        assert result.error_count == 0
        assert result.warning_count == 0

    def test_scope_filtering(self):
        checker = CustomRuleChecker()
        result = checker.check("sudo ls", "python")
        assert result is not None
        for issue in result.issues:
            if issue.rule_id == "CUSTOM_SUDO":
                pytest.fail("sudo rule should not match for python scope")

    def test_regex_rule(self):
        checker = CustomRuleChecker()
        result = checker.check("UPDATE users SET name='x' WHERE 1=1;", "sql")
        assert result is not None
        has_regex_match = any(i.rule_id == "CUSTOM_SQL_UPDATE_1_1" for i in result.issues)
        assert has_regex_match

    def test_line_number_correct(self):
        checker = CustomRuleChecker()
        content = "echo 'safe'\nrm -rf /\necho 'also safe'"
        result = checker.check(content, "shell")
        rm_issues = [i for i in result.issues if i.rule_id == "CUSTOM_RM_RF"]
        assert len(rm_issues) >= 1
        assert rm_issues[0].line == 2

    def test_default_rules_loaded(self):
        assert len(DEFAULT_RULES) > 0

    def test_invalidate_cache(self):
        CustomRuleChecker._rules_cache = [{"test": True}]
        CustomRuleChecker._rules_cache_time = 999
        CustomRuleChecker.invalidate_cache()
        assert CustomRuleChecker._rules_cache is None
        assert CustomRuleChecker._rules_cache_time == 0

    def test_multiple_matches(self):
        checker = CustomRuleChecker()
        content = "rm -rf /var\nrm -rf /tmp"
        result = checker.check(content, "shell")
        rm_issues = [i for i in result.issues if i.rule_id == "CUSTOM_RM_RF"]
        assert len(rm_issues) >= 2


class TestScriptCheckService:
    def test_available_tools(self):
        tools = ScriptCheckService.available_tools()
        assert isinstance(tools, list)
        assert "custom_rule" in tools

    def test_check_with_dangerous_script(self):
        result = ScriptCheckService.check("rm -rf /", "shell")
        assert isinstance(result, CheckResult)
        assert result.error_count >= 1

    def test_check_with_clean_script(self):
        result = ScriptCheckService.check("echo 'hello'", "shell")
        assert isinstance(result, CheckResult)

    def test_check_skips_unavailable_checkers(self):
        original_checkers = ScriptCheckService._checkers
        ScriptCheckService._checkers = None
        try:
            result = ScriptCheckService.check("rm -rf /", "shell")
            assert isinstance(result, CheckResult)
        finally:
            ScriptCheckService._checkers = original_checkers