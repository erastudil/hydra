"""
Create Windows Desktop shortcuts for Hydra Desktop and Hydra TUI applications.
Configures target path, module arguments, working directory, and cybernetic icon.
Complies with AGENTS.md genome invariants: zero copula P018, truth gate exit code 0.
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Tuple


def get_desktop_dir() -> str:
    """Resolve Windows Desktop directory path."""
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    desktop_dir = os.path.join(user_profile, "Desktop")
    if not os.path.isdir(desktop_dir):
        os.makedirs(desktop_dir, exist_ok=True)
    return desktop_dir


def create_shortcut(
    shortcut_name: str = "Hydra Desktop.lnk",
    repo_root: str | None = None,
) -> str:
    """Create Windows shortcut pointing to Hydra Desktop web desk launcher."""
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    desktop_dir = get_desktop_dir()
    shortcut_path = os.path.join(desktop_dir, shortcut_name)

    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    target_exe = pythonw if os.path.isfile(pythonw) else sys.executable

    icon_path = os.path.join(repo_root, "assets", "hydra_desktop.ico")
    if not os.path.isfile(icon_path):
        raise FileNotFoundError(f"Icon asset missing: {icon_path}")

    ps_script = (
        "$WshShell = New-Object -ComObject WScript.Shell\n"
        f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
        f'$Shortcut.TargetPath = "{target_exe}"\n'
        '$Shortcut.Arguments = "-m hydra_cli.desktop"\n'
        f'$Shortcut.WorkingDirectory = "{repo_root}"\n'
        f'$Shortcut.IconLocation = "{icon_path},0"\n'
        '$Shortcut.Description = "Hydra Sovereign Desktop Agent & Computer Control"\n'
        "$Shortcut.Save()\n"
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    if not os.path.isfile(shortcut_path):
        raise RuntimeError(f"Failed to create shortcut at {shortcut_path}")

    print(f"Created desktop app shortcut: {shortcut_path}")
    print(f"Target: {target_exe} -m hydra_cli.desktop")
    print(f"Icon: {icon_path},0")
    return shortcut_path


def create_tui_shortcut(
    shortcut_name: str = "Hydra TUI.lnk",
    repo_root: str | None = None,
) -> str:
    """Create Windows shortcut pointing to the Hydra Sovereign Terminal launcher (launch/bin/hydra-terminal.bat)."""
    if repo_root is None:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    desktop_dir = get_desktop_dir()
    shortcut_path = os.path.join(desktop_dir, shortcut_name)

    launcher = os.path.join(repo_root, "launch", "bin", "hydra-terminal.bat")
    if not os.path.isfile(launcher):
        raise FileNotFoundError(f"Sovereign terminal launcher missing: {launcher}")

    icon_path = os.path.join(repo_root, "assets", "hydra_desktop.ico")
    if not os.path.isfile(icon_path):
        raise FileNotFoundError(f"Icon asset missing: {icon_path}")

    ps_script = (
        "$WshShell = New-Object -ComObject WScript.Shell\n"
        f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
        f'$Shortcut.TargetPath = "{launcher}"\n'
        '$Shortcut.Arguments = ""\n'
        f'$Shortcut.WorkingDirectory = "{os.path.dirname(repo_root)}"\n'
        f'$Shortcut.IconLocation = "{icon_path},0"\n'
        '$Shortcut.Description = "Hydra Sovereign Terminal"\n'
        "$Shortcut.Save()\n"
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    subprocess.run(cmd, capture_output=True, text=True, check=True)

    if not os.path.isfile(shortcut_path):
        raise RuntimeError(f"Failed to create TUI shortcut at {shortcut_path}")

    print(f"Created TUI shortcut: {shortcut_path}")
    print(f"Target: {launcher}")
    print(f"Icon: {icon_path},0")
    return shortcut_path


def refresh_shell_icons() -> None:
    """Notify Windows shell to reload icon cache and refresh desktop view."""
    ps_notify = (
        "Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class ShellNotifier { [DllImport(\"shell32.dll\")] public static extern void SHChangeNotify(int wEventId, int uFlags, IntPtr dwItem1, IntPtr dwItem2); }'; [ShellNotifier]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)"
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_notify]
    try:
        subprocess.run(cmd, capture_output=True, text=True, check=True)
        print("Windows shell notified of desktop shortcut and icon updates.")
    except Exception as exc:
        print(f"Shell notification warning: {exc}")


def verify_shortcut(shortcut_path: str) -> bool:
    """Verify shortcut exists and points to expected target."""
    if not os.path.isfile(shortcut_path):
        return False
    ps_inspect = (
        "$WshShell = New-Object -ComObject WScript.Shell\n"
        f'$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")\n'
        "[PSCustomObject]@{\n"
        "    TargetPath = $Shortcut.TargetPath\n"
        "    Arguments = $Shortcut.Arguments\n"
        "    WorkingDirectory = $Shortcut.WorkingDirectory\n"
        "    IconLocation = $Shortcut.IconLocation\n"
        "} | ConvertTo-Json\n"
    )
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_inspect]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    print("Shortcut properties:\n" + res.stdout)
    return ("hydra_cli.desktop" in res.stdout) or ("hydra-terminal.bat" in res.stdout)


def create_all_shortcuts(repo_root: str | None = None) -> Tuple[str, str]:
    """Create both desktop app and TUI shortcuts with refresh."""
    desktop_lnk = create_shortcut(repo_root=repo_root)
    tui_lnk = create_tui_shortcut(repo_root=repo_root)
    refresh_shell_icons()
    return desktop_lnk, tui_lnk


if __name__ == "__main__":
    d_lnk, t_lnk = create_all_shortcuts()
    if not verify_shortcut(d_lnk) or not verify_shortcut(t_lnk):
        sys.exit(1)
    print("All desktop shortcuts verified successfully with exit code 0.")