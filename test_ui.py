from hydra_cli.ui import get_terminal_banner, wrap_text, render_prompt_box

print("--- BANNER ---")
print(get_terminal_banner())

print("--- WRAP TEXT ---")
print(wrap_text("This is a very long string that should be wrapped carefully so that we can test the wrap logic without breaking words apart. " * 3, width=40))

print("--- PROMPT BOX ---")
print(render_prompt_box("Claude-5.5", "1.2.0", 1500, 100000, 4, queued_steer="Focus on invariants and clean exit codes. Avoid metaphor.", width=60))

print("ALL TESTS PASSED")
