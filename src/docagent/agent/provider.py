"""LLM provider interface and Anthropic adapter."""

from __future__ import annotations

from typing import Any, Protocol

from .models import ProviderResponse, ToolCall


class ProviderError(RuntimeError):
    """Raised when the provider cannot return validated structured output."""


class Provider(Protocol):
    """Provider contract used by the loop and fake tests."""

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str
    ) -> ProviderResponse:
        """Generate one bounded response."""


class AnthropicProvider:
    """Official Anthropic SDK adapter with a narrow normalized response."""

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str
    ) -> ProviderResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()
        response = await client.messages.create(
            model=model,
            max_tokens=4096,
            temperature=0,
            system=system,
            messages=messages,  # type: ignore[arg-type]
            tools=tools,  # type: ignore[arg-type]
        )
        usage = response.usage
        for block in response.content:
            if block.type == "tool_use":
                return ProviderResponse(
                    tool_call=ToolCall.model_validate(
                        {"id": block.id, "name": block.name, "arguments": block.input}
                    ),
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
        raise ProviderError("model stopped without a tool call; finalize is required")
