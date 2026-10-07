"""
Terminal UI, 7-Headed Hydra ASCII Art, and Green Phosphor Styling.
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

HYDRA_LOGO_ASCII = r"""
  ___ ___            .___              
 /   |   \___.__.  __| _/___________   
/    ~    <   |  | / __ |\_  __ \__  \  
\    Y    /\___  |/ /_/ | |  | \// __ \_
 \___|_  / / ____|\____ | |__|  (____  /
       \/  \/          \/            \/ 
"""

# One composition only: the 7 heads. Do not stack the wordmark under this —
# that reads as the banner printing twice.
HYDRA_7_HEADS_DETAILED = r"""
            [1]        [2]        [3]        [4]        [5]        [6]        [7]
           HERMES       PI      ARCHITECT  SOVEREIGN   CODER     AUDITOR   SYNTHESIS
          (\___/)    (\___/)    (\___/)    <(\___/)>   (\___/)    (\___/)    (\___/)
          /0   0\    /o   o\    /^   ^\    { 0   0 }   /^   ^\    /o   o\    /0   0\
         ( ==Y== )  ( ==v== )  ( ==w== )  (  ==X==  ) ( ==w== )  ( ==v== )  ( ==Y== )
          )     (    )     (    )     (   / )     ( \  )     (    )     (    )     (
         /       \  /       \  /       \ ( /       \ )/       \  /       \  /       \
        /   | |   \/   | |   \/   | |   \ V   | |   V /   | |   \/   | |   \/   | |   \
       |    | |        | |        | |    |    | |   |   | |        | |        | |    |
       \    \ \       / /        / /     |    | |   |    \ \        \ \       / /    /
        \    \ \_____/ /        / /      \    | |   /     \ \________\ \_____/ /    /
         \    \_______/        / /        \___/ \__/       \_______/  \_______/    /
          \                   / /          |       |        \                     /
           '.               .' /           |  VII  |         \                  .'
             '.           .'  /            |       |          \               .'
               '---------'   /             /_______\           \   '---------'
                            /             /         \           \
                           (             /   HYDRA   \           )
                            '._________.'|   CORE    |'._________.'
                                         \           /
                                          '---------'
"""

HYDRA_7_HEADS_MONSTER = r"""
                             __====-_                                    _-====__
                       _--~~~  VII   ~--_                            _--~   VII  ~~~--_
                    _-~   [1]     [2]    ~-_                      _-~    [6]     [7]   ~-_
                 _-~     HERMES    PI       ~-_                _-~      AUDITOR SYNTH     ~-_
               .~   __---~~~~--__            ~-______________-~            __--~~~~---__   ~.
              /   .~             ~.           /              \           .~             ~.   \
             /   /     [3] ARCH    \         /    [4] CORE    \         /    [5] CODER   \   \
            |   |   (\___/) (\___/) |       |    <(\___/)>     |       | (\___/) (\___/)   |   |
            |   |   ( 0 0 ) ( o o ) |       |    {  @ @  }     |       | ( o o ) ( 0 0 )   |   |
             \   \   \ = /   \ v /  /        \    \  X  /     /        \  \ v /   \ = /   /   /
              \   '._ '-'     '-' _.'         '._  '-'      _.'         '._ '-'     '-' _.'   /
               ~-._  `'-------'`                 `'------'`                `'-------'`  _.-~
                   `'--..__________________________________________________________..--'`
                                             |  HYDRA-7  |
                                             | SOVEREIGN |
                                              \_________/
"""


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
    """Compact wordmark used above help/usage. Intentionally not the 7-head art."""
    version = version or __version__
    header = HYDRA_LOGO_ASCII.rstrip()
    tagline = f"      Sovereign Multi-Headed AI Shell · v{version}"
    body = f"{header}\n{tagline}\n"
    if supports_color():
        return f"{GREEN_BOLD}{header}{RESET}\n{GREEN_BRIGHT}{tagline}{RESET}\n"
    return body


def get_terminal_banner(detailed: bool = True, version: Optional[str] = None) -> str:
    """Construct a single Hydra banner composition with green terminal styling.

    `detailed=True` prints the 7 heads once. `detailed=False` prints the compact
    monster mark. The HYDRA wordmark is never stacked under either — that looked
    like the banner printing twice.
    """
    version = version or __version__
    art = HYDRA_7_HEADS_DETAILED if detailed else HYDRA_7_HEADS_MONSTER
    tagline = f"       Sovereign Multi-Headed AI Shell · v{version}"
    if supports_color():
        return f"{GREEN_MID}{art}{RESET}\n{GREEN_BRIGHT}{tagline}{RESET}\n"
    return f"{art}\n{tagline}\n"


def print_banner(detailed: bool = True, version: Optional[str] = None) -> None:
    """Print the 7-headed Hydra banner to stdout."""
    print(get_terminal_banner(detailed=detailed, version=version))
