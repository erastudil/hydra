# computer_use

scope : sovereign computer use automation engine for hydra agent loops.

subsystem : coordinate clipping, os input simulation, screen capture, and playwright automation.

dialect : progen syntax.


## capabilities

coordinate guards : clamp target coordinates within active display dimensions minus safety margins.

fenced zones : register rectangular exclusion regions blocking mouse dispatch.

failsafe mechanism : emergency abort triggered when cursor targets screen corner (0, 0).

mouse automation : smooth movement, single and double clicks, button hold, drag operations, and wheel scroll.

keyboard automation : virtual key code mapping, down and up events, key chords, and character stream typing.

window management : inspect active foreground window, enumerate top-level windows, and restore focus.

screen capture : grab full screen or bounding boxes with base64 serialization and synthetic fallback.

browser bridge : playwright chromium integration for dom inspection, element interactions, and pdf exports.


## programmatic interface

```python
from computer_use import get_computer_use_engine

engine = get_computer_use_engine()
engine.bounds.add_fence("taskbar", 0, 1040, 1920, 1080)
engine.dispatch("mouse_move", x=500, y=400)
engine.dispatch("mouse_click", button="left")
engine.dispatch("type_text", text="hydra status")
screenshot = engine.dispatch("screen_capture")
```
