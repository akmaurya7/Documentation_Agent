"""LLM provider interface and external model adapters."""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import AsyncIterator
from typing import Any, Protocol, cast

import httpx

from ..codex_oauth import (
    CodexCredentials,
    CodexCredentialStore,
    CodexOAuthError,
    refresh_credentials,
)
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


class AntigravityCliProvider:
    """Use an already authenticated Antigravity CLI account without token access."""

    def __init__(self, command: str = "agy", timeout_seconds: int = 600) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        self.command = command
        self.timeout_seconds = timeout_seconds

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str
    ) -> ProviderResponse:
        """Ask the CLI for one typed provider response through its stream protocol."""
        instruction = _antigravity_prompt(system, messages, tools)
        event = json.dumps(
            {"event": "user", "message": {"content": instruction}}, separators=(",", ":")
        ).encode()
        with tempfile.TemporaryDirectory(prefix="docagent-agy-") as directory:
            try:
                process = await asyncio.create_subprocess_exec(
                    self.command,
                    "--input-format",
                    "stream-json",
                    "--output-format",
                    "stream-json",
                    "--disable-slash-commands",
                    "--mode",
                    "plan",
                    "--model",
                    model,
                    "--print-timeout",
                    f"{self.timeout_seconds}s",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=directory,
                )
                stdout, _stderr = await asyncio.wait_for(
                    process.communicate(event + b"\n"), timeout=self.timeout_seconds
                )
            except (OSError, TimeoutError) as exc:
                raise ProviderError("Antigravity CLI could not complete safely") from exc
        result = _last_result(stdout)
        if result.get("status") != "SUCCESS":
            raise ProviderError("Antigravity CLI returned an unsuccessful result")
        try:
            response = ProviderResponse.model_validate(json.loads(str(result["response"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("Antigravity CLI returned invalid provider JSON") from exc
        usage = result.get("usage", {})
        if isinstance(usage, dict):
            response.input_tokens = int(usage.get("input_tokens", 0))
            response.output_tokens = int(usage.get("output_tokens", 0))
        return response


def _antigravity_prompt(
    system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> str:
    """Serialize the existing provider-neutral conversation without filesystem access."""
    return (
        f"SYSTEM POLICY:\n{system}\n\n"
        "Return exactly one JSON object matching this schema and no Markdown:\n"
        '{"tool_call":{"id":"string","name":"string","arguments":{}}|null,'
        '"final":{"status":"success|noop|blocked|failed",...}|null}\n\n'
        f"TOOLS:\n{json.dumps(tools, sort_keys=True)}\n\n"
        f"MESSAGES:\n{json.dumps(messages, sort_keys=True)}"
    )


def _last_result(stdout: bytes) -> dict[str, Any]:
    """Read the terminal result event and ignore progress text."""
    for line in reversed(stdout.decode("utf-8", errors="replace").splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            return cast(dict[str, Any], event["result"])
    raise ProviderError("Antigravity CLI returned no result event")


class CodexOAuthProvider:
    """Use a locally stored Sign in with ChatGPT session with Responses API."""

    def __init__(self, store: CodexCredentialStore, timeout_seconds: int = 600) -> None:
        self.store = store
        self.timeout_seconds = timeout_seconds
        self._refresh_lock = asyncio.Lock()

    async def complete(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]], model: str
    ) -> ProviderResponse:
        credentials = await self._valid_credentials()
        payload = {
            "model": model,
            "instructions": system,
            "input": _responses_input(messages),
            "tools": _responses_tools(tools),
            "store": False,
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    "https://api.openai.com/v1/responses",
                    headers={"Authorization": f"Bearer {credentials.access_token}"},
                    json=payload,
                ) as response:
                    if response.is_error:
                        raise ProviderError("Codex Responses API request failed")
                    return await _parse_responses_stream(response.aiter_lines())
        except ProviderError:
            raise
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
            raise ProviderError("Codex Responses API could not complete safely") from exc

    async def _valid_credentials(self) -> CodexCredentials:
        credentials = self.store.load()
        if credentials is None:
            raise ProviderError("Codex OAuth credentials are not configured")
        if credentials.expires_at > int(time.time()) + 120:
            return credentials
        async with self._refresh_lock:
            current = self.store.load() or credentials
            if current.expires_at <= int(time.time()) + 120:
                try:
                    current = await refresh_credentials(current)
                    self.store.save(current)
                except CodexOAuthError as exc:
                    raise ProviderError("Codex OAuth credentials could not be refreshed") from exc
            return current


def _responses_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        }
        for tool in tools
    ]


def _responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            for item in content:
                if item.get("type") == "tool_use":
                    result.append(
                        {
                            "type": "function_call",
                            "call_id": item["id"],
                            "name": item["name"],
                            "arguments": json.dumps(item["input"], separators=(",", ":")),
                        }
                    )
                elif item.get("type") == "tool_result":
                    result.append(
                        {
                            "type": "function_call_output",
                            "call_id": item["tool_use_id"],
                            "output": str(item.get("content", "")),
                        }
                    )
                else:
                    result.append({"role": role, "content": item})
        else:
            result.append({"role": role, "content": content})
    return result


async def _parse_responses_stream(lines: AsyncIterator[str]) -> ProviderResponse:
    call: dict[str, Any] | None = None
    usage = {"input_tokens": 0, "output_tokens": 0}
    async for line in lines:
        if not line.startswith("data: "):
            continue
        try:
            event = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if event.get("type") == "response.output_item.done":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                call = item
        elif event.get("type") == "response.completed":
            usage.update(event.get("response", {}).get("usage", {}))
    if call is None:
        raise ProviderError("Codex model stopped without a tool call; finalize is required")
    try:
        arguments = json.loads(call.get("arguments", "{}"))
        return ProviderResponse(
            tool_call=ToolCall(
                id=call.get("call_id", call.get("id", "")),
                name=call["name"],
                arguments=arguments,
            ),
            input_tokens=int(usage["input_tokens"]),
            output_tokens=int(usage["output_tokens"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError("Codex Responses API returned invalid provider JSON") from exc
