from io import BytesIO
from unittest.mock import MagicMock, patch
import json
import pytest

from hydra_cli.providers import (
    ProviderError,
    fetch_chat_completion,
    stream_chat_completion,
)


def make_mock_sse_response(chunks):
    lines = []
    for c in chunks:
        payload = json.dumps({"choices": [{"delta": {"content": c}}]})
        lines.append(f"data: {payload}\n\n".encode("utf-8"))
    lines.append(b"data: [DONE]\n\n")
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = lines
    return mock_resp


@patch("urllib.request.urlopen")
def test_stream_chat_completion_success(mock_urlopen):
    mock_urlopen.return_value = make_mock_sse_response(["Hydra", " ", "is", " ", "live."])

    tokens = list(
        stream_chat_completion(
            url="https://api.test/v1/chat/completions",
            headers={"Authorization": "Bearer test"},
            model="test-model",
            messages=[{"role": "user", "content": "hi"}],
        )
    )

    assert tokens == ["Hydra", " ", "is", " ", "live."]


@patch("urllib.request.urlopen")
def test_fetch_chat_completion_success(mock_urlopen):
    response_payload = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "Full response content here."
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    res = fetch_chat_completion(
        url="https://api.test/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
    )

    assert res == "Full response content here."


@patch("urllib.request.urlopen")
def test_stream_error_handling(mock_urlopen):
    import urllib.error
    mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

    with pytest.raises(ProviderError) as exc_info:
        list(
            stream_chat_completion(
                url="https://api.test/v1/chat/completions",
                headers={},
                model="test-model",
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert "Connection failed" in str(exc_info.value)
