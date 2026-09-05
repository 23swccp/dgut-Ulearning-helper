from types import SimpleNamespace

import pytest

from dgutbot.experimental.ulearning_ai import ChatChunk, ChatContext
from dgutbot.experimental.ulearning_ai_bridge import UlearningAiBridge, flatten_messages


def test_flatten_messages_preserves_single_user_query():
    assert flatten_messages([{"role": "user", "content": "  hello  "}]) == "hello"


def test_flatten_messages_includes_multiturn_roles():
    prompt = flatten_messages([
        {"role": "system", "content": "be concise"},
        {"role": "assistant", "content": "ready"},
        {"role": "user", "content": "answer"},
    ])
    assert "[system]\nbe concise" in prompt
    assert prompt.endswith("[user]\nanswer")


def test_flatten_messages_requires_final_user_message():
    with pytest.raises(ValueError, match="final"):
        flatten_messages([{"role": "assistant", "content": "hello"}])


def test_bridge_collects_text_reasoning_and_upstream_calls():
    access = SimpleNamespace(
        context=ChatContext("1", "2", "3"),
        create_session=lambda: object(),
    )

    class FakeClient:
        def __init__(self, _session):
            pass

        def stream_chat(self, _context, **_kwargs):
            yield ChatChunk(text="hello", reasoning="think", tool_calls=({"id": "call-1"},))

    bridge = UlearningAiBridge(
        access_factory=lambda _port: access,
        client_factory=FakeClient,
    )
    reply = bridge.complete([{"role": "user", "content": "probe"}])
    assert reply.text == "hello"
    assert reply.reasoning == "think"
    assert reply.upstream_tool_calls == ({"id": "call-1"},)


def test_backend_chat_command_returns_only_safe_tool_count():
    from dgutbot.app import backend_commands

    reply = SimpleNamespace(text="answer", reasoning="", upstream_tool_calls=({"arguments": "private"},))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backend_commands.AI_BRIDGE, "complete", lambda _messages: reply)
        result = backend_commands.handle("ai_chat", {"messages": [{"role": "user", "content": "hello"}]})
    assert result == {"ok": True, "answer": "answer", "reasoning": "", "upstreamToolCallCount": 1}
    assert "private" not in repr(result)
