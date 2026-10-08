from unittest.mock import MagicMock, patch
import os
import urllib.error
import pytest

from hydra_cli.auth import (
    PROVIDER_DIRECTORY,
    auth_wizard,
    execute_auth_command,
    get_auth_status,
    get_masked_input,
    mask_secret,
    open_registration_page,
    print_auth_status,
    probe_credential,
    save_credentials,
)


def test_mask_secret_zero_leakage():
    assert mask_secret("") == "[NOT SET]"
    assert mask_secret("   ") == "[NOT SET]"
    assert mask_secret(None) == "[NOT SET]"
    assert mask_secret("short") == "********"
    assert mask_secret("12345678") == "********"

    token = "sk-or-v1-abcdef0123456789"
    masked = mask_secret(token)
    assert masked == "sk-o...6789"
    assert "abcdef0123" not in masked


def test_get_masked_input():
    with patch("getpass.getpass", return_value="secret_pass_123"):
        val = get_masked_input("Enter key: ")
        assert val == "secret_pass_123"

    with patch("getpass.getpass", side_effect=Exception("No TTY")), \
         patch("builtins.input", return_value="fallback_pass_456"):
        val = get_masked_input("Enter key: ")
        assert val == "fallback_pass_456"


def test_save_credentials_atomic(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    initial_content = "# System Config\nEXISTING_VAR=123\n"
    env_file.write_text(initial_content, encoding="utf-8")

    saved_path = save_credentials({"OPENROUTER_API_KEY": "sk-or-test-key-999"}, env_path=str(env_file))
    assert saved_path == str(env_file)
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-test-key-999"

    read_back = env_file.read_text(encoding="utf-8")
    assert "EXISTING_VAR=123" in read_back
    assert "OPENROUTER_API_KEY=sk-or-test-key-999" in read_back

    # Update existing and add another key
    save_credentials(
        {"OPENROUTER_API_KEY": "sk-or-updated-key-000", "HF_TOKEN": "hf_testtoken_111"},
        env_path=str(env_file),
    )
    read_back_2 = env_file.read_text(encoding="utf-8")
    assert "OPENROUTER_API_KEY=sk-or-updated-key-000" in read_back_2
    assert "HF_TOKEN=hf_testtoken_111" in read_back_2
    assert read_back_2.count("OPENROUTER_API_KEY") == 1


def test_get_auth_status_and_print(tmp_path, monkeypatch, capsys):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=sk-or-real-looking-key-123456\n"
        "CLOUDFLARE_API_TOKEN=cf-token-sample-12345\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)

    status = get_auth_status(env_path=str(env_file))
    assert "openrouter" in status
    assert status["openrouter"]["configured"] is True
    assert "keys" in status["openrouter"]
    assert status["openrouter"]["keys"]["OPENROUTER_API_KEY"]["masked"] == "sk-o...3456"

    # Cloudflare is incomplete without account id
    assert status["cloudflare"]["configured"] is False

    # Print status and verify zero secret token leakage
    print_auth_status(env_path=str(env_file))
    out = capsys.readouterr().out
    assert "OpenRouter" in out
    assert "[CONFIGURED]" in out
    assert "real-looking-key" not in out
    assert "sk-o...3456" in out


def test_probe_credential_validation():
    # Empty credential fails
    ok, msg = probe_credential("openrouter", "")
    assert ok is False

    # Too short fails
    ok, msg = probe_credential("openrouter", "short")
    assert ok is False
    assert "too short" in msg

    # Valid format with network probe mock (200 OK)
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, msg = probe_credential("openrouter", "sk-or-v1-1234567890abcdef")
        assert ok is True
        assert "verified" in msg

    # Probe 401 Unauthorized
    http_err = urllib.error.HTTPError(
        url="https://openrouter.ai",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_err):
        ok, msg = probe_credential("openrouter", "sk-or-v1-1234567890abcdef")
        assert ok is False
        assert "Unauthorized" in msg

    # Modal endpoint validation
    assert probe_credential("modal", "not-a-url")[0] is False
    assert probe_credential("modal", "https://app.modal.run/v1")[0] is True


def test_open_registration_page():
    with patch("webbrowser.open", return_value=True) as mock_open:
        ok = open_registration_page("openrouter")
        assert ok is True
        mock_open.assert_called_once_with(PROVIDER_DIRECTORY["openrouter"]["registration_url"])

    assert open_registration_page("unknown_provider") is False


def test_execute_auth_command(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("hydra_cli.auth.hydra_home", lambda: str(tmp_path))

    # 1. status
    ret = execute_auth_command(["status"])
    assert ret == 0
    assert "Hydra Model Provider Credentials" in capsys.readouterr().out

    # 2. set
    ret = execute_auth_command(["set", "OPENROUTER_API_KEY", "sk-or-my-new-token-123456"])
    assert ret == 0
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-my-new-token-123456"

    # 3. test with mock
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ret = execute_auth_command(["test", "openrouter"])
        assert ret == 0
        assert "[OK]" in capsys.readouterr().out
