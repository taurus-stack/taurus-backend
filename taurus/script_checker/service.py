from typing import List, Optional

from .base import BaseChecker
from .result import CheckResult
from .custom_rule import CustomRuleChecker
from .shellcheck import ShellCheckChecker
from .bandit_checker import BanditChecker
from .semgrep_checker import SemgrepChecker


class ScriptCheckService:
    """ScriptdetectService - Unified入口"""

    _checkers: Optional[List[BaseChecker]] = None

    @classmethod
    def get_checkers(cls) -> List[BaseChecker]:
        if cls._checkers is None:
            cls._checkers = [
                ShellCheckChecker(),
                BanditChecker(),
                SemgrepChecker(),
                CustomRuleChecker(),
            ]
        return cls._checkers

    @classmethod
    def check(cls, content: str, script_type: str = '') -> CheckResult:
        """Execution所有可用detectEngine的detect"""
        final_result = CheckResult()

        for checker in cls.get_checkers():
            if not checker.is_available():
                continue
            if not checker.supports(script_type) and script_type:
                continue

            result = checker.check(content, script_type)
            if result:
                final_result.tools_used.extend(result.tools_used)
                for issue in result.issues:
                    final_result.add_issue(issue)

        final_result.calculate_risk_level()
        return final_result

    @classmethod
    def available_tools(cls) -> List[str]:
        """return可用的detect工具list"""
        return [c.name for c in cls.get_checkers() if c.is_available()]
