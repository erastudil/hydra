# computer_use

scope : sovereign computer use automation engine for hydra agent loops.

subsystem : coordinate clipping, os input simulation, screen capture, composite actions, and playwright automation.

dialect : progen syntax.


## capabilities

coordinate guards : clamp target coordinates within active display dimensions minus safety margins.

fenced zones : register rectangular exclusion regions blocking mouse dispatch.

failsafe mechanism : emergency abort triggered when cursor targets screen corner (0, 0).

mouse automation : smooth movement, single and double clicks, button hold, drag operations, and wheel scroll.

keyboard automation : virtual key code mapping, down and up events, key chords, and character stream typing.

window management : inspect active foreground window, enumerate top-level windows, relocate window bounds, and restore focus.

screen capture : grab full screen, bounding boxes, or active window with base64 serialization and synthetic fallback.

browser bridge : playwright chromium integration for dom inspection, element interactions, and pdf exports.

composite browser primitives : fill_form across dictionary of selectors, scroll_until_visible bounded loops, and extract_table_data into structured rows.

composite desktop primitives : find_window_by_title_pattern with regex, set_window_bounds resizing, capture_active_window, safe_drag_and_drop, and safe_key_sequence with bounded pacing.


## programmatic interface

```python
from computer_use import get_computer_use_engine

engine = get_computer_use_engine()
engine.bounds.add_fence("taskbar", 0, 1040, 1920, 1080)
engine.dispatch("safe_drag_and_drop", from_coord=(100, 100), to_coord=(400, 300))
engine.dispatch("fill_form", fields={"#user": "admin", "#pass": "secret"})
engine.dispatch("capture_active_window")
```
