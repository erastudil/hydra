"""
Integration test suite for Hydra Desktop Extension System.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import pytest

from desktop.extension_system import (
    BaseExtension,
    ExtensionCapability,
    ExtensionContext,
    ExtensionLifecycleState,
    ExtensionManager,
    ExtensionManifest,
    get_extension_manager,
    reset_extension_manager,
)


@pytest.fixture
def manager():
    em = ExtensionManager()
    yield em
    em.clear()


class MockGitVisualizerExtension(BaseExtension):
    """Test extension with tool interception and render capabilities."""

    def __init__(self) -> None:
        manifest = ExtensionManifest(
            extension_id="ext.git_visualizer",
            name="Git Visualizer",
            capabilities=[ExtensionCapability.TOOL_INTERCEPT, ExtensionCapability.UI_RENDER],
        )
        super().__init__(manifest)
        self.init_called = False
        self.unloaded = False

    def on_init(self, context: ExtensionContext) -> None:
        self.init_called = True
        context.emit_event("initialized", {"status": "ok"})

    def on_tool_call(self, tool_name: str, args: dict) -> dict | None:
        if tool_name == "git_log":
            modified = dict(args)
            modified["graph"] = True
            return modified
        return None

    def on_render(self, render_target: str, data: dict) -> str | None:
        if render_target == "sidebar":
            return "<div class='git-viz'>Graph Ready</div>"
        return None

    def on_unload(self) -> None:
        self.unloaded = True


class RestrictedExtension(BaseExtension):
    """Test extension lacking intercept capabilities."""

    def __init__(self) -> None:
        manifest = ExtensionManifest(
            extension_id="ext.restricted",
            name="Restricted Extension",
            capabilities=[ExtensionCapability.WORKSPACE_READ],
        )
        super().__init__(manifest)


def test_extension_manifest_parsing_and_capabilities():
    """Verify manifest parsing, capability checks, and serialization."""
    manifest = ExtensionManifest(
        extension_id="ext.sample",
        name="Sample Plugin",
        capabilities=["network", "workspace:read"],
    )
    assert manifest.extension_id == "ext.sample"
    assert manifest.has_capability(ExtensionCapability.NETWORK) is True
    assert manifest.has_capability(ExtensionCapability.WORKSPACE_READ) is True
    assert manifest.has_capability(ExtensionCapability.TERMINAL_EXEC) is False

    d = manifest.to_dict()
    assert d["extension_id"] == "ext.sample"
    assert "network" in d["capabilities"]

    reconstructed = ExtensionManifest.from_dict(d)
    assert reconstructed.extension_id == manifest.extension_id


def test_extension_context_capability_gating():
    """Verify capability assertions raise PermissionError when missing."""
    manifest = ExtensionManifest(
        extension_id="ext.gated",
        name="Gated",
        capabilities=[ExtensionCapability.WORKSPACE_READ],
    )
    ctx = ExtensionContext(manifest)

    # Allowed capability
    ctx.assert_capability(ExtensionCapability.WORKSPACE_READ)

    # Denied capability raises Security Violation
    with pytest.raises(PermissionError) as exc_info:
        ctx.assert_capability(ExtensionCapability.TERMINAL_EXEC)
    assert "Security violation" in str(exc_info.value)
    assert "terminal:exec" in str(exc_info.value)


def test_extension_manager_registration_and_lifecycle(manager: ExtensionManager):
    """Verify extension registration, automatic on_init hook execution, and active count."""
    ext = MockGitVisualizerExtension()
    registered = manager.register_extension(ext)

    assert registered.id == "ext.git_visualizer"
    assert ext.init_called is True
    assert ext.state == ExtensionLifecycleState.ACTIVE
    assert manager.total_extensions == 1
    assert manager.active_extensions == 1

    # Disable extension
    assert manager.disable_extension("ext.git_visualizer") is True
    assert ext.state == ExtensionLifecycleState.DISABLED
    assert manager.active_extensions == 0
    assert ext.unloaded is True

    # Enable extension
    assert manager.enable_extension("ext.git_visualizer") is True
    assert ext.state == ExtensionLifecycleState.ACTIVE
    assert manager.active_extensions == 1


def test_extension_manager_tool_interception_dispatch(manager: ExtensionManager):
    """Verify tool call dispatch transforms parameters via active interceptor."""
    ext = MockGitVisualizerExtension()
    manager.register_extension(ext)

    # Tool call intercepted and augmented
    initial_args = {"repo": "hydra", "limit": 10}
    result_args = manager.dispatch_tool_call("git_log", initial_args)

    assert result_args["repo"] == "hydra"
    assert result_args["limit"] == 10
    assert result_args["graph"] is True

    # Unhandled tool call passes through unmodified
    unmodified_args = manager.dispatch_tool_call("other_tool", {"foo": "bar"})
    assert unmodified_args == {"foo": "bar"}


def test_extension_manager_render_hook_dispatch(manager: ExtensionManager):
    """Verify UI render hook dispatch collects HTML component snippets."""
    ext = MockGitVisualizerExtension()
    manager.register_extension(ext)

    snippets = manager.dispatch_render("sidebar", {"active_tab": "git"})
    assert len(snippets) == 1
    assert "<div class='git-viz'>Graph Ready</div>" in snippets[0]

    # Non-matching render target yields empty list
    empty_snippets = manager.dispatch_render("header", {})
    assert len(empty_snippets) == 0


def test_restricted_extension_bypasses_tool_interception(manager: ExtensionManager):
    """Verify extension lacking TOOL_INTERCEPT capability does not participate in interception."""
    restricted = RestrictedExtension()
    manager.register_extension(restricted)

    raw_args = {"cmd": "status"}
    res = manager.dispatch_tool_call("git_log", raw_args)
    assert res == raw_args


def test_extension_error_isolation(manager: ExtensionManager):
    """Verify extension execution errors are safely isolated into ERROR state."""
    class FaultyExtension(BaseExtension):
        def __init__(self) -> None:
            manifest = ExtensionManifest(
                extension_id="ext.faulty",
                name="Faulty",
                capabilities=[ExtensionCapability.TOOL_INTERCEPT],
            )
            super().__init__(manifest)

        def on_tool_call(self, tool_name: str, args: dict) -> dict | None:
            raise RuntimeError("Faulty plugin crash")

    faulty = FaultyExtension()
    manager.register_extension(faulty)

    # Dispatched tool call catches exception, marks extension state as ERROR, and returns original args
    args = {"test": 123}
    safe_res = manager.dispatch_tool_call("any_tool", args)
    assert safe_res == args
    assert faulty.state == ExtensionLifecycleState.ERROR
    assert "Faulty plugin crash" in str(faulty.last_error)


def test_global_singleton_extension_manager():
    """Verify singleton lifecycle for ExtensionManager."""
    m1 = get_extension_manager()
    m2 = get_extension_manager()
    assert m1 is m2

    m3 = reset_extension_manager()
    assert m3 is not m1
    assert get_extension_manager() is m3
