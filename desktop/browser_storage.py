"""
Hydra Desktop Browser Storage and Cookie Persistence Subsystem.
Provides cookie serialization, LocalStorage and SessionStorage persistence,
domain and path matching, cross-origin isolation, and Playwright storage state parity.
Complies with AGENTS.md genome invariants: zero copula P018, zero stubs, deterministic verification.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("hydra.desktop.browser_storage")


class BrowserCookie:
    """
    Representation of an HTTP/browser cookie with validation,
    domain matching, path matching, expiration check, and serialization.
    """

    def __init__(
        self,
        name: str,
        value: str,
        domain: str,
        path: str = "/",
        expires: Optional[float] = None,
        http_only: bool = False,
        secure: bool = False,
        same_site: str = "Lax",
    ) -> None:
        self.name: str = self._validate_token(name, "Cookie name")
        self.value: str = self._validate_value(value)
        self.domain: str = self._validate_domain(domain)
        self.path: str = path if path.startswith("/") else "/" + path
        self.expires: Optional[float] = float(expires) if expires is not None else None
        self.http_only: bool = bool(http_only)
        self.secure: bool = bool(secure)

        clean_same_site = str(same_site).capitalize()
        if clean_same_site not in ("Strict", "Lax", "None"):
            clean_same_site = "Lax"
        self.same_site: str = clean_same_site

    @staticmethod
    def _validate_token(token: str, field_name: str) -> str:
        clean = str(token).strip()
        if not clean:
            raise ValueError(f"{field_name} cannot be empty")
        if any(c in clean for c in "\r\n\0;="):
            raise ValueError(f"Invalid characters detected in {field_name}: '{token}'")
        return clean

    @staticmethod
    def _validate_value(value: str) -> str:
        clean = str(value)
        if any(c in clean for c in "\r\n\0;"):
            raise ValueError(f"Invalid characters detected in cookie value: '{value}'")
        return clean

    @staticmethod
    def _validate_domain(domain: str) -> str:
        clean = str(domain).strip().lower()
        if not clean:
            raise ValueError("Cookie domain cannot be empty")
        if any(c in clean for c in "\r\n\0/:"):
            raise ValueError(f"Invalid characters in cookie domain: '{domain}'")
        return clean

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        """Evaluate whether cookie timestamp exceeds current epoch time."""
        if self.expires is None or self.expires < 0:
            return False
        now = current_time if current_time is not None else time.time()
        return now >= self.expires

    def matches_url(self, url: str) -> bool:
        """
        Evaluate whether this cookie applies to a target URL
        based on domain, path, and secure flag.
        """
        parsed = urllib.parse.urlparse(url)
        req_host = (parsed.hostname or "").lower()
        req_scheme = (parsed.scheme or "").lower()
        req_path = parsed.path or "/"

        if self.secure and req_scheme != "https":
            return False

        # Domain matching
        clean_cookie_domain = self.domain.lstrip(".")
        if req_host != clean_cookie_domain and not req_host.endswith("." + clean_cookie_domain):
            return False

        # Path matching
        if not req_path.startswith(self.path):
            return False

        return True

    def to_dict(self) -> Dict[str, Any]:
        """Convert to standard Playwright cookie format."""
        d: Dict[str, Any] = {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "httpOnly": self.http_only,
            "secure": self.secure,
            "sameSite": self.same_site,
        }
        if self.expires is not None:
            d["expires"] = self.expires
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BrowserCookie":
        """Instantiate BrowserCookie from dictionary with key normalization."""
        name = data.get("name")
        value = data.get("value")
        domain = data.get("domain")

        if name is None or value is None or domain is None:
            raise ValueError(f"Cookie dict must declare 'name', 'value', and 'domain': {data}")

        return cls(
            name=str(name),
            value=str(value),
            domain=str(domain),
            path=data.get("path", "/"),
            expires=data.get("expires"),
            http_only=data.get("httpOnly", data.get("http_only", False)),
            secure=data.get("secure", False),
            same_site=data.get("sameSite", data.get("same_site", "Lax")),
        )


class BrowserStorageManager:
    """
    Thread-safe browser storage persistence manager for cookies,
    HTML5 LocalStorage, and SessionStorage state files.
    """

    def __init__(self, storage_path: Optional[str] = None) -> None:
        self.storage_path: Optional[str] = storage_path
        # Keyed by (domain, path, name)
        self._cookies: Dict[Tuple[str, str, str], BrowserCookie] = {}
        # Keyed by origin -> key -> value
        self._local_storage: Dict[str, Dict[str, str]] = {}
        # Keyed by origin -> key -> value
        self._session_storage: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

        if self.storage_path and os.path.isfile(self.storage_path):
            self.load_from_file(self.storage_path)

    @staticmethod
    def normalize_origin(url_or_origin: str) -> str:
        """Extract canonical origin string (scheme://host[:port])."""
        parsed = urllib.parse.urlparse(url_or_origin)
        scheme = (parsed.scheme or "https").lower()
        netloc = (parsed.netloc or parsed.path).lower()
        if not netloc:
            netloc = "localhost"
        return f"{scheme}://{netloc}"

    # --- Cookie Management ---

    def set_cookie(self, cookie: Union[BrowserCookie, Dict[str, Any]]) -> BrowserCookie:
        """Store or update a validated browser cookie."""
        with self._lock:
            if isinstance(cookie, dict):
                c = BrowserCookie.from_dict(cookie)
            elif isinstance(cookie, BrowserCookie):
                c = cookie
            else:
                raise TypeError(f"Expected BrowserCookie or dict, got {type(cookie)}")

            key = (c.domain.lower(), c.path, c.name)
            self._cookies[key] = c

            if self.storage_path:
                self.save_to_file(self.storage_path)
            return c

    def get_cookie(self, name: str, domain: str, path: str = "/") -> Optional[BrowserCookie]:
        """Retrieve cookie matching exact domain, path, and name."""
        with self._lock:
            key = (domain.lower(), path, name)
            c = self._cookies.get(key)
            if c and not c.is_expired():
                return c
            return None

    def get_cookies(
        self,
        domain: Optional[str] = None,
        url: Optional[str] = None,
        include_expired: bool = False,
    ) -> List[BrowserCookie]:
        """Query cookies matching domain, URL, or all stored cookies."""
        with self._lock:
            results: List[BrowserCookie] = []
            now = time.time()
            clean_domain = domain.lower() if domain else None

            for c in self._cookies.values():
                if not include_expired and c.is_expired(now):
                    continue

                if clean_domain:
                    ck_dom = c.domain.lstrip(".")
                    if clean_domain != ck_dom and not clean_domain.endswith("." + ck_dom):
                        continue

                if url and not c.matches_url(url):
                    continue

                results.append(c)

            return results

    def delete_cookie(self, name: str, domain: str, path: str = "/") -> bool:
        """Delete specific cookie."""
        with self._lock:
            key = (domain.lower(), path, name)
            if key in self._cookies:
                del self._cookies[key]
                if self.storage_path:
                    self.save_to_file(self.storage_path)
                return True
            return False

    def clear_cookies(self, domain: Optional[str] = None) -> int:
        """Clear cookies for designated domain or all cookies if None."""
        with self._lock:
            if domain is None:
                count = len(self._cookies)
                self._cookies.clear()
            else:
                clean_domain = domain.lower()
                keys_to_remove = [k for k in self._cookies if k[0] == clean_domain or k[0].endswith("." + clean_domain)]
                count = len(keys_to_remove)
                for k in keys_to_remove:
                    del self._cookies[k]

            if self.storage_path:
                self.save_to_file(self.storage_path)
            return count

    # --- LocalStorage Management ---

    def set_local_item(self, origin: str, key: str, value: str) -> None:
        """Store key-value item in origin LocalStorage."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            if can_origin not in self._local_storage:
                self._local_storage[can_origin] = {}
            self._local_storage[can_origin][str(key)] = str(value)
            if self.storage_path:
                self.save_to_file(self.storage_path)

    def get_local_item(self, origin: str, key: str) -> Optional[str]:
        """Retrieve key-value item from origin LocalStorage."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            return self._local_storage.get(can_origin, {}).get(str(key))

    def remove_local_item(self, origin: str, key: str) -> bool:
        """Remove item from origin LocalStorage."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            bucket = self._local_storage.get(can_origin)
            if bucket and str(key) in bucket:
                del bucket[str(key)]
                if self.storage_path:
                    self.save_to_file(self.storage_path)
                return True
            return False

    def get_local_storage(self, origin: str) -> Dict[str, str]:
        """Retrieve complete LocalStorage snapshot for origin."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            return dict(self._local_storage.get(can_origin, {}))

    def clear_local_storage(self, origin: Optional[str] = None) -> int:
        """Clear LocalStorage for specific origin or all origins."""
        with self._lock:
            if origin is None:
                count = sum(len(v) for v in self._local_storage.values())
                self._local_storage.clear()
            else:
                can_origin = self.normalize_origin(origin)
                count = len(self._local_storage.get(can_origin, {}))
                self._local_storage.pop(can_origin, None)

            if self.storage_path:
                self.save_to_file(self.storage_path)
            return count

    # --- SessionStorage Management ---

    def set_session_item(self, origin: str, key: str, value: str) -> None:
        """Store key-value item in origin SessionStorage."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            if can_origin not in self._session_storage:
                self._session_storage[can_origin] = {}
            self._session_storage[can_origin][str(key)] = str(value)

    def get_session_item(self, origin: str, key: str) -> Optional[str]:
        """Retrieve item from origin SessionStorage."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            return self._session_storage.get(can_origin, {}).get(str(key))

    def get_session_storage(self, origin: str) -> Dict[str, str]:
        """Retrieve complete SessionStorage snapshot for origin."""
        with self._lock:
            can_origin = self.normalize_origin(origin)
            return dict(self._session_storage.get(can_origin, {}))

    def clear_session_storage(self, origin: Optional[str] = None) -> int:
        """Clear SessionStorage for origin or all origins."""
        with self._lock:
            if origin is None:
                count = sum(len(v) for v in self._session_storage.values())
                self._session_storage.clear()
            else:
                can_origin = self.normalize_origin(origin)
                count = len(self._session_storage.get(can_origin, {}))
                self._session_storage.pop(can_origin, None)
            return count

    # --- Serialization and Playwright State Parity ---

    def export_storage_state(self) -> Dict[str, Any]:
        """
        Export standard Playwright-compatible storageState dictionary
        including cookies and origin-partitioned localStorage.
        """
        with self._lock:
            now = time.time()
            cookies_list = [c.to_dict() for c in self._cookies.values() if not c.is_expired(now)]

            origins_list = []
            for origin, storage in self._local_storage.items():
                if storage:
                    entries = [{"name": k, "value": v} for k, v in sorted(storage.items())]
                    origins_list.append({"origin": origin, "localStorage": entries})

            return {
                "cookies": cookies_list,
                "origins": origins_list,
            }

    def import_storage_state(self, state: Dict[str, Any], merge: bool = True) -> int:
        """
        Import Playwright-compatible storageState dictionary.
        Returns total count of imported cookies and storage keys.
        """
        with self._lock:
            if not merge:
                self.clear_all()

            imported_count = 0
            # Import cookies
            for c_data in state.get("cookies", []):
                try:
                    self.set_cookie(c_data)
                    imported_count += 1
                except Exception as err:
                    logger.warning(f"Skipping malformed cookie: {err}")

            # Import origins localStorage
            for origin_data in state.get("origins", []):
                origin = origin_data.get("origin")
                if not origin:
                    continue
                can_origin = self.normalize_origin(origin)
                for item in origin_data.get("localStorage", []):
                    k = item.get("name")
                    v = item.get("value")
                    if k is not None and v is not None:
                        self.set_local_item(can_origin, str(k), str(v))
                        imported_count += 1

            if self.storage_path:
                self.save_to_file(self.storage_path)
            return imported_count

    def save_to_file(self, file_path: Optional[str] = None) -> str:
        """Save storage state atomically to JSON file."""
        with self._lock:
            target = os.path.realpath(os.path.abspath(file_path or self.storage_path or "storage_state.json"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            state_data = self.export_storage_state()

            # Atomic write via temporary file
            tmp_target = f"{target}.tmp.{os.getpid()}"
            with open(tmp_target, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            os.replace(tmp_target, target)
            return target

    def load_from_file(self, file_path: Optional[str] = None) -> int:
        """Load storage state from JSON file."""
        with self._lock:
            target = os.path.realpath(os.path.abspath(file_path or self.storage_path or "storage_state.json"))
            if not os.path.isfile(target):
                raise FileNotFoundError(f"Storage state file not found: {target}")

            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)

            return self.import_storage_state(data, merge=True)

    def clear_all(self) -> None:
        """Wipe all cookies, LocalStorage, and SessionStorage."""
        with self._lock:
            self._cookies.clear()
            self._local_storage.clear()
            self._session_storage.clear()
            if self.storage_path and os.path.isfile(self.storage_path):
                os.remove(self.storage_path)
