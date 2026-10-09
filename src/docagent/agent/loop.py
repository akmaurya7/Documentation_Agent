"""Bounded tool-calling loop that can finish only through finalize."""

from __future__ import annotations

from typing import Any

from ..redaction import redact
from .models import RunReport
from .prompt import load_prompt
from .provider import Provider
from .tools import ToolContext, ToolError, tool_definitions


class AgentLoopError(RuntimeError):
    """Raised when the model cannot complete a safe run."""


async def run_agent(
    *,
    provider: Provider,
    context: ToolContext,
    prompt_path: str,
    allowed_prompt_hashes: set[str],
    model: str,
    run_id: str,
    event_type: str,
    head_sha: str,
    max_tool_calls: int = 20,
    max_input_tokens: int = 40_000,
    max_output_tokens: int = 12_000,
) -> RunReport:
    """Run a bounded agent and validate the final report."""
    system, prompt_hash = load_prompt(prompt_path, allowed_prompt_hashes)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": "Analyze the change and update documentation."}
    ]
    input_tokens = 0
    output_tokens = 0
    for _ in range(max_tool_calls):
        response = await provider.complete(system, messages, tool_definitions(), model)
        input_tokens += response.input_tokens
        output_tokens += response.output_tokens
        if input_tokens > max_input_tokens or output_tokens > max_output_tokens:
            raise AgentLoopError("provider token budget exceeded")
        if response.tool_call is None:
            raise AgentLoopError("provider stopped without finalize")
        call = response.tool_call
        if call.name == "finalize":
            report_data = context.execute(call.name, call.arguments)["value"]
            _, hits = redact(str(report_data))
            if hits:
                raise AgentLoopError("secret detected in final report")
            return RunReport(
                run_id=run_id,
                event_type=event_type,
                head_sha=head_sha,
                prompt_hash=prompt_hash,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                **report_data,
            )
        try:
            result = context.execute(call.name, call.arguments)
        except ToolError as exc:
            result = {"error": str(exc)}
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call.id,
                        "name": call.name,
                        "input": call.arguments,
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": str(result),
                    }
                ],
            }
        )
    raise AgentLoopError("tool-call limit exceeded before finalize")
