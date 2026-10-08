"""Windows console input that delivers Ctrl+Space to the Hydra composer.

prompt_toolkit's virtual-terminal reader drops key records whose Unicode
character is NUL. On this console, Ctrl+Space is VK_SPACE with Unicode NUL,
so the chord never reaches the c-space binding. This reader yields NUL for
that chord. The VT parser maps NUL to Control-At, the c-space alias.
"""

from __future__ import annotations

from typing import Iterator

from prompt_toolkit.input.win32 import (
    ConsoleInputReader,
    Vt100ConsoleInputReader,
    Win32Input,
    _Win32InputBase,
    _is_win_vt100_input_enabled,
)
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from prompt_toolkit.win32_types import KEY_EVENT_RECORD, EventTypes

VK_SPACE = 0x20
LEFT_CTRL_PRESSED = 0x0008
RIGHT_CTRL_PRESSED = 0x0004


def _ctrl_down(control_key_state: int) -> bool:
    return bool(control_key_state & (LEFT_CTRL_PRESSED | RIGHT_CTRL_PRESSED))


def _is_ctrl_space(ev: KEY_EVENT_RECORD) -> bool:
    return (
        ev.VirtualKeyCode == VK_SPACE
        and _ctrl_down(ev.ControlKeyState)
        and ev.uChar.UnicodeChar in ("\x00", " ")
    )


class CtrlSpaceVt100Reader(Vt100ConsoleInputReader):
    def _get_keys(self, read, input_records) -> Iterator[str]:
        for i in range(read.value):
            record = input_records[i]
            if record.EventType not in EventTypes:
                continue
            ev = getattr(record.Event, EventTypes[record.EventType])
            if not (isinstance(ev, KEY_EVENT_RECORD) and ev.KeyDown):
                continue
            if _is_ctrl_space(ev):
                yield "\x00"
                continue
            char = ev.uChar.UnicodeChar
            if char != "\x00":
                yield char


class CtrlSpaceConsoleReader(ConsoleInputReader):
    def _event_to_key_presses(self, ev: KEY_EVENT_RECORD) -> list[KeyPress]:
        if ev.uChar.UnicodeChar == "\x00" and _is_ctrl_space(ev):
            return [KeyPress(Keys.ControlSpace, " ")]
        return super()._event_to_key_presses(ev)


class HydraWin32Input(Win32Input):
    def __init__(self) -> None:
        _Win32InputBase.__init__(self)
        self._use_virtual_terminal_input = _is_win_vt100_input_enabled()
        if self._use_virtual_terminal_input:
            self.console_input_reader = CtrlSpaceVt100Reader()
        else:
            self.console_input_reader = CtrlSpaceConsoleReader()


def console_input() -> HydraWin32Input:
    return HydraWin32Input()
