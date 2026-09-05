from unittest.mock import patch

import pytest

from dgutbot.experimental.ulearning_ai import UlearningAiError
from dgutbot.experimental.ulearning_ai_browser import (
    BrowserAiAccess,
    _decode_ai_frame,
    _is_dgut_domain,
    discover_browser_access,
)


def test_decode_ai_frame_uses_path_id_and_required_query_values():
    assert _decode_ai_frame(
        "https://aijx.dgut.edu.cn/ai/1234?auth=memory-only&courseId=654321&theme=blue"
    ) == (
        "1234",
        "654321",
        "memory-only",
        "https://aijx.dgut.edu.cn/ai/1234?auth=memory-only&courseId=654321&theme=blue",
    )
    assert _decode_ai_frame("https://aijx.dgut.edu.cn/ai/agentPreview?path=x") is None


def test_access_repr_redacts_authorization_and_cookies():
    access = BrowserAiAccess.__new__(BrowserAiAccess)
    object.__setattr__(access, "context", None)
    object.__setattr__(access, "authorization", "very-secret")
    object.__setattr__(access, "referer", "https://example.test/?auth=referer-secret")
    object.__setattr__(access, "user_agent", "secret-agent")
    object.__setattr__(access, "cookies", ({"name": "AUTH", "value": "cookie-secret"},))
    shown = repr(access)
    assert "very-secret" not in shown
    assert "cookie-secret" not in shown
    assert "referer-secret" not in shown
    assert "secret-agent" not in shown


def test_dgut_cookie_scope_rejects_lookalike_domains():
    assert _is_dgut_domain(".aijx.dgut.edu.cn")
    assert not _is_dgut_domain("dgut.edu.cn.example.test")


def test_discovery_does_not_guess_between_multiple_workbenches():
    targets = [
        {"id": "one", "type": "page", "url": "https://lms.dgut.edu.cn/#/course/workbench"},
        {"id": "two", "type": "page", "url": "https://lms.dgut.edu.cn/#/course/workbench"},
    ]
    with patch("dgutbot.experimental.ulearning_ai_browser._targets", return_value=targets):
        with pytest.raises(UlearningAiError, match="Multiple"):
            discover_browser_access()
