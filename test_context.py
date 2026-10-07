from hydra_cli.context import SessionContextLedger
ledger = SessionContextLedger("test_session_123")
ledger.append_turn("user", "test the file path C:\\Users\\jpm05\\Documents\\hydra and camelCase and snake_case_words with longwords")
ledger.append_turn("assistant", "another response")
retrieved = ledger.retrieve_verbatim(["C:\\Users\\jpm05\\Documents\\hydra"])
compacted = ledger.compact_session()
print(retrieved)
print(compacted["summary"])
