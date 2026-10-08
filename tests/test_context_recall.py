from hydra_cli.context import SessionContextLedger, normalize_context_mode


def test_recall_pulls_a_synonym_and_keeps_the_turn_whole(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    ledger = SessionContextLedger(session_id="pytest_recall")
    body = "def parse_widget():\n" + ("    return 1\n" * 40)
    ledger.append_turn("assistant", "traceback showed an exception in parse_widget\n" + body)
    ledger.append_turn("assistant", "unrelated lunch order for the team")
    hits = ledger.retrieve_verbatim(["bug"], max_turns=2)
    assert len(hits) == 1
    assert hits[0]["content"] == "traceback showed an exception in parse_widget\n" + body
    rendered = ledger.render_prior("fix the bug", "recall")
    assert "[RETRIEVED CONTEXT]" in rendered
    assert body in rendered
    assert "lunch order" not in rendered


def test_pinned_instruction_survives_recall(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    ledger = SessionContextLedger(session_id="pytest_pin")
    ledger.append_turn("instruction", "rule : never rewrite the agents file")
    ledger.append_turn("assistant", "shipped the widget parser")
    rendered = ledger.render_prior("discuss the weather", "recall")
    assert "never rewrite the agents file" in rendered
    assert "widget parser" not in rendered


def test_sliding_keeps_recent_turns_unsliced(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    ledger = SessionContextLedger(session_id="pytest_slide")
    long_turn = "alpha_marker " + ("token " * 500)
    ledger.append_turn("user", long_turn)
    rendered = ledger.render_prior("anything", "sliding")
    assert long_turn in rendered
    assert "[PRIOR TURNS]" in rendered


def test_compact_leaves_every_turn_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr("hydra_cli.context.Path.home", lambda: tmp_path)
    ledger = SessionContextLedger(session_id="pytest_flush")
    ledger.append_turn("user", "keep this sentence intact")
    report = ledger.compact_session(max_history_turns=0)
    assert report["pruned_messages"] == []
    assert report["turns_on_disk"] == 1
    reloaded = SessionContextLedger(session_id="pytest_flush")
    assert reloaded.turns[0]["content"] == "keep this sentence intact"


def test_mode_aliases():
    assert normalize_context_mode("retrieve") == "recall"
    assert normalize_context_mode("compact") == "recall"
    assert normalize_context_mode("sliding") == "sliding"
    assert normalize_context_mode("nope") == "recall"
