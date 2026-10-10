# computer_use specification

scope : formal specification for coordinate safety bounds, os input protocols, and screen capture.

version : 1.0.0.

dialect : progen instruct.


## coordinate invariants

display domain : width W in pixels, height H in pixels.

coordinate domain : integer pairs (x, y) where 0 <= x < W and 0 <= y < H.

safety margin : integer M >= 0 defining boundary inset.

clamping rule : clamped x = max(M, min(x, W - 1 - M)); clamped y = max(M, min(y, H - 1 - M)).

fenced region : tuple (x1, y1, x2, y2) where min_x <= x <= max_x and min_y <= y <= max_y.

fence enforcement : if coordinate falls within registered fence, then reject action and emit error.

failsafe coordinate : if cursor targets (0, 0) up to (2, 2) when failsafe enabled, then halt execution.


## os input protocols

windows substrate : ctypes.windll.user32 interface for SetCursorPos, mouse_event, and keybd_event.

fallback substrate : virtual display state tracking cursor position, active window, and input history.

mouse event codes : left down 0x0002, left up 0x0004, right down 0x0008, right up 0x0010, wheel 0x0800.

key event codes : virtual key mapping for standard ASCII, navigation, modifiers, and function keys F1 to F12.


## screen capture invariants

output format : portable network graphics PNG bytes.

header invariant : first eight bytes match 0x89504E470D0A1A0A.

synthetic fallback : if physical display surface unavailable, then generate synthetic RGB frame buffer.

encoding format : base64 standard string representation for websocket and http transmission.
