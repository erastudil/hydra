"""
Terminal UI — phosphor-green splash art and the HYDRA wordmark.

Banner composition (one splash, printed once):
  1. revised 3-head TUI creature (no role graffiti)
  2. classic HYDRA title wordmark
  3. single version tagline

Help uses the wordmark alone so usage text is not buried under art.
"""

import os
import sys
from typing import Optional

from hydra_cli._version import __version__

# ANSI Escape Codes for Phosphor Terminal Green
GREEN_BRIGHT = "\033[38;5;46m"
GREEN_MID = "\033[38;5;40m"
GREEN_DARK = "\033[38;5;34m"
GREEN_DIM = "\033[38;5;28m"
GREEN_BOLD = "\033[1;38;5;46m"
RESET = "\033[0m"

# Classic title wordmark — keep this; it is the brand signal under the TUI art.
HYDRA_LOGO_ASCII = r"""
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
"""

# Revised 3-head TUI splash. Clean creature only — no HERMES/PI/role labels.
# Pure ASCII so the Windows .bat / cmd launcher never mojibakes the splash.
HYDRA_TUI_3_HEADS = r"""
                         __====-_          _-====__
                   _--~~~       ~~--_  _--~~       ~~~--_
                _-~                       ~~              ~-_
              .~     (\___/)   (\___/)   (\___/)            ~.
             /      ( 0   0 ) ( o   o ) ( 0   0 )             \
            |        \  =  /   \  v  /   \  =  /               |
            |         '--'      '--'      '--'                 |
             \        .-------------------------------.       /
              ~-._     \                             /    _.-~
                  `--.  \         H Y D R A         /  .--'
                      `'--..___________________..--'`
"""

# Compact mark for tight terminals
HYDRA_TUI_3_HEADS_COMPACT = r"""
              (\___/)   (\___/)   (\___/)
              ( 0 0 )   ( o o )   ( 0 0 )
               \ = /     \ v /     \ = /
                '-'       '-'       '-'
             .-----------------------------.
             |            HYDRA            |
             '-----------------------------'
"""

# Legacy exports kept so older imports do not explode. Prefer HYDRA_TUI_3_HEADS.
HYDRA_7_HEADS_DETAILED = HYDRA_TUI_3_HEADS
HYDRA_7_HEADS_MONSTER = HYDRA_TUI_3_HEADS_COMPACT


def supports_color() -> bool:
    """Check if stdout supports ANSI color output."""
    if os.environ.get("NO_COLOR") or os.environ.get("HYDRA_NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def colorize(text: str, color_code: str = GREEN_BRIGHT) -> str:
    """Wrap text in ANSI color if supported."""
    if not supports_color():
        return text
    return f"{color_code}{text}{RESET}"


def get_help_header(version: Optional[str] = None) -> str:
    """Compact wordmark used above help/usage. No creature art."""
    version = version or __version__
    header = HYDRA_LOGO_ASCII.rstrip()
    tagline = f"      Sovereign Multi-Headed AI Shell · v{version}"
    if supports_color():
        return f"{GREEN_BOLD}{header}{RESET}\n{GREEN_BRIGHT}{tagline}{RESET}\n"
    return f"{header}\n{tagline}\n"


def get_terminal_banner(detailed: bool = True, version: Optional[str] = None) -> str:
    """One splash: 3-head TUI art + HYDRA wordmark + single tagline.

    Duplication means printing this block twice, or stacking two full creature
    arts. Creature + title wordmark together is intentional branding.
    """
    version = version or __version__
    art = HYDRA_TUI_3_HEADS if detailed else HYDRA_TUI_3_HEADS_COMPACT
    header = HYDRA_LOGO_ASCII.rstrip()
    tagline = f"      Sovereign Multi-Headed AI Shell · v{version}"
    if supports_color():
        return (
            f"{GREEN_MID}{art}{RESET}\n"
            f"{GREEN_BOLD}{header}{RESET}\n"
            f"{GREEN_BRIGHT}{tagline}{RESET}\n"
        )
    return f"{art}\n{header}\n{tagline}\n"


def print_banner(detailed: bool = True, version: Optional[str] = None) -> None:
    """Print the Hydra TUI splash to stdout once."""
    print(get_terminal_banner(detailed=detailed, version=version))
