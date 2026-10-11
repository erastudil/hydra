"""
Notification Hub for Hydra Desktop.
Manages priority-queued notifications, tray balloon popups, unread tracking,
and event callback routing.
Adheres to AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import heapq
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


class NotificationPriority(IntEnum):
    """Notification urgency and dispatch priority."""
    LOW = 10
    NORMAL = 20
    HIGH = 30
    URGENT = 40
    CRITICAL = 50


class NotificationType(str, Enum):
    """Semantic category of notification."""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    AGENT = "agent"
    SYSTEM = "system"


def parse_priority(value: Union[str, int, NotificationPriority]) -> NotificationPriority:
    """Parse string, integer, or enum into NotificationPriority."""
    if isinstance(value, NotificationPriority):
        return value
    if isinstance(value, int):
        for p in NotificationPriority:
            if p.value == value:
                return p
        return NotificationPriority.NORMAL

    clean = str(value).strip().lower()
    mapping = {
        "low": NotificationPriority.LOW,
        "normal": NotificationPriority.NORMAL,
        "standard": NotificationPriority.NORMAL,
        "high": NotificationPriority.HIGH,
        "urgent": NotificationPriority.URGENT,
        "critical": NotificationPriority.CRITICAL,
    }
    return mapping.get(clean, NotificationPriority.NORMAL)


def parse_notification_type(value: Union[str, NotificationType]) -> NotificationType:
    """Parse string or enum into NotificationType."""
    if isinstance(value, NotificationType):
        return value
    clean = str(value).strip().lower()
    for t in NotificationType:
        if t.value == clean:
            return t
    return NotificationType.INFO


@dataclass(order=False)
class Notification:
    """Individual desktop notification record."""
    title: str
    message: str
    notification_id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:12]}")
    priority: NotificationPriority = NotificationPriority.NORMAL
    notification_type: NotificationType = NotificationType.INFO
    timestamp: float = field(default_factory=time.time)
    source: str = "desktop"
    action_url: Optional[str] = None
    callback_id: Optional[str] = None
    read: bool = False
    dismissed: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.notification_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "id": self.notification_id,
            "title": self.title,
            "message": self.message,
            "priority": self.priority.name.lower(),
            "priority_value": int(self.priority.value),
            "type": self.notification_type.value,
            "notification_type": self.notification_type.value,
            "timestamp": self.timestamp,
            "source": self.source,
            "action_url": self.action_url,
            "callback_id": self.callback_id,
            "read": self.read,
            "dismissed": self.dismissed,
            "metadata": dict(self.metadata),
        }


NotificationItem = Notification


class NotificationHub:
    """
    Sovereign Notification Hub for Hydra Desktop.
    Provides thread-safe priority queue, desktop balloon notification dispatcher,
    callback routing, and subscription broadcasts.
    """

    def __init__(self, max_history: int = 1000) -> None:
        self.max_history = max(50, max_history)
        self._lock = threading.RLock()
        self._notifications: Dict[str, Notification] = {}
        # Priority queue stores tuples of (-priority_value, timestamp, notification_id)
        self._priority_heap: List[Tuple[int, float, str]] = []
        self._callbacks: Dict[str, Callable[[Notification, Dict[str, Any]], Any]] = {}
        self._subscribers: Dict[str, Callable[[Notification], None]] = {}
        self._balloon_events: List[Dict[str, Any]] = []

    @property
    def total_notifications(self) -> int:
        with self._lock:
            return len(self._notifications)

    @property
    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for n in self._notifications.values() if not n.read and not n.dismissed)

    @property
    def pending_queue_count(self) -> int:
        with self._lock:
            return len(self._priority_heap)

    @property
    def balloon_events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._balloon_events)

    def clear(self) -> None:
        """Clear all stored notifications and pending priority queues."""
        with self._lock:
            self._notifications.clear()
            self._priority_heap.clear()
            self._balloon_events.clear()

    def register_callback(
        self,
        callback_id: str,
        callback: Callable[[Notification, Dict[str, Any]], Any],
    ) -> None:
        """Register action callback for notification interactions."""
        clean = callback_id.strip()
        with self._lock:
            self._callbacks[clean] = callback

    def unregister_callback(self, callback_id: str) -> bool:
        """Unregister action callback by ID."""
        clean = callback_id.strip()
        with self._lock:
            return self._callbacks.pop(clean, None) is not None

    def subscribe(self, listener: Callable[[Notification], None]) -> str:
        """Subscribe to notification publish events; returns subscription ID."""
        sub_id = f"sub_{uuid.uuid4().hex[:8]}"
        with self._lock:
            self._subscribers[sub_id] = listener
        return sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        """Unsubscribe listener by subscription ID."""
        with self._lock:
            return self._subscribers.pop(sub_id, None) is not None

    def send_tray_balloon(
        self,
        title: str,
        message: str,
        timeout_sec: int = 5,
        icon_type: str = "info",
    ) -> bool:
        """
        Dispatch system tray balloon or notification popup.
        Logs balloon event and executes native notification if available.
        """
        event = {
            "title": title,
            "message": message,
            "timeout_sec": timeout_sec,
            "icon_type": icon_type,
            "timestamp": time.time(),
        }

        with self._lock:
            self._balloon_events.append(event)
            if len(self._balloon_events) > 500:
                self._balloon_events.pop(0)

        # On Windows, attempt basic system notification if available
        if sys.platform == "win32":
            try:
                # Attempt ctypes user32 flash or beep if applicable
                import ctypes
                ctypes.windll.user32.MessageBeep(0)
            except Exception:
                pass

        return True

    def publish(
        self,
        title: str,
        message: str,
        priority: Union[NotificationPriority, str, int] = NotificationPriority.NORMAL,
        notification_type: Union[NotificationType, str] = NotificationType.INFO,
        notification_id: Optional[str] = None,
        source: str = "desktop",
        action_url: Optional[str] = None,
        callback_id: Optional[str] = None,
        callback: Optional[Callable[[Notification, Dict[str, Any]], Any]] = None,
        show_balloon: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Notification:
        """Publish a new notification into priority queue and dispatch to subscribers."""
        p_enum = parse_priority(priority)
        t_enum = parse_notification_type(notification_type)

        nid = notification_id or f"notif_{uuid.uuid4().hex[:12]}"
        cb_id = callback_id

        if callback is not None:
            if not cb_id:
                cb_id = f"cb_{nid}"
            self.register_callback(cb_id, callback)

        notif = Notification(
            title=title,
            message=message,
            notification_id=nid,
            priority=p_enum,
            notification_type=t_enum,
            source=source,
            action_url=action_url,
            callback_id=cb_id,
            metadata=metadata or {},
        )

        with self._lock:
            self._notifications[notif.notification_id] = notif
            # Negative priority ensures highest priority values pop first in min-heap
            heapq.heappush(self._priority_heap, (-int(p_enum.value), notif.timestamp, notif.notification_id))

            # Prune old notifications if over max_history
            if len(self._notifications) > self.max_history:
                oldest_id = next(iter(self._notifications))
                self._notifications.pop(oldest_id, None)

            subscribers = list(self._subscribers.values())

        # Dispatch balloon popup if requested or urgent/critical
        if show_balloon or p_enum >= NotificationPriority.URGENT:
            self.send_tray_balloon(title, message, icon_type=t_enum.value)

        # Notify subscribers
        for sub in subscribers:
            try:
                sub(notif)
            except Exception:
                pass

        return notif

    def get_notification(self, notification_id: str) -> Optional[Notification]:
        """Fetch notification by ID."""
        with self._lock:
            return self._notifications.get(notification_id)

    def pop_highest_priority(self) -> Optional[Notification]:
        """Pop and return the highest priority notification from the queue."""
        with self._lock:
            while self._priority_heap:
                _, _, nid = heapq.heappop(self._priority_heap)
                notif = self._notifications.get(nid)
                if notif and not notif.dismissed:
                    return notif
        return None

    def peek_highest_priority(self) -> Optional[Notification]:
        """Inspect the highest priority notification without removing from queue."""
        with self._lock:
            for _, _, nid in sorted(self._priority_heap):
                notif = self._notifications.get(nid)
                if notif and not notif.dismissed:
                    return notif
        return None

    def list_notifications(
        self,
        unread_only: bool = False,
        min_priority: Optional[NotificationPriority] = None,
        limit: int = 50,
    ) -> List[Notification]:
        """List notifications ordered by timestamp descending."""
        with self._lock:
            items = list(self._notifications.values())

        if unread_only:
            items = [n for n in items if not n.read and not n.dismissed]

        if min_priority is not None:
            min_val = int(parse_priority(min_priority).value)
            items = [n for n in items if int(n.priority.value) >= min_val]

        items.sort(key=lambda n: n.timestamp, reverse=True)
        return items[:limit]

    def mark_as_read(self, notification_id: str) -> bool:
        """Mark notification as read."""
        with self._lock:
            notif = self._notifications.get(notification_id)
            if notif:
                notif.read = True
                return True
        return False

    def mark_all_read(self) -> int:
        """Mark all notifications as read; returns count updated."""
        count = 0
        with self._lock:
            for notif in self._notifications.values():
                if not notif.read:
                    notif.read = True
                    count += 1
        return count

    def dismiss(self, notification_id: str) -> bool:
        """Dismiss notification from active displays and queues."""
        with self._lock:
            notif = self._notifications.get(notification_id)
            if notif:
                notif.dismissed = True
                notif.read = True
                return True
        return False

    def trigger_callback(
        self,
        notification_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Trigger registered callback associated with a notification."""
        notif = self.get_notification(notification_id)
        if not notif:
            raise KeyError(f"Notification not found: {notification_id}")

        if not notif.callback_id:
            return None

        with self._lock:
            handler = self._callbacks.get(notif.callback_id)

        if not handler:
            raise KeyError(f"No callback registered with ID: {notif.callback_id}")

        return handler(notif, payload or {})


_GLOBAL_HUB: Optional[NotificationHub] = None
_GLOBAL_HUB_LOCK = threading.RLock()


def get_notification_hub() -> NotificationHub:
    """Acquire thread-safe singleton NotificationHub."""
    global _GLOBAL_HUB
    with _GLOBAL_HUB_LOCK:
        if _GLOBAL_HUB is None:
            _GLOBAL_HUB = NotificationHub()
        return _GLOBAL_HUB


def reset_notification_hub() -> NotificationHub:
    """Reset singleton NotificationHub."""
    global _GLOBAL_HUB
    with _GLOBAL_HUB_LOCK:
        _GLOBAL_HUB = NotificationHub()
        return _GLOBAL_HUB
