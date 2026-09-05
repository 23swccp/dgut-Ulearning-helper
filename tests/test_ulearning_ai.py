import json

import pytest
import requests

from dgutbot.experimental.ulearning_ai import (
    ChatContext,
    UlearningAiClient,
    UlearningAiError,
    new_protocol_id,
    parse_event_stream,
)


class FakeResponse:
    def __init__(self, lines, *, content_type="text/event-stream", error=None):
        self.lines = lines
        self.headers = {"Content-Type": content_type}
        self.error = error
        self.closed = False

    def raise_for_status(self):
        if self.error:
            raise self.error

    def iter_lines(self):
        return iter(self.lines)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_parse_event_stream_handles_text_reasoning_and_done():
    lines = [
        b'data: {"data":"hello","reasoningContent":"","toolCalls":[{"id":"call-1"}]}',
        ': heartbeat',
        json.dumps({"data": "", "reasoningContent": "think"}),
        "data: [DONE]",
    ]
    chunks = list(parse_event_stream(lines))
    assert [(item.text, item.reasoning, item.done) for item in chunks] == [
        ("hello", "", False),
        ("", "think", False),
        ("", "", True),
    ]
    assert chunks[0].tool_calls == ({"id": "call-1"},)


def test_client_uses_observed_contract_and_closes_response():
    response = FakeResponse([b'data: {"data":"ok","reasoningContent":""}'])
    session = FakeSession(response)
    client = UlearningAiClient(session, base_url="https://aijx.example")
    chunks = list(client.stream_chat(
        ChatContext("1234", "654321", "123456789012345"),
        request_id="12345678901234",
        query="probe",
    ))
    assert chunks[0].text == "ok"
    url, call = session.calls[0]
    assert url == "https://aijx.example/api/kbChat/chat"
    assert call["params"] == {
        "sessionId": "123456789012345",
        "assistantId": "1234",
        "requestId": "12345678901234",
        "courseId": "654321",
        "modelId": 1,
        "sessionSign": 1,
        "instructionId": 0,
        "askType": 1,
        "num": 0,
        "thinking": "disabled",
        "online": 0,
    }
    assert call["json"] == {"query": "probe", "files": [], "toolsContentDTOS": None, "remarks": ""}
    assert call["headers"] == {"Accept": "text/event-stream", "Origin": "https://aijx.example"}
    assert call["stream"] is True
    assert response.closed is True


def test_client_errors_do_not_include_credentials():
    response = FakeResponse([], error=requests.HTTPError("Authorization: very-secret"))
    client = UlearningAiClient(FakeSession(response))
    with pytest.raises(UlearningAiError) as caught:
        list(client.stream_chat(ChatContext("1", "2", "3"), request_id="4", query="probe"))
    assert "very-secret" not in str(caught.value)


def test_context_and_request_ids_are_safe_opaque_values():
    with pytest.raises(ValueError):
        ChatContext("assistant?token=secret", "2", "3")
    client = UlearningAiClient(FakeSession(FakeResponse([])))
    with pytest.raises(ValueError):
        list(client.stream_chat(ChatContext("assistant-v2", "course_2", "session.3"), request_id="bad/id", query="probe"))


def test_client_closes_response_when_status_check_fails():
    response = FakeResponse([], error=requests.HTTPError("server error"))
    client = UlearningAiClient(FakeSession(response))
    with pytest.raises(UlearningAiError):
        list(client.stream_chat(ChatContext("1", "2", "3"), request_id="4", query="probe"))
    assert response.closed is True


def test_protocol_id_matches_observed_web_generator_shape():
    assert new_protocol_id(timestamp_ms=1_000, random_fraction=0) == "1000"
    assert new_protocol_id(timestamp_ms=1_000, random_fraction=0.5) == "51000"
