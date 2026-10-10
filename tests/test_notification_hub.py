"""
Integration test suite for Hydra Desktop Notification Hub.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import concurrent.futures
import pytest

from desktop.notification_hub import (
    NotificationHub,
    Notification,
    NotificationItem,
    NotificationPriority,
    NotificationType,
    parse_priority,
    parse_notification_type,
    get_notification_hub,
    reset_notification_hub,
)


@pytest.fixture
def hub():
    h = NotificationHub()
    yield h
    h.clear()


def test_notification_creation_and_serialization():
    """Verify notification item fields, priority parsing, and dictionary serialization."""
    notif = Notification(
        title="Agent Task Completed",
        message="Wave 8 verification passed exit status 0",
        priority=NotificationPriority.HIGH,
        notification_type=NotificationType.SUCCESS,
        source="agent_runner",
        action_url="hydra://workspaces/primary",
        metadata={"task_id": "wave_008"},
    )

    assert notif.title == "Agent Task Completed"
    assert notif.message == "Wave 8 verification passed exit status 0"
    assert notif.priority == NotificationPriority.HIGH
    assert notif.notification_type == NotificationType.SUCCESS
    assert notif.read is False
    assert notif.dismissed is False
    assert notif.source == "agent_runner"

    d = notif.to_dict()
    assert d["title"] == "Agent Task Completed"
    assert d["priority"] == "high"
    assert d["priority_value"] == 30
    assert d["type"] == "success"
    assert d["action_url"] == "hydra://workspaces/primary"
    assert d["metadata"]["task_id"] == "wave_008"

    # Verify alias
    assert NotificationItem is Notification


def test_priority_and_type_parsing_helpers():
    """Verify parsing robustness across strings, ints, and enum instances."""
    assert parse_priority(NotificationPriority.CRITICAL) == NotificationPriority.CRITICAL
    assert parse_priority("urgent") == NotificationPriority.URGENT
    assert parse_priority("HIGH") == NotificationPriority.HIGH
    assert parse_priority("standard") == NotificationPriority.NORMAL
    assert parse_priority("low") == NotificationPriority.LOW
    assert parse_priority(50) == NotificationPriority.CRITICAL
    assert parse_priority("unknown_priority") == NotificationPriority.NORMAL

    assert parse_notification_type(NotificationType.ERROR) == NotificationType.ERROR
    assert parse_notification_type("warning") == NotificationType.WARNING
    assert parse_notification_type("agent") == NotificationType.AGENT
    assert parse_notification_type("invalid_type") == NotificationType.INFO


def test_priority_alert_queueing_and_ordered_dispatch(hub: NotificationHub):
    """Verify alerts are prioritized correctly and popped in highest-priority order."""
    # Publish out of order: LOW, CRITICAL, NORMAL, URGENT, HIGH
    hub.publish("Low alert", "System routine check", priority=NotificationPriority.LOW)
    hub.publish("Critical alert", "Memory exhaustion detected", priority=NotificationPriority.CRITICAL)
    hub.publish("Normal alert", "New model catalog loaded", priority=NotificationPriority.NORMAL)
    hub.publish("Urgent alert", "Tool execution timeout", priority=NotificationPriority.URGENT)
    hub.publish("High alert", "API retry limit reached", priority=NotificationPriority.HIGH)

    assert hub.total_notifications == 5
    assert hub.pending_queue_count == 5

    # Peek highest priority should be CRITICAL
    peeked = hub.peek_highest_priority()
    assert peeked is not None
    assert peeked.title == "Critical alert"
    assert peeked.priority == NotificationPriority.CRITICAL

    # Pop in strict priority order: CRITICAL (50), URGENT (40), HIGH (30), NORMAL (20), LOW (10)
    p1 = hub.pop_highest_priority()
    assert p1 is not None and p1.title == "Critical alert"

    p2 = hub.pop_highest_priority()
    assert p2 is not None and p2.title == "Urgent alert"

    p3 = hub.pop_highest_priority()
    assert p3 is not None and p3.title == "High alert"

    p4 = hub.pop_highest_priority()
    assert p4 is not None and p4.title == "Normal alert"

    p5 = hub.pop_highest_priority()
    assert p5 is not None and p5.title == "Low alert"

    # Queue should be empty now
    assert hub.pop_highest_priority() is None


def test_notification_publish_subscribers_broadcast(hub: NotificationHub):
    """Verify live listener subscription and message broadcasts."""
    received = []

    def on_notif(n: Notification):
        received.append(n)

    sub_id = hub.subscribe(on_notif)
    assert sub_id.startswith("sub_")

    hub.publish("Build Status", "Desktop dist built successfully", priority="high", notification_type="success")
    hub.publish("Test Runner", "All 125 test suites passed", priority="normal", notification_type="info")

    assert len(received) == 2
    assert received[0].title == "Build Status"
    assert received[1].title == "Test Runner"

    # Unsubscribe and verify no further broadcasts received
    assert hub.unsubscribe(sub_id) is True
    hub.publish("Third Event", "Should not trigger unsubscribed callback")
    assert len(received) == 2


def test_tray_balloon_popup_events(hub: NotificationHub):
    """Verify system tray balloon dispatch and recording."""
    assert len(hub.balloon_events) == 0

    hub.send_tray_balloon("Desktop Alert", "Computer use coordinate safety latch", timeout_sec=3, icon_type="warning")
    assert len(hub.balloon_events) == 1
    event = hub.balloon_events[0]
    assert event["title"] == "Desktop Alert"
    assert event["message"] == "Computer use coordinate safety latch"
    assert event["timeout_sec"] == 3
    assert event["icon_type"] == "warning"

    # Urgent and critical notifications automatically trigger balloons
    hub.publish("Emergency Halt", "Critical fence violation", priority=NotificationPriority.CRITICAL)
    assert len(hub.balloon_events) == 2
    assert hub.balloon_events[1]["title"] == "Emergency Halt"


def test_notification_callback_registration_and_trigger(hub: NotificationHub):
    """Verify action callback registration, invocation, and payload delivery."""
    executed_payloads = []

    def handle_restart(notif: Notification, payload: dict):
        executed_payloads.append((notif.title, payload.get("action")))
        return {"status": "restarting", "target": payload.get("target")}

    hub.register_callback("restart_agent", handle_restart)

    notif = hub.publish(
        "Agent Halted",
        "Agent encountered deadlock",
        callback_id="restart_agent",
    )

    result = hub.trigger_callback(notif.notification_id, {"action": "force_restart", "target": "open_worker_1"})
    assert len(executed_payloads) == 1
    assert executed_payloads[0] == ("Agent Halted", "force_restart")
    assert result["status"] == "restarting"
    assert result["target"] == "open_worker_1"

    # Inline callback publish
    inline_log = []
    inline_notif = hub.publish(
        "Inline Event",
        "Inline test message",
        callback=lambda n, p: inline_log.append(p.get("step")) or "done",
    )
    res_inline = hub.trigger_callback(inline_notif.notification_id, {"step": "step_one"})
    assert inline_log == ["step_one"]
    assert res_inline == "done"


def test_read_tracking_and_filtering(hub: NotificationHub):
    """Verify unread tracking, mark as read, dismiss, and priority filtering."""
    n1 = hub.publish("N1", "Message 1", priority="low")
    n2 = hub.publish("N2", "Message 2", priority="normal")
    n3 = hub.publish("N3", "Message 3", priority="high")

    assert hub.unread_count == 3

    # Mark individual as read
    assert hub.mark_as_read(n1.notification_id) is True
    assert hub.unread_count == 2

    # Dismiss notification
    assert hub.dismiss(n2.notification_id) is True
    assert hub.unread_count == 1
    assert hub.get_notification(n2.notification_id).dismissed is True

    # List unread only
    unread = hub.list_notifications(unread_only=True)
    assert len(unread) == 1
    assert unread[0].notification_id == n3.notification_id

    # Filter by minimum priority (HIGH = 30)
    high_items = hub.list_notifications(min_priority=NotificationPriority.HIGH)
    assert len(high_items) == 1
    assert high_items[0].notification_id == n3.notification_id

    # Mark all read
    updated = hub.mark_all_read()
    assert updated == 1
    assert hub.unread_count == 0


def test_concurrent_notification_publishing_thread_safety(hub: NotificationHub):
    """Verify thread-safe multi-threaded publishing without data races."""
    def worker(worker_id: int):
        for i in range(20):
            p = NotificationPriority.HIGH if (i % 2 == 0) else NotificationPriority.LOW
            hub.publish(
                f"Worker {worker_id} Event {i}",
                f"Payload from thread {worker_id}",
                priority=p,
            )
        return worker_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        for f in concurrent.futures.as_completed(futures):
            assert f.result() >= 0

    assert hub.total_notifications == 5 * 20
    assert hub.pending_queue_count == 5 * 20

    # Pop all elements and verify order monotonically non-increasing in priority value
    prev_priority = 999
    count = 0
    while True:
        item = hub.pop_highest_priority()
        if item is None:
            break
        assert int(item.priority.value) <= prev_priority
        prev_priority = int(item.priority.value)
        count += 1

    assert count == 100


def test_global_singleton_notification_hub():
    """Verify singleton lifecycle and state resets."""
    h1 = get_notification_hub()
    h2 = get_notification_hub()
    assert h1 is h2

    h1.publish("Singleton Test", "Testing singleton instance")
    assert h2.total_notifications == 1

    h3 = reset_notification_hub()
    assert h3 is not h1
    assert h3.total_notifications == 0
