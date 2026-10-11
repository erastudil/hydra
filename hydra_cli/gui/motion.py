"""Animation helpers. QPropertyAnimation and QVariantAnimation only; durations and curves come from theme tokens.

Nothing here runs per streamed token. Each helper starts one short animation, and under reduced motion
(theme.reduced_motion) it applies the end state at once and returns None.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, QAbstractAnimation, QPropertyAnimation, QVariantAnimation, QObject, QEvent
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractScrollArea, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QWidget

from hydra_cli.gui import theme

_LIVE = "_motion_anim"


def _keep(owner: QObject, anim: QAbstractAnimation) -> None:
    """Hold the newest animation on its owner so it is not collected mid-flight; stop any older one."""
    old = owner.property(_LIVE)
    if isinstance(old, QAbstractAnimation) and old.state() == QAbstractAnimation.State.Running:
        old.stop()
    owner.setProperty(_LIVE, anim)


def _variant(owner: QObject, start, end, name: str, curve: str, apply: Callable, done: Optional[Callable] = None
             ) -> Optional[QVariantAnimation]:
    ms = theme.duration(name)
    if ms <= 0:
        apply(end)
        if done:
            done()
        return None
    anim = QVariantAnimation(owner)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(theme.easing(curve))
    anim.valueChanged.connect(apply)
    if done:
        anim.finished.connect(done)
    _keep(owner, anim)
    anim.start()
    return anim


def fade_in(widget: QWidget, name: str = "base") -> Optional[QPropertyAnimation]:
    """Fade a widget from transparent to opaque, then drop the effect so painting returns to normal."""
    ms = theme.duration(name)
    if ms <= 0:
        widget.setGraphicsEffect(None)
        return None
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(theme.easing("standard"))
    anim.finished.connect(lambda: widget.setGraphicsEffect(None))
    _keep(widget, anim)
    anim.start()
    return anim


def glide(owner: QObject, start: int, end: int, apply: Callable[[int], None], name: str = "base",
          curve: str = "emphasized", done: Optional[Callable] = None) -> Optional[QVariantAnimation]:
    """Animate an integer (a width, a scroll position) with an eased curve; apply gets rounded ints."""
    return _variant(owner, start, end, name, curve, lambda v: apply(int(round(v))), done)


def glide_scroll(area: QAbstractScrollArea, name: str = "base") -> None:
    """Scroll a view to its bottom with easing. A running glide is retargeted, not restarted per token."""
    bar = area.verticalScrollBar()
    live = bar.property(_LIVE)
    target = bar.maximum()
    if isinstance(live, QVariantAnimation) and live.state() == QAbstractAnimation.State.Running:
        live.setEndValue(target)
        return
    if bar.value() >= target:
        return
    glide(bar, bar.value(), target, bar.setValue, name=name, curve="standard")


class HoverGlow(QObject):
    """Soft accent glow that eases in on hover or focus and out on leave. One shadow effect per widget."""

    def __init__(self, widget: QWidget, color: str, radius: int = 18) -> None:
        super().__init__(widget)
        self._w = widget
        self._radius = radius
        self._fx = QGraphicsDropShadowEffect(widget)
        self._fx.setOffset(0, 0)
        glow = QColor(color)
        glow.setAlpha(150)
        self._fx.setColor(glow)
        self._fx.setBlurRadius(0)
        widget.setGraphicsEffect(self._fx)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self._to(self._radius, "fast", "standard")
        elif kind in (QEvent.Type.Leave, QEvent.Type.FocusOut) and not self._w.hasFocus():
            self._to(0, "base", "exit")
        return False

    def _to(self, radius: int, name: str, curve: str) -> None:
        _variant(self._fx, self._fx.blurRadius(), float(radius), name, curve, self._fx.setBlurRadius)


def is_gliding(owner: QObject) -> bool:
    live = owner.property(_LIVE)
    return isinstance(live, QAbstractAnimation) and live.state() == QAbstractAnimation.State.Running


class _Veil(QWidget):
    """Transparent overlay that paints the view background at falling alpha, so new content appears to fade in."""

    def __init__(self, parent: QWidget, color: QColor) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._color = color
        self.alpha = 255

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        c = QColor(self._color)
        c.setAlpha(self.alpha)
        p.fillRect(self.rect(), c)


def reveal(area: QAbstractScrollArea, top: int, color: str, name: str = "base") -> Optional[QVariantAnimation]:
    """Fade in everything below viewport y=top. One overlay repaint per frame; the document is never touched."""
    view = area.viewport()
    if theme.duration(name) <= 0 or top >= view.height():
        return None
    veil = _Veil(view, QColor(color))
    veil.setGeometry(0, max(0, top), view.width(), view.height() - max(0, top))
    veil.show()

    def step(value: int) -> None:
        veil.alpha = value
        veil.update()

    return _variant(veil, 255, 0, name, "standard", step, done=veil.deleteLater)
