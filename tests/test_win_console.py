"""Ctrl+Space must survive the Windows console reader."""

import sys

import pytest

pytest.importorskip("prompt_toolkit")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="win32 console input")

from ctypes.wintypes import DWORD

from prompt_toolkit.keys import Keys
from prompt_toolkit.win32_types import INPUT_RECORD

from hydra_cli.win_console import (
    LEFT_CTRL_PRESSED,
    VK_SPACE,
    CtrlSpaceConsoleReader,
    CtrlSpaceVt100Reader,
)


def _records(virtual_key: int, char: str, control_state: int, key_down: int = 1):
    record = INPUT_RECORD()
    record.EventType = 1
    event = record.Event.KeyEvent
    event.KeyDown = key_down
    event.RepeatCount = 1
    event.VirtualKeyCode = virtual_key
    event.VirtualScanCode = 0x39
    event.uChar.UnicodeChar = char
    event.ControlKeyState = control_state
    batch = (INPUT_RECORD * 1)()
    batch[0] = record
    return DWORD(1), batch


def test_vt_reader_emits_nul_for_ctrl_space():
    reader = CtrlSpaceVt100Reader.__new__(CtrlSpaceVt100Reader)
    read, batch = _records(VK_SPACE, "\x00", LEFT_CTRL_PRESSED)
    assert list(reader._get_keys(read, batch)) == ["\x00"]


def test_vt_reader_emits_nul_when_ctrl_space_carries_a_space_char():
    reader = CtrlSpaceVt100Reader.__new__(CtrlSpaceVt100Reader)
    read, batch = _records(VK_SPACE, " ", LEFT_CTRL_PRESSED)
    assert list(reader._get_keys(read, batch)) == ["\x00"]


def test_vt_reader_drops_bare_ctrl_and_keeps_letters():
    reader = CtrlSpaceVt100Reader.__new__(CtrlSpaceVt100Reader)
    read, batch = _records(0x11, "\x00", LEFT_CTRL_PRESSED)
    assert list(reader._get_keys(read, batch)) == []
    read, batch = _records(0x41, "a", 0)
    assert list(reader._get_keys(read, batch)) == ["a"]


def test_legacy_reader_maps_nul_ctrl_space():
    reader = CtrlSpaceConsoleReader.__new__(CtrlSpaceConsoleReader)
    read, batch = _records(VK_SPACE, "\x00", LEFT_CTRL_PRESSED)
    event = batch[0].Event.KeyEvent
    presses = reader._event_to_key_presses(event)
    assert len(presses) == 1
    assert presses[0].key == Keys.ControlSpace
    assert read.value == 1
