import hashlib
from pathlib import Path

import pytest

from docagent.agent.loop import AgentLoopError, run_agent
from docagent.agent.models import ProviderResponse, ToolCall
from docagent.agent.prompt import load_prompt
from docagent.agent.tools import ToolContext
from docagent.github.checkout import Checkout


class ScriptedProvider:
    def __init__(self, responses: list[ProviderResponse]) -> None:
        self.responses = iter(responses)

    async def complete(self, system, messages, tools, model):
        del system, messages, tools, model
        return next(self.responses)


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
            )
        ),
        ProviderResponse(
            tool_call=ToolCall(
                id="2",
                name="finalize",
                arguments={"status": "success", "files_changed": ["docs/guide.md"]},
            )
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
