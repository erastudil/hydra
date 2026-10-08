# test_spinner.py
import io, sys, threading, time
from unittest.mock import MagicMock, patch
import pytest
from hydra_cli.agent import run_agent_loop
from hydra_cli.ui import (
    ASCII_SPINNER_FRAMES, BRAILLE_SPINNER_FRAMES, CLEAR_LINE, CR,
    DEFAULT_SPINNER_STATUS_MESSAGES, GREEN_BRIGHT, RESET,
    ThinkingSpinner, can_render_braille
)

class MockStream:
    def __init__(self, encoding='utf-8'):
        self.encoding = encoding
        self.buffer = []
    def write(self, data):
        self.buffer.append(data)
    def flush(self):
        pass
    def getvalue(self):
        return ''.join(self.buffer)

def test_spinner_frame_and_message_constants():
    assert len(BRAILLE_SPINNER_FRAMES) == 10
    assert BRAILLE_SPINNER_FRAMES[0] == chr(0x280b)
    assert BRAILLE_SPINNER_FRAMES[-1] == chr(0x280f)
    assert len(ASCII_SPINNER_FRAMES) == 4
    assert ASCII_SPINNER_FRAMES == ['|', '/', '-', '\\\\']
    assert DEFAULT_SPINNER_STATUS_MESSAGES == ['Working...']

def test_spinner_braille_vs_ascii_selection():
    sp_braille = ThinkingSpinner(use_braille=True)
    assert sp_braille.frames == BRAILLE_SPINNER_FRAMES
    sp_ascii = ThinkingSpinner(use_braille=False)
    assert sp_ascii.frames == ASCII_SPINNER_FRAMES
    utf8_stream = MockStream(encoding='utf-8')
    assert can_render_braille(utf8_stream) is True
    ascii_stream = MockStream(encoding='ascii')
    assert can_render_braille(ascii_stream) is False
    sp_auto = ThinkingSpinner(stream=ascii_stream)
    assert sp_auto.frames == ASCII_SPINNER_FRAMES

def test_spinner_line_formatting_and_status_rotation():
    stream = MockStream()
    sp = ThinkingSpinner(message='Thinking...', stream=stream, rotate_interval=2.0, color=False)
    line0 = sp.format_line(elapsed=0.0)
    assert 'Thinking... (0.0s)' in line0
    assert 'Working...' in line0
    assert sp.sink_text() == 'Working'
    sp.update_status('Executing tool...')
    line_custom = sp.format_line(elapsed=1.0)
    assert 'Executing tool...' in line_custom
    assert sp.sink_text() == 'Executing tool'
    sp.update_status(None)
    line_revert = sp.format_line(elapsed=1.0)
    assert 'Working...' in line_revert
    sp.set_status_messages(['Custom A', 'Custom B'])
    assert sp.format_line(elapsed=0.5).endswith('Custom A')
    assert sp.format_line(elapsed=2.5).endswith('Custom B')
    # sink stays on the single Working label unless manually overridden
    assert sp.sink_text() == 'Working'

def test_spinner_colorization():
    stream_color = MockStream()
    sp_color = ThinkingSpinner(stream=stream_color, color=True)
    sp_color._render_frame()
    output_color = stream_color.getvalue()
    assert GREEN_BRIGHT in output_color
    assert RESET in output_color
    stream_plain = MockStream()
    sp_plain = ThinkingSpinner(stream=stream_plain, color=False)
    sp_plain._render_frame()
    output_plain = stream_plain.getvalue()
    assert chr(27) + '[' not in output_plain

def test_spinner_clean_exit_sequence():
    stream = MockStream()
    sp = ThinkingSpinner(stream=stream, color=False)
    sp.start()
    assert sp.is_running is True
    time.sleep(0.02)
    sp.stop()
    assert sp.is_running is False
    output = stream.getvalue()
    assert CLEAR_LINE in output

def test_spinner_write_line():
    stream = MockStream()
    sp = ThinkingSpinner(stream=stream, color=False)
    sp.start()
    time.sleep(0.01)
    sp.write_line('[INFO] Intermediary log message')
    time.sleep(0.01)
    sp.stop()
    output = stream.getvalue()
    assert '[INFO] Intermediary log message' in output

def test_spinner_context_manager_lifecycle():
    stream = MockStream()
    sp = ThinkingSpinner(stream=stream, interval=0.01, color=False)
    with sp as active_sp:
        assert active_sp.is_running is True
        assert active_sp._thread is not None
        assert active_sp._thread.is_alive() is True
        time.sleep(0.03)
    assert sp.is_running is False
    assert not sp._thread.is_alive()
    assert CLEAR_LINE in stream.getvalue()

def test_nested_spinners_pause_and_resume():
    stream = MockStream()
    outer_sp = ThinkingSpinner(message='Outer', stream=stream, interval=0.01, color=False)
    inner_sp = ThinkingSpinner(message='Inner', stream=stream, interval=0.01, color=False)
    with outer_sp:
        assert outer_sp.is_running is True
        assert outer_sp._paused is False
        assert ThinkingSpinner._active_spinner is outer_sp
        with inner_sp:
            assert inner_sp.is_running is True
            assert outer_sp._paused is True
            assert ThinkingSpinner._active_spinner is inner_sp
        assert inner_sp.is_running is False
        assert outer_sp._paused is False
        assert ThinkingSpinner._active_spinner is outer_sp
    assert outer_sp.is_running is False
    assert ThinkingSpinner._active_spinner is None

def test_agent_loop_with_thinking_spinner():
    stream = MockStream()
    mock_response = {'choices': [{'message': {'role': 'assistant', 'content': 'Task completed successfully.'}}]}
    with patch('hydra_cli.agent._fetch_raw_completion', return_value=mock_response), patch('sys.stderr', stream):
        answer = run_agent_loop(alias='fast', prompt='Inspect code', max_turns=1, use_prompt_adapter=False)
    assert 'Task completed successfully.' in answer
    assert CR in stream.getvalue()
