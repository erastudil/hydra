"""Local hands: stacks, calc, memory, personalities. No model call."""

from pathlib import Path

import pytest

from hydra_cli.hands import answer_locally, dispatch_native, resolve_personality


def test_stack_cite_speed_of_light():
    text = answer_locally("what is the speed of light in vacuum", personality="researcher")
    assert "personality: researcher" in text
    assert "act: cite" in text
    assert "299792458" in text
    assert "physics.nist.gov" in text


def test_calc_fraction():
    text = answer_locally("simplify 3/4 + 1/6")
    assert "act: say" in text
    assert "11/12" in text


def test_units():
    text = answer_locally("10 km to miles")
    assert "route: COMPUTE" in text
    assert "miles" in text


def test_memory_round_trip(tmp_path: Path):
    path = tmp_path / "memory.db"
    kept = answer_locally("remember preference: use pytest", personality="coder", memory_file=path)
    assert "Kept a preference" in kept
    recalled = answer_locally("what do you remember about pytest", memory_file=path)
    assert "use pytest" in recalled


def test_gap():
    text = answer_locally("who won the 1998 world series")
    assert "silence-gap" in text
    assert "next:" in text


def test_native_dispatch_misses_mcp_names():
    assert dispatch_native("filesystem__read_file", {"path": "x"}) is None


def test_unknown_voice():
    with pytest.raises(KeyError):
        resolve_personality("wizard")
