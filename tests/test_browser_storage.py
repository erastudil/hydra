"""
Integration test suite for Hydra Desktop Browser Storage and Cookie Persistence.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import tempfile
import time
import pytest

from desktop.browser_storage import BrowserCookie, BrowserStorageManager


@pytest.fixture
def storage():
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage_file = os.path.join(tmp_dir, "browser_storage.json")
        mgr = BrowserStorageManager(storage_path=storage_file)
        yield mgr
        mgr.clear_all()


def test_browser_cookie_validation_and_token_safety():
    """Verify cookie token validation, header injection defense, and attribute defaults."""
    # Valid cookie
    c = BrowserCookie(
        name="session_token",
        value="alpha_numeric_secret_123",
        domain="app.hydra.local",
        path="/",
        secure=True,
        http_only=True,
        same_site="Strict",
    )
    assert c.name == "session_token"
    assert c.value == "alpha_numeric_secret_123"
    assert c.domain == "app.hydra.local"
    assert c.path == "/"
    assert c.secure is True
    assert c.http_only is True
    assert c.same_site == "Strict"

    # Rejection of invalid characters (null bytes, newlines, semicolons)
    with pytest.raises(ValueError):
        BrowserCookie(name="bad\nname", value="val", domain="example.com")

    with pytest.raises(ValueError):
        BrowserCookie(name="name", value="bad\rval", domain="example.com")

    with pytest.raises(ValueError):
        BrowserCookie(name="name", value="val\0injected", domain="example.com")

    with pytest.raises(ValueError):
        BrowserCookie(name="name;injected=1", value="val", domain="example.com")

    with pytest.raises(ValueError):
        BrowserCookie(name="", value="val", domain="example.com")

    with pytest.raises(ValueError):
        BrowserCookie(name="name", value="val", domain="")


def test_browser_cookie_domain_and_path_matching():
    """Verify URL matching against domain, path, and secure flag."""
    c_sub = BrowserCookie("auth", "val", domain=".hydra.local", path="/api", secure=True)

    # Subdomain match on HTTPS
    assert c_sub.matches_url("https://console.hydra.local/api/v1/status") is True
    assert c_sub.matches_url("https://hydra.local/api/v1") is True

    # Path mismatch
    assert c_sub.matches_url("https://console.hydra.local/dashboard") is False

    # Insecure HTTP rejected for secure cookie
    assert c_sub.matches_url("http://console.hydra.local/api/v1") is False

    # Domain mismatch
    assert c_sub.matches_url("https://attacker.org/api") is False


def test_browser_cookie_expiration_and_retrieval(storage: BrowserStorageManager):
    """Verify expiration timestamp evaluation and active cookie filtering."""
    now = time.time()
    valid_c = BrowserCookie("active_user", "usr_123", domain="hydra.local", expires=now + 3600)
    expired_c = BrowserCookie("old_session", "exp_456", domain="hydra.local", expires=now - 100)

    storage.set_cookie(valid_c)
    storage.set_cookie(expired_c)

    # Standard retrieval filters expired
    assert storage.get_cookie("active_user", "hydra.local") is not None
    assert storage.get_cookie("old_session", "hydra.local") is None

    # Query with include_expired
    all_cookies = storage.get_cookies(domain="hydra.local", include_expired=True)
    assert len(all_cookies) == 2

    active_only = storage.get_cookies(domain="hydra.local", include_expired=False)
    assert len(active_only) == 1
    assert active_only[0].name == "active_user"


def test_local_storage_and_session_storage_origin_isolation(storage: BrowserStorageManager):
    """Verify origin boundary isolation between LocalStorage and SessionStorage."""
    origin_a = "https://app.hydra.local"
    origin_b = "https://api.hydra.local"

    storage.set_local_item(origin_a, "theme", "dark")
    storage.set_local_item(origin_a, "zoom", "1.2")
    storage.set_local_item(origin_b, "theme", "light")

    assert storage.get_local_item(origin_a, "theme") == "dark"
    assert storage.get_local_item(origin_b, "theme") == "light"
    assert storage.get_local_item(origin_b, "zoom") is None

    # Clear origin A only
    storage.clear_local_storage(origin_a)
    assert storage.get_local_item(origin_a, "theme") is None
    assert storage.get_local_item(origin_b, "theme") == "light"

    # Session storage
    storage.set_session_item(origin_a, "tab_id", "tab_99")
    assert storage.get_session_item(origin_a, "tab_id") == "tab_99"
    assert storage.get_session_item(origin_b, "tab_id") is None


def test_playwright_storage_state_export_import_roundtrip(storage: BrowserStorageManager):
    """Verify Playwright storageState parity and serialization roundtrip."""
    storage.set_cookie({
        "name": "playwright_session",
        "value": "pw_token_xyz",
        "domain": "portal.local",
        "path": "/",
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    })
    storage.set_local_item("https://portal.local", "user_profile", '{"role": "admin"}')
    storage.set_local_item("https://portal.local", "pref_lang", "en")

    # Export to Playwright format
    state = storage.export_storage_state()
    assert "cookies" in state
    assert "origins" in state
    assert len(state["cookies"]) == 1
    assert state["cookies"][0]["name"] == "playwright_session"
    assert len(state["origins"]) == 1
    assert state["origins"][0]["origin"] == "https://portal.local"

    # Ingest into fresh storage manager
    fresh_storage = BrowserStorageManager()
    count = fresh_storage.import_storage_state(state)
    assert count == 3

    assert fresh_storage.get_cookie("playwright_session", "portal.local") is not None
    assert fresh_storage.get_local_item("https://portal.local", "pref_lang") == "en"


def test_browser_storage_file_persistence_atomic(storage: BrowserStorageManager):
    """Verify atomic disk persistence and state restoration across restarts."""
    storage.set_cookie({"name": "persistent_id", "value": "p_123", "domain": "desk.local"})
    storage.set_local_item("https://desk.local", "saved_key", "saved_value")

    state_path = storage.storage_path
    assert state_path is not None
    saved_file = storage.save_to_file()
    assert os.path.isfile(saved_file)

    # Restoring in clean instance
    loaded_storage = BrowserStorageManager(storage_path=saved_file)
    assert loaded_storage.get_cookie("persistent_id", "desk.local") is not None
    assert loaded_storage.get_local_item("https://desk.local", "saved_key") == "saved_value"


def test_browser_storage_cookie_deletion_and_clear(storage: BrowserStorageManager):
    """Verify fine-grained cookie deletion and bulk clearance."""
    storage.set_cookie({"name": "c1", "value": "v1", "domain": "a.org"})
    storage.set_cookie({"name": "c2", "value": "v2", "domain": "a.org"})
    storage.set_cookie({"name": "c3", "value": "v3", "domain": "b.org"})

    assert storage.delete_cookie("c1", "a.org") is True
    assert storage.delete_cookie("c1", "a.org") is False
    assert len(storage.get_cookies(domain="a.org")) == 1

    # Bulk clear for a.org
    cleared = storage.clear_cookies("a.org")
    assert cleared == 1
    assert len(storage.get_cookies(domain="a.org")) == 0
    assert len(storage.get_cookies(domain="b.org")) == 1


def test_concurrent_browser_storage_access(storage: BrowserStorageManager):
    """Verify thread-safe multi-threaded read/write storage operations."""
    def worker(worker_id: int):
        domain = f"worker_{worker_id}.local"
        origin = f"https://{domain}"
        for i in range(20):
            storage.set_cookie({"name": f"ck_{i}", "value": f"v_{worker_id}_{i}", "domain": domain})
            storage.set_local_item(origin, f"key_{i}", f"val_{worker_id}_{i}")
            storage.get_cookie(f"ck_{i}", domain)
            storage.get_local_item(origin, f"key_{i}")
        return worker_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() >= 0

    all_cookies = storage.get_cookies(include_expired=True)
    assert len(all_cookies) == 8 * 20
