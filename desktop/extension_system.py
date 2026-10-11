"""
Hydra Desktop Extension Plugin System.
Provides extension manifests, lifecycle hooks (on_init, on_tool_call, on_render),
capability gating, and sandboxed execution context.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Type, Union


class ExtensionCapability(str, Enum):
    """Declared capabilities required by extensions."""
    NETWORK = "network"
    WORKSPACE_READ = "workspace:read"
    WORKSPACE_WRITE = "workspace:write"
    TERMINAL_EXEC = "terminal:exec"
    COMPUTER_USE = "computer_use"
    TOOL_INTERCEPT = "tool:intercept"
    UI_RENDER = "ui:render"
    THEME_MODIFY = "theme:modify"


class ExtensionLifecycleState(str, Enum):
    """Extension state machine lifecycle phases."""
    REGISTERED = "registered"
    INITIALIZED = "initialized"
    ACTIVE = "active"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class ExtensionManifest:
    """Extension metadata and capability declaration manifest."""
    extension_id: str
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    entrypoint: Optional[str] = None
    capabilities: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.extension_id

    def has_capability(self, capability: Union[str, ExtensionCapability]) -> bool:
        cap_str = capability.value if isinstance(capability, ExtensionCapability) else str(capability).strip()
        return cap_str in self.capabilities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "extension_id": self.extension_id,
            "id": self.extension_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "entrypoint": self.entrypoint,
            "capabilities": list(self.capabilities),
            "permissions": list(self.permissions),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExtensionManifest:
        eid = data.get("extension_id") or data.get("id") or "unnamed_ext"
        caps = [c.value if isinstance(c, ExtensionCapability) else str(c) for c in data.get("capabilities", [])]
        return cls(
            extension_id=eid,
            name=data.get("name", eid),
            version=data.get("version", "1.0.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            entrypoint=data.get("entrypoint"),
            capabilities=caps,
            permissions=list(data.get("permissions", [])),
            metadata=dict(data.get("metadata", {})),
        )


class ExtensionContext:
    """Bounded, capability-gated runtime execution context for an extension."""

    def __init__(
        self,
        manifest: ExtensionManifest,
        workspace_root: Optional[str] = None,
        event_sink: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.manifest = manifest
        self.workspace_root = workspace_root
        self._event_sink = event_sink
        self.granted_capabilities: Set[str] = set(manifest.capabilities)

    def has_capability(self, capability: Union[str, ExtensionCapability]) -> bool:
        cap_str = capability.value if isinstance(capability, ExtensionCapability) else str(capability).strip()
        return cap_str in self.granted_capabilities

    def assert_capability(self, capability: Union[str, ExtensionCapability]) -> None:
        cap_str = capability.value if isinstance(capability, ExtensionCapability) else str(capability).strip()
        if not self.has_capability(cap_str):
            raise PermissionError(
                f"Security violation: Extension '{self.manifest.extension_id}' lacking capability '{cap_str}'"
            )

    def emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        if self._event_sink:
            self._event_sink(event_type, data)


class BaseExtension(ABC):
    """Abstract base contract for Hydra Desktop extensions."""

    def __init__(self, manifest: ExtensionManifest) -> None:
        self.manifest = manifest
        self.state = ExtensionLifecycleState.REGISTERED
        self.context: Optional[ExtensionContext] = None
        self.last_error: Optional[str] = None

    @property
    def id(self) -> str:
        return self.manifest.extension_id

    @property
    def is_active(self) -> bool:
        return self.state == ExtensionLifecycleState.ACTIVE

    def on_init(self, context: ExtensionContext) -> None:
        """Called once when extension is initialized."""
        pass

    def on_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Intercept or modify tool calls if granted TOOL_INTERCEPT capability.
        Returns modified args dictionary, or None to pass through unmodified.
        """
        return None

    def on_render(self, render_target: str, data: Dict[str, Any]) -> Optional[str]:
        """
        Generate UI components or HTML snippets if granted UI_RENDER capability.
        Returns HTML/CSS string or None.
        """
        return None

    def on_unload(self) -> None:
        """Called when extension is disabled or unloaded."""
        pass

    def on_error(self, error: Exception) -> None:
        """Called on unhandled execution error."""
        self.last_error = str(error)
        self.state = ExtensionLifecycleState.ERROR


class ExtensionManager:
    """
    Sovereign Extension Manager for Hydra Desktop.
    Handles manifest loading, lifecycle states, capability gating, and hook dispatching.
    """

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = workspace_root
        self._lock = threading.RLock()
        self._extensions: Dict[str, BaseExtension] = {}
        self._events: List[Dict[str, Any]] = []

    @property
    def total_extensions(self) -> int:
        with self._lock:
            return len(self._extensions)

    @property
    def active_extensions(self) -> int:
        with self._lock:
            return sum(1 for e in self._extensions.values() if e.is_active)

    def clear(self) -> None:
        """Unload and clear all registered extensions."""
        with self._lock:
            for ext in list(self._extensions.values()):
                try:
                    ext.on_unload()
                except Exception:
                    pass
            self._extensions.clear()
            self._events.clear()

    def register_extension(self, extension: Union[BaseExtension, Dict[str, Any]]) -> BaseExtension:
        """Register an extension instance or construct a basic extension from manifest dict."""
        if isinstance(extension, BaseExtension):
            ext = extension
        elif isinstance(extension, dict):
            manifest = ExtensionManifest.from_dict(extension)
            # Create standard generic extension instance
            class GenericExtension(BaseExtension):
                pass
            ext = GenericExtension(manifest)
        else:
            raise TypeError(f"Expected BaseExtension or dict, got {type(extension)}")

        with self._lock:
            self._extensions[ext.id] = ext
            ext.state = ExtensionLifecycleState.REGISTERED

        # Automatically initialize and activate
        self.initialize_extension(ext.id)
        self.enable_extension(ext.id)
        return ext

    def unregister_extension(self, extension_id: str) -> bool:
        """Unload and remove extension by ID."""
        with self._lock:
            ext = self._extensions.pop(extension_id, None)
            if ext:
                try:
                    ext.on_unload()
                except Exception:
                    pass
                ext.state = ExtensionLifecycleState.DISABLED
                return True
        return False

    def get_extension(self, extension_id: str) -> Optional[BaseExtension]:
        """Fetch extension by ID."""
        with self._lock:
            return self._extensions.get(extension_id)

    def list_extensions(self, enabled_only: bool = False) -> List[BaseExtension]:
        """List registered extensions."""
        with self._lock:
            exts = list(self._extensions.values())
        if enabled_only:
            exts = [e for e in exts if e.is_active]
        return exts

    def initialize_extension(self, extension_id: str) -> bool:
        """Run on_init hook with sandboxed context."""
        with self._lock:
            ext = self._extensions.get(extension_id)
            if not ext:
                return False

            ctx = ExtensionContext(
                manifest=ext.manifest,
                workspace_root=self.workspace_root,
                event_sink=lambda ev, d: self._record_event(ext.id, ev, d),
            )
            ext.context = ctx

            try:
                ext.on_init(ctx)
                ext.state = ExtensionLifecycleState.INITIALIZED
                return True
            except Exception as exc:
                ext.on_error(exc)
                return False

    def enable_extension(self, extension_id: str) -> bool:
        """Activate extension."""
        with self._lock:
            ext = self._extensions.get(extension_id)
            if not ext:
                return False
            ext.state = ExtensionLifecycleState.ACTIVE
            return True

    def disable_extension(self, extension_id: str) -> bool:
        """Deactivate extension."""
        with self._lock:
            ext = self._extensions.get(extension_id)
            if not ext:
                return False
            try:
                ext.on_unload()
            except Exception:
                pass
            ext.state = ExtensionLifecycleState.DISABLED
            return True

    def dispatch_tool_call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Dispatch tool call through active extensions with TOOL_INTERCEPT capability.
        Allows extensions to sequentially filter or transform parameters.
        """
        current_args = dict(args)

        with self._lock:
            candidates = [
                e for e in self._extensions.values()
                if e.is_active and e.manifest.has_capability(ExtensionCapability.TOOL_INTERCEPT)
            ]

        for ext in candidates:
            try:
                if ext.context:
                    ext.context.assert_capability(ExtensionCapability.TOOL_INTERCEPT)
                modified = ext.on_tool_call(tool_name, current_args)
                if isinstance(modified, dict):
                    current_args = modified
            except Exception as exc:
                ext.on_error(exc)

        return current_args

    def dispatch_render(self, render_target: str, data: Dict[str, Any]) -> List[str]:
        """
        Dispatch render requests through active extensions with UI_RENDER capability.
        Returns list of rendered HTML/CSS component strings.
        """
        rendered_snippets: List[str] = []

        with self._lock:
            candidates = [
                e for e in self._extensions.values()
                if e.is_active and e.manifest.has_capability(ExtensionCapability.UI_RENDER)
            ]

        for ext in candidates:
            try:
                if ext.context:
                    ext.context.assert_capability(ExtensionCapability.UI_RENDER)
                output = ext.on_render(render_target, data)
                if output and isinstance(output, str):
                    rendered_snippets.append(output)
            except Exception as exc:
                ext.on_error(exc)

        return rendered_snippets

    def _record_event(self, extension_id: str, event_type: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._events.append({
                "extension_id": extension_id,
                "event": event_type,
                "data": data,
                "timestamp": uuid.uuid4().hex,
            })


_GLOBAL_EXTENSION_MANAGER: Optional[ExtensionManager] = None
_GLOBAL_EXT_LOCK = threading.RLock()


def get_extension_manager() -> ExtensionManager:
    """Acquire thread-safe singleton ExtensionManager."""
    global _GLOBAL_EXTENSION_MANAGER
    with _GLOBAL_EXT_LOCK:
        if _GLOBAL_EXTENSION_MANAGER is None:
            _GLOBAL_EXTENSION_MANAGER = ExtensionManager()
        return _GLOBAL_EXTENSION_MANAGER


def reset_extension_manager() -> ExtensionManager:
    """Reset singleton ExtensionManager."""
    global _GLOBAL_EXTENSION_MANAGER
    with _GLOBAL_EXT_LOCK:
        _GLOBAL_EXTENSION_MANAGER = ExtensionManager()
        return _GLOBAL_EXTENSION_MANAGER
