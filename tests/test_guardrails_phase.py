import pytest

from docagent.config import RunLimits
from docagent.guardrails import GuardrailViolation, verify_limits


def test_limits_fail_closed() -> None:
    with pytest.raises(GuardrailViolation, match="tool_calls"):
        verify_limits(
            files=1,
            input_tokens=1,
            output_tokens=1,
            tool_calls=81,
            limits=RunLimits(),
        )
