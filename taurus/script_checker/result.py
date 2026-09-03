from dataclasses import dataclass, field
from typing import List, Optional


SEVERITY_ERROR = 'error'
SEVERITY_WARNING = 'warning'
SEVERITY_INFO = 'info'
SEVERITY_STYLE = 'style'

RISK_LEVEL_HIGH = 'high'
RISK_LEVEL_MEDIUM = 'medium'
RISK_LEVEL_LOW = 'low'

RISK_LEVEL_MAP = {
    RISK_LEVEL_HIGH: 'High',
    RISK_LEVEL_MEDIUM: 'Medium',
    RISK_LEVEL_LOW: 'Low',
}


@dataclass
class CheckIssue:
    """单 detect问题"""
    tool: str
    severity: str
    line: int
    column: Optional[int] = None
    end_line: Optional[int] = None
    rule_id: str = ''
    message: str = ''
    fix_suggestion: str = ''
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            'tool': self.tool,
            'severity': self.severity,
            'severity_display': self.severity_display,
            'line': self.line,
            'column': self.column,
            'end_line': self.end_line,
            'rule_id': self.rule_id,
            'message': self.message,
            'fix_suggestion': self.fix_suggestion,
        }

    @property
    def severity_display(self):
        return {
            SEVERITY_ERROR: 'Error',
            SEVERITY_WARNING: 'Warning',
            SEVERITY_INFO: 'Info',
            SEVERITY_STYLE: 'Style',
        }.get(self.severity, self.severity)


@dataclass
class CheckResult:
    """detect结果汇总"""
    risk_level: str = RISK_LEVEL_LOW
    issues: List[CheckIssue] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0

    def add_issue(self, issue: CheckIssue):
        self.issues.append(issue)
        if issue.severity == SEVERITY_ERROR:
            self.error_count += 1
        elif issue.severity == SEVERITY_WARNING:
            self.warning_count += 1
        else:
            self.info_count += 1

    def calculate_risk_level(self) -> str:
        """according to问题数量compute风险等级"""
        if self.error_count >= 3 or self.error_count >= 1 and self.warning_count >= 3:
            self.risk_level = RISK_LEVEL_HIGH
        elif self.error_count >= 1 or self.warning_count >= 3:
            self.risk_level = RISK_LEVEL_MEDIUM
        else:
            self.risk_level = RISK_LEVEL_LOW
        return self.risk_level

    def to_dict(self):
        self.calculate_risk_level()
        return {
            'risk_level': self.risk_level,
            'risk_level_display': RISK_LEVEL_MAP.get(self.risk_level, self.risk_level),
            'total_count': len(self.issues),
            'error_count': self.error_count,
            'warning_count': self.warning_count,
            'info_count': self.info_count,
            'tools_used': self.tools_used,
            'issues': [issue.to_dict() for issue in self.issues],
        }
