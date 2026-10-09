import hashlib
from pathlib import Path

import pytest

from docagent.agent.loop import AgentLoopError, run_agent
from docagent.agent.models import ProviderResponse, ToolCall
from docagent.agent.prompt import load_prompt
from docagent.agent.provider import AntigravityCliProvider, ProviderError
from docagent.agent.tools import ToolContext
from docagent.github.checkout import Checkout


class ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = iter(responses)

    async def complete(self, system, messages, tools, model):
        del system, messages, tools, model
        return next(self.responses)


class FakeAntigravityProcess:
    async def communicate(self, payload: bytes):
        assert b"SYSTEM POLICY" in payload
        return (
            b'{"event":"result","result":{"status":"SUCCESS",'
            b'"response":"{\\"tool_call\\":null,\\"final\\":'
            b'{\\"status\\":\\"noop\\"}}",'
            b'"usage":{"input_tokens":12,"output_tokens":4}}}\n',
            b"",
        )


@pytest.mark.asyncio
async def test_antigravity_cli_provider_uses_typed_stream_result(monkeypatch) -> None:
    captured = {}

    async def create_process(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeAntigravityProcess()

    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)
    response = await AntigravityCliProvider(timeout_seconds=5).complete(
        "policy", [], [], "gemini-test"
    )
    assert response.final is not None and response.final.status == "noop"
    assert response.input_tokens == 12
    assert response.output_tokens == 4
    assert "--mode" in captured["args"]
    assert captured["kwargs"]["cwd"]


@pytest.mark.asyncio
async def test_antigravity_cli_provider_rejects_missing_result(monkeypatch) -> None:
    class EmptyProcess:
        async def communicate(self, payload: bytes):
            del payload
            return b"", b""

    async def create_process(*args, **kwargs):
        del args, kwargs
        return EmptyProcess()

    monkeypatch.setattr("asyncio.create_subprocess_exec", create_process)
    with pytest.raises(ProviderError, match="no result"):
        await AntigravityCliProvider(timeout_seconds=5).complete("policy", [], [], "model")


def test_prompt_hash_is_required(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("policy", encoding="utf-8")
    digest = hashlib.sha256(b"policy").hexdigest()
    assert load_prompt(str(prompt), {digest}) == ("policy", digest)
    with pytest.raises(RuntimeError):
        load_prompt(str(prompt), set())


@pytest.mark.asyncio
async def test_agent_writes_docs_and_finishes_through_finalize(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("policy", encoding="utf-8")
    digest = hashlib.sha256(b"policy").hexdigest()
    provider = ScriptedProvider([
        ProviderResponse(
            tool_call=ToolCall(
                id="1",
                name="write_doc_file",
                arguments={"path": "docs/guide.md", "content": "# Guide\n"},
            ),
            input_tokens=3,
            output_tokens=2,
        ),
        ProviderResponse(
            tool_call=ToolCall(
                id="2",
                name="finalize",
                arguments={"status": "success", "files_changed": ["docs/guide.md"]},
            ),
            input_tokens=4,
            output_tokens=1,
        ),
    ])
    report = await run_agent(
        provider=provider,
        context=ToolContext(Checkout(repo), "docs"),
        prompt_path=str(prompt),
        allowed_prompt_hashes={digest},
        model="fake",
        run_id="r1",
        event_type="push",
        head_sha="abc",
    )
    assert report.status == "success"
    assert report.prompt_hash == digest
    assert report.input_tokens == 7
    assert report.output_tokens == 3
    assert (repo / "docs" / "guide.md").read_text(encoding="utf-8") == "# Guide\n"


@pytest.mark.asyncio
async def test_agent_cannot_stop_without_finalize(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("policy", encoding="utf-8")
    digest = hashlib.sha256(b"policy").hexdigest()
    provider = ScriptedProvider(
        [ProviderResponse(tool_call=ToolCall(id="1", name="read_file", arguments={"path": "x"}))]
    )
    with pytest.raises(AgentLoopError):
        await run_agent(
            provider=provider,
            context=ToolContext(Checkout(tmp_path), "docs"),
            prompt_path=str(prompt),
            allowed_prompt_hashes={digest},
            model="fake",
            run_id="r1",
            event_type="push",
            head_sha="abc",
            max_tool_calls=1,
        )


@pytest.mark.asyncio
async def test_agent_enforces_provider_token_budget(tmp_path: Path) -> None:
    prompt = tmp_path / "prompt.md"
    prompt.write_text("policy", encoding="utf-8")
    digest = hashlib.sha256(b"policy").hexdigest()
    provider = ScriptedProvider([
        ProviderResponse(
            tool_call=ToolCall(id="1", name="finalize", arguments={"status": "success"}),
            input_tokens=11,
        )
    ])
    with pytest.raises(AgentLoopError, match="token budget"):
        await run_agent(
            provider=provider,
            context=ToolContext(Checkout(tmp_path), "docs"),
            prompt_path=str(prompt),
            allowed_prompt_hashes={digest},
            model="fake",
            run_id="r1",
            event_type="push",
            head_sha="abc",
        max_input_tokens=10,
    )
