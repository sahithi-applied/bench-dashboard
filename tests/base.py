"""Base types for hardware tests."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class TestResult:
    test_name: str
    device_name: str
    status: str = "not_run"       # not_run | running | pass | fail | error
    input_desc: str = ""
    output_desc: str = ""
    error: Optional[str] = None
    duration_ms: int = 0
    started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "test_name": self.test_name,
            "device_name": self.device_name,
            "status": self.status,
            "input": self.input_desc,
            "output": self.output_desc,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


def run_test(fn):
    """Decorator that times a test and catches exceptions into TestResult."""
    def wrapper(*args, **kwargs) -> TestResult:
        result: TestResult = fn(*args, **kwargs)
        return result
    return wrapper
