"""
Create Windows Desktop shortcut for Hydra Desktop application.
Configures target path, module arguments, working directory, and cybernetic icon.
Complies with AGENTS.md genome invariants: zero copula P018, truth gate exit code 0.
"""

from __future__ import annotations

import os
import subprocess
import sys


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

    # Detect pythonw.exe or python.exe
    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    target_exe = pythonw if os.path.isfile(pythonw) else sys.executable

    icon_path = os.path.join(repo_root, "assets", "hydra_desktop.ico")
    if not os.path.isfile(icon_path):
        raise FileNotFoundError(f"Icon asset missing: {icon_path}")

    # Build PowerShell COM automation script
    ps_script = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{target_exe}"
$Shortcut.Arguments = "-m hydra_cli.desktop"
$Shortcut.WorkingDirectory = "{repo_root}"
$Shortcut.IconLocation = "{icon_path},0"
$Shortcut.Description = "Hydra Sovereign Desktop Agent & Computer Control"
$Shortcut.Save()
"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)

    if not os.path.isfile(shortcut_path):
        raise RuntimeError(f"Failed to create shortcut at {shortcut_path}: {res.stderr}")

    print(f"Created shortcut: {shortcut_path}")
    print(f"Target: {target_exe} -m hydra_cli.desktop")
    print(f"Working Directory: {repo_root}")
    print(f"Icon: {icon_path},0")
    return shortcut_path


def verify_shortcut(shortcut_path: str) -> bool:
    """Verify shortcut exists and points to expected target."""
    if not os.path.isfile(shortcut_path):
        return False
    ps_inspect = f"""
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
[PSCustomObject]@{{
    TargetPath = $Shortcut.TargetPath
    Arguments = $Shortcut.Arguments
    WorkingDirectory = $Shortcut.WorkingDirectory
    IconLocation = $Shortcut.IconLocation
}} | ConvertTo-Json
"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_inspect]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    print("Shortcut properties:\n" + res.stdout)
    return "hydra_cli.desktop" in res.stdout


if __name__ == "__main__":
    lnk = create_shortcut()
    valid = verify_shortcut(lnk)
    if not valid:
        sys.exit(1)
    print("Shortcut creation and verification successful with exit code 0.")
