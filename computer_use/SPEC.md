# computer_use specification

scope : formal specification for coordinate safety bounds, os input protocols, composite primitives, and screen capture.

version : 1.1.0.

dialect : progen instruct.


## coordinate invariants

display domain : width W in pixels, height H in pixels.

coordinate domain : integer pairs (x, y) where 0 <= x < W and 0 <= y < H.

safety margin : integer M >= 0 defining boundary inset.

clamping rule : clamped x = max(M, min(x, W - 1 - M)); clamped y = max(M, min(y, H - 1 - M)).

fenced region : tuple (x1, y1, x2, y2) where min_x <= x <= max_x and min_y <= y <= max_y.

fence enforcement : if coordinate falls within registered fence, then reject action and emit error.

failsafe coordinate : if cursor targets (0, 0) up to (2, 2) when failsafe enabled, then halt execution.


## composite action invariants

form fill atomicity : if form field selector fails, then record partial progress and emit structured error without process abort.

scroll bounding : max_scrolls integer bound guarantees termination; each step advances viewport and audits selector visibility.

table extraction : returns typed dictionary declaring headers list, rows matrix, and total row count.

drag boundary verification : validates and clips both source and destination coordinates prior to mouse button engagement.

window bounds mutation : coordinates clamped within primary desktop or virtual display envelope.

key sequence interval : inter-keystroke interval bounded by lower clamp 0.005 seconds to prevent input buffer overflow.


## screen capture invariants

output format : portable network graphics PNG bytes.

header invariant : first eight bytes match 0x89504E470D0A1A0A.

synthetic fallback : if physical display surface unavailable, then generate synthetic RGB frame buffer.

active window bounding : extracts foreground window rectangle, clamps coordinates to display dimensions, and captures bounded region.
