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


def test_bridge_probe_only_discovers_browser_access():
    calls = []
    bridge = UlearningAiBridge(access_factory=lambda port: calls.append(port), debug_port=lambda: 9333)
    assert bridge.probe() is None
    assert calls == [9333]


def test_backend_chat_command_returns_only_safe_tool_count():
    from dgutbot.app import backend_commands

    reply = SimpleNamespace(text="answer", reasoning="", upstream_tool_calls=({"arguments": "private"},))
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backend_commands.AI_BRIDGE, "complete", lambda _messages: reply)
        result = backend_commands.handle("ai_chat", {"messages": [{"role": "user", "content": "hello"}]})
    assert result == {"ok": True, "answer": "answer", "reasoning": "", "upstreamToolCallCount": 1}
    assert "private" not in repr(result)


def test_gui_course_start_requires_ready_ai_when_auto_answer_is_enabled():
    from dgutbot.app import backend_commands
    from dgutbot.experimental.ulearning_ai import UlearningAiError

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backend_commands.backend.config, "course_quiz_auto_answer", True)
        monkeypatch.setattr(backend_commands.AI_BRIDGE, "probe", lambda: (_ for _ in ()).throw(UlearningAiError("private")))
        start = SimpleNamespace(called=False)
        monkeypatch.setattr(backend_commands.backend, "start_course_helper", lambda **_kwargs: setattr(start, "called", True))
        result = backend_commands.handle("start_course_helper", {})
    assert result["ok"] is False
    assert "进入对话" in result["error"]
    assert "private" not in repr(result)
    assert start.called is False


def test_gui_course_start_injects_ai_provider_after_probe():
    from dgutbot.app import backend_commands

    observed = []
    controller = SimpleNamespace(emit=lambda *_args: None)
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backend_commands.backend.config, "course_quiz_auto_answer", True)
        monkeypatch.setattr(backend_commands.AI_BRIDGE, "probe", lambda: observed.append("probe"))
        monkeypatch.setattr(backend_commands.backend, "_course_controller", controller)
        monkeypatch.setattr(backend_commands.backend, "start_course_helper", lambda **kwargs: observed.append(kwargs) or True)
        result = backend_commands.handle("start_course_helper", {})
    assert result == {"ok": True}
    assert observed[0] == "probe"
    assert observed[1]["quiz_mode"] == "ai"
    assert observed[1]["ai_provider"].bridge is backend_commands.AI_BRIDGE


def test_gui_course_start_skips_ai_probe_when_auto_answer_is_disabled():
    from dgutbot.app import backend_commands

    observed = []
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backend_commands.backend.config, "course_quiz_auto_answer", False)
        monkeypatch.setattr(backend_commands.AI_BRIDGE, "probe", lambda: observed.append("probe"))
        monkeypatch.setattr(backend_commands.backend, "start_course_helper", lambda **kwargs: observed.append(kwargs) or True)
        result = backend_commands.handle("start_course_helper", {})
    assert result == {"ok": True}
    assert observed == [{}]
