import asyncio
import io
import re
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application.current import set_app
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.output import DummyOutput

from hydra_cli import ui
from hydra_cli.agent import REPL_COMMAND_HELP, HydraReplCompleter, run_agent_loop
from hydra_cli.tui import HydraTUI, _LineGate

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class SizedOutput(DummyOutput):
    def __init__(self, columns: int, rows: int = 40) -> None:
        self.size = Size(rows=rows, columns=columns)

    def get_size(self) -> Size:
        return self.size


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _make_tui(pipe, width=100):
    tui = HydraTUI(
        commands=REPL_COMMAND_HELP,
        complete=HydraReplCompleter(model_aliases=["opus-5.5", "sonnet-5.5"]).get_candidates,
        pt_input=pipe,
        pt_output=SizedOutput(width),
        bridge_stdout=False,
    )
    tui._app = tui._build_app()
    return tui


def _render(tui, width):
    tui._pt_output.size = Size(rows=40, columns=width)
    app = tui._app
    container = app.layout.container

    async def paint():
        with set_app(app):
            height = container.preferred_height(width, 40).preferred
            screen = Screen()
            container.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, width, height), "", False, None)
            screen.draw_all_floats()
        return ["".join(screen.data_buffer[y][x].char for x in range(width)) for y in range(height)]

    return asyncio.run(paint())


@pytest.fixture(autouse=True)
def _clean_bridge():
    ui.drain_steering()
    yield
    ui.drain_steering()
    ui.set_status_sink(None)
    ui.set_prompt_handler(None)


def test_banner_scales_to_terminal(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    cases = {140: ui.get_terminal_banner(columns=140, rows=60),
             70: ui.get_terminal_banner(columns=70, rows=40),
             50: ui.get_terminal_banner(columns=50, rows=20)}
    for cols, text in cases.items():
        assert max(len(line) for line in text.splitlines()) <= cols
        assert text.count(ui.TAGLINE) == 1
    assert "(@)" in cases[140] and "(@)" in cases[70]
    assert "(@)" not in cases[50]
    assert len(cases[140].splitlines()) > len(cases[70].splitlines())


@pytest.mark.parametrize("mode", [ui.COLOR_256, ui.COLOR_TRUE])
def test_paint_keeps_every_glyph(mode):
    for art in (ui.HYDRA_ART_LARGE, ui.HYDRA_ART_MEDIUM):
        plain = [ANSI.sub("", line) for line in ui.paint_art(art, mode)]
        assert plain == art.strip("\n").splitlines()
    mark = [ANSI.sub("", line) for line in ui.paint_wordmark(mode, shimmer_at=10)]
    assert mark == ui.HYDRA_WORDMARK.splitlines()


def test_art_is_mirror_symmetric():
    table = str.maketrans("/\\()<>`'", "\\/)(><'`")
    for art in (ui.HYDRA_ART_LARGE, ui.HYDRA_ART_MEDIUM):
        lines = art.strip("\n").splitlines()
        width = max(len(line) for line in lines)
        for line in lines[:-1]:
            padded = line.ljust(width)
            assert padded[::-1].translate(table).rstrip() == padded.rstrip() or "~" in line


def test_launch_banner_without_tty_prints_final_frame(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(
        ui.shutil,
        "get_terminal_size",
        lambda fallback=(100, 40): type("Size", (), {"columns": 160, "lines": 60})(),
    )
    out = io.StringIO()
    ui.play_launch_banner(version="9.9.9", info=[("model", "opus-5.5"), ("cwd", "/tmp/x")], stream=out)
    text = out.getvalue()
    assert "v9.9.9" in text and "opus-5.5" in text and "/tmp/x" in text
    assert "\x1b[" not in text
    assert text.count("vVVVVVVVv") == 1


def test_render_answer_uses_the_window_width(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    text = ui.render_answer("word " * 80, width=160)
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines
    assert max(len(line) for line in lines) > 110
    assert all(len(line) <= 160 for line in lines)


def test_stream_wrap_matches_width():
    chunks = []
    wrapper = ui.StreamWrap(chunks.append, width_fn=lambda: 24)
    wrapper.feed("alpha beta gamma delta epsilon zeta")
    wrapper.finish()
    text = "".join(chunks)
    assert all(len(line) <= 22 for line in text.splitlines())
    assert "alpha beta gamma" in text
    assert "epsilon zeta" in text


def test_render_answer_wraps_and_styles(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    answer = "# Plan\n\n- first **bold** step with `code` " + "word " * 30 + "\n\n```py\nprint('x')\n```"
    text = ui.render_answer(answer, width=60, header="· opus · 1.0s")
    lines = text.splitlines()
    assert lines[0].endswith("hydra · opus · 1.0s")
    assert "  Plan" in lines
    assert all(len(line) <= 60 for line in lines)
    assert any("print('x')" in line for line in lines)
    assert "**" not in text


def test_line_gate_buffers_partials_and_collapses_carriage_returns():
    writes = []
    proxy = MagicMock()
    proxy.write.side_effect = writes.append
    gate = _LineGate(proxy, io.StringIO())
    gate.write("partial")
    assert writes == []
    gate.write(" line\nspin 1\rspin 2\r\x1b[Kdone\nleft")
    assert writes == ["partial line\ndone\n"]
    gate.drain()
    assert writes[-1] == "left\n"


def test_spinner_routes_status_to_sink_without_stream_writes():
    seen = []
    stream = io.StringIO()
    ui.set_status_sink(seen.append)
    with ui.ThinkingSpinner(message="Thinking...", status_messages=["Working..."], stream=stream, interval=0.01):
        time.sleep(0.05)
    assert stream.getvalue() == ""
    assert "Working" in seen
    assert seen[-1] is None


def test_busy_status_is_just_working():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 80)
        tui._waiting = None
        tui._begin_busy()
        tui._on_activity("Reasoning · Synthesizing plan")
        status = "".join(text for _, text in tui._status())
    assert "working..." in status
    assert "Reasoning" not in status
    assert "Synthesizing" not in status


def test_mouse_wheel_scrolls_history_and_typing_repins():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 60)
        tui._waiting = "main"
        calls = []

        def fake_scroll(lines):
            calls.append(lines)
            return True

        with patch("hydra_cli.tui.scroll_terminal_history", side_effect=fake_scroll):
            tui._on_wheel(-3)
            assert tui._history_scroll == -3
            tui._on_wheel(-3)
            assert tui._history_scroll == -6
            tui._buffer.text = "hello"
            assert tui._history_scroll == 0
    assert calls == [-3, -3]


def test_prompt_input_prefers_handler():
    ui.set_prompt_handler(lambda prompt: f"answer:{prompt}")
    assert ui.prompt_input("q?") == "answer:q?"
    ui.set_prompt_handler(None)
    with patch("builtins.input", return_value="typed"):
        assert ui.prompt_input("q?") == "typed"


@pytest.mark.parametrize("width", [36, 64, 100, 180])
def test_composer_frame_holds_at_every_width(width):
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, width)
        tui.update_status(model="claude-opus-5.5", tier="frontier", tokens=24000, budget=200000, turns=3)
        g = tui._g
        for waiting, text in (("main", ""), (None, "queued words " * 12), ("answer", "y"), ("main", "one\ntwo\nthree")):
            tui._waiting = waiting
            tui._buffer.text = text
            rows = _render(tui, width)
            top = next(i for i, row in enumerate(rows) if row.startswith(g["tl"]))
            bottom = max(i for i, row in enumerate(rows) if row.startswith(g["bl"]))
            assert rows[top][width - 1] == g["tr"]
            assert rows[bottom][width - 1] == g["br"]
            for row in rows[top + 1:bottom]:
                assert row[0] == g["v"] and row[width - 1] == g["v"]
            if "\n" in text:
                assert bottom - top - 1 == 3


def test_reflow_estimate_counts_rows_split_by_a_narrower_window():
    from hydra_cli.tui import reflow_rows_above_cursor

    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 120)
        tui.update_status(model="claude-opus-5.5", tier="frontier")
        tui._waiting = "main"
        app = tui._app
        tui._pt_output.size = Size(rows=40, columns=120)

        async def paint():
            with set_app(app):
                screen = Screen()
                container = app.layout.container
                height = container.preferred_height(120, 40).preferred
                container.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, 120, height), "", False, None)
                return screen

        screen = asyncio.run(paint())
    assert reflow_rows_above_cursor(screen, 4, 2, 120) == 0
    assert reflow_rows_above_cursor(screen, 4, 2, 60) == 1
    assert reflow_rows_above_cursor(screen, 70, 2, 60) == 2
    assert reflow_rows_above_cursor(screen, 4, 2, 0) == 0


def test_footer_shows_slash_suggestions():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 120)
        tui._waiting = "main"
        tui._buffer.text = "/mo"
        footer = _render(tui, 120)[-1]
        assert "/model" in footer and "tab" in footer
        tui._complete()
        assert tui._buffer.text == "/model "
        tui._buffer.text = "/model op"
        tui._complete()
        assert tui._buffer.text == "/model opus-5.5 "


def test_enter_state_machine_submit_queue_steer_and_answer():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe)
        tui._waiting = "main"
        tui._buffer.text = "hello hydra"
        tui._on_enter()
        assert tui._main_q.get_nowait() == "hello hydra"
        assert tui._buffer.text == ""

        tui._waiting = None
        tui._buffer.text = "use pytest"
        tui._on_enter()
        assert tui._queued == ["use pytest"]
        tui._on_enter()
        assert tui._queued == []
        assert ui.drain_steering() == ["use pytest"]

        tui._waiting = "main"
        tui._on_enter()
        tui._on_enter()
        assert tui._main_q.get_nowait() == "/steer"

        tui._waiting = "answer"
        tui._buffer.text = "y"
        tui._on_enter()
        assert tui._answer_q.get_nowait() == "y"


def test_read_line_drains_queue_and_steer_leftovers_first():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe)
        tui._queued = ["next task"]
        ui.push_steering("late steer")
        assert tui.read_line() == "late steer"
        assert tui.read_line() == "next task"


def test_threaded_composer_end_to_end_with_dictation():
    class FakeRecorder:
        def __init__(self, on_autostop=None):
            self.levels = [0.1] * 20
            self.heard_speech = True
            self.elapsed = 1.0

        def start(self):
            return None

        def stop(self):
            from hydra_cli.dictation import pcm_to_wav
            return pcm_to_wav(b"\x00\x00" * 16000)

        def cancel(self):
            return None

    with create_pipe_input() as pipe, \
         patch("hydra_cli.dictation.Recorder", FakeRecorder), \
         patch("hydra_cli.dictation.transcribe", return_value=("spoken words", "fake")):
        tui = HydraTUI(commands=REPL_COMMAND_HELP, pt_input=pipe, pt_output=SizedOutput(100), bridge_stdout=False)
        tui.start()
        try:
            assert tui.active
            result = {}
            reader = threading.Thread(target=lambda: result.setdefault("line", tui.read_line()))
            reader.start()
            assert _wait_for(lambda: tui._waiting == "main")
            pipe.send_text("summon ")
            pipe.send_text("\x00")
            assert _wait_for(lambda: tui._recorder is not None)
            pipe.send_text("\x00")
            assert _wait_for(lambda: "spoken words" in tui._buffer.text)
            pipe.send_text("\r")
            reader.join(5)
            assert result["line"] == "summon spoken words"

            assert tui._waiting is None
            pipe.send_text("prefer ruff\r")
            assert _wait_for(lambda: tui._queued == ["prefer ruff"])
            pipe.send_text("\r")
            assert _wait_for(lambda: not tui._queued)
            assert ui.drain_steering() == ["prefer ruff"]
        finally:
            tui.close()
        assert not tui.active


def test_agent_loop_injects_tui_steering(tmp_path):
    provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    tool_turn = {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "function": {"name": "filesystem__read_file", "arguments": '{"path": "a.txt"}'}}]}}]}
    final_turn = {"choices": [{"message": {"role": "assistant", "content": "done", "tool_calls": None}}]}
    seen = []

    def fake_fetch(url, headers, payload, timeout=120):
        seen.append([str(m.get("content") or "") for m in payload["messages"]])
        if len(seen) == 1:
            ui.push_steering("prefer pathlib")
            return tool_turn
        return final_turn

    registry = MagicMock()
    registry.get_openai_tools.return_value = [{"type": "function", "function": {"name": "filesystem__read_file"}}]
    registry.dispatch.return_value = "contents"
    with patch("hydra_cli.agent.get_frontier_providers", return_value=provider), \
         patch("hydra_cli.agent._fetch_raw_completion", side_effect=fake_fetch), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        assert run_agent_loop(alias="sonnet 5.5", prompt="read a.txt", registry=registry, cwd=str(tmp_path)) == "done"
    assert "[IN-FLIGHT STEERING DIRECTIVE]: prefer pathlib" in seen[1]


def test_escape_interrupts_and_clipboard_replaces_ctrl_c():
    from prompt_toolkit.clipboard.in_memory import InMemoryClipboard

    with create_pipe_input() as pipe:
        board = InMemoryClipboard()
        tui = HydraTUI(
            commands=REPL_COMMAND_HELP,
            pt_input=pipe,
            pt_output=SizedOutput(80),
            bridge_stdout=False,
            clipboard=board,
        )
        tui._app = tui._build_app()
        assert tui._app.mouse_support()
        tui._waiting = "main"
        tui._buffer.text = "draft"
        tui._on_interrupt()
        assert tui._buffer.text == ""
        assert tui._main_q.empty()

        tui._buffer.text = "select me"
        tui._buffer.cursor_position = 0
        tui._buffer.start_selection()
        tui._buffer.cursor_position = 6
        tui._copy()
        assert board.get_data().text == "select"
        tui._buffer.exit_selection()
        tui._buffer.cursor_position = len(tui._buffer.text)
        tui._paste()
        assert tui._buffer.text == "select meselect"

        tui._buffer.text = "abcdef"
        tui._buffer.cursor_position = 0
        tui._buffer.start_selection()
        tui._buffer.cursor_position = 3

        async def delete_selection():
            with set_app(tui._app):
                tui._app.key_processor.feed(KeyPress(Keys.Backspace))
                tui._app.key_processor.process_keys()

        from prompt_toolkit.key_binding.key_processor import KeyPress
        from prompt_toolkit.keys import Keys
        asyncio.run(delete_selection())
        assert tui._buffer.text == "def"


def test_prompt_wraps_on_word_boundaries():
    from hydra_cli.tui import _should_wrap

    assert _should_wrap("hello wonderful", 6, 6, 10, 1) is True
    assert _should_wrap("hello wonderful", 7, 0, 10, 1) is False
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 16)
        tui._waiting = "main"
        tui._buffer.text = "hello wonderful"
        blob = "\n".join(_render(tui, 16))
    assert "hello wond" not in blob
    assert "wonderful" in blob


def test_mouse_click_moves_the_cursor():
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import BufferControl
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    def find_control(container):
        if isinstance(container, Window) and isinstance(container.content, BufferControl):
            return container.content
        for child in getattr(container, "children", ()) or ():
            found = find_control(child)
            if found is not None:
                return found
        return None

    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 60)
        tui._waiting = "main"
        tui._buffer.text = "hello wonderful"
        tui._buffer.cursor_position = 0
        _render(tui, 60)
        control = find_control(tui._app.layout.container)
        assert control is not None

        async def click():
            with set_app(tui._app):
                control.create_content(30, 4)
                control.mouse_handler(MouseEvent(
                    Point(x=4, y=0),
                    MouseEventType.MOUSE_DOWN,
                    MouseButton.LEFT,
                    frozenset(),
                ))

        asyncio.run(click())
    assert tui._buffer.cursor_position == 4


def test_mouse_wheel_on_prompt_control_scrolls_history():
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    with create_pipe_input() as pipe:
        tui = _make_tui(pipe, 60)
        tui._waiting = "main"
        control = None
        from prompt_toolkit.layout.containers import Window
        from prompt_toolkit.layout.controls import BufferControl

        def find_control(container):
            if isinstance(container, Window) and isinstance(container.content, BufferControl):
                return container.content
            for child in getattr(container, "children", ()) or ():
                found = find_control(child)
                if found is not None:
                    return found
            return None

        control = find_control(tui._app.layout.container)
        assert control is not None
        seen = []
        with patch("hydra_cli.tui.scroll_terminal_history", side_effect=lambda n: seen.append(n) or True):
            async def wheel():
                with set_app(tui._app):
                    control.mouse_handler(MouseEvent(
                        Point(x=1, y=0),
                        MouseEventType.SCROLL_UP,
                        MouseButton.MIDDLE,
                        frozenset(),
                    ))
            asyncio.run(wheel())
    assert seen == [-3]


def test_slash_help_lists_banner_models_system():
    names = {name for name, _ in REPL_COMMAND_HELP}
    assert "/banner" in names
    assert "/models" in names
    assert "/system" in names
    assert "/help" in names
    assert "/model" in names


def test_tokens_stream_before_the_turn_finishes(tmp_path):
    body = (
        'data: {"choices":[{"delta":{"content":"hel"}}]}\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n'
        "data: [DONE]\n"
    ).encode()

    class _Resp:
        def __iter__(self):
            yield from body.splitlines(keepends=True)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    seen = []
    provider = [{"url": "https://api.test/v1/chat/completions", "headers": {}}]
    with patch("hydra_cli.agent.urlopen", return_value=_Resp()), \
         patch("hydra_cli.agent.get_frontier_providers", return_value=provider), \
         patch("hydra_cli.agent.hydra_home", return_value=str(tmp_path)):
        answer = run_agent_loop(
            alias="sonnet 5.5",
            prompt="say hello",
            cwd=str(tmp_path),
            on_token=seen.append,
        )
    assert seen == ["hel", "lo"]
    assert answer == "hello"


def test_write_token_flushes_each_partial():
    with create_pipe_input() as pipe:
        tui = _make_tui(pipe)
        chunks = []

        class _Proxy:
            def write(self, data):
                chunks.append(data)

            def flush(self):
                chunks.append("<flush>")

        tui._proxy = _Proxy()
        tui.write_token("hel")
        tui.write_token("lo")
    assert chunks == ["hel", "<flush>", "lo", "<flush>"]
