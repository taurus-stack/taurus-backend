from abc import ABC, abstractmethod
from typing import Optional

from .result import CheckResult


class BaseChecker(ABC):
    """detectEngineBase class"""

    name: str = 'base'
    supported_types: list = []

    @abstractmethod
    def is_available(self) -> bool:
        """detectEngineYesNo可用"""
        pass

    @abstractmethod
    def check(self, content: str, script_type: str = '') -> Optional[CheckResult]:
        """Executiondetect, return结果;不support的Script typereturn None"""
        pass

    def supports(self, script_type: str) -> bool:
        """YesNosupport指定Script type"""
        if not self.supported_types:
            return True
        return script_type.lower() in [t.lower() for t in self.supported_types]
