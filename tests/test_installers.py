"""The curl and PowerShell installers must fetch every runtime file.

hydra_cli/__init__.py imports the agent, MCP, sandbox, serve, and voice
modules at package import time. A partial tree makes `hydra` crash before
it can print a version.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def runtime_files():
    names = ["bin/hydra", "bin/hydra.js"]
    cli = ROOT / "hydra_cli"
    for path in sorted(cli.iterdir()):
        if path.is_file() and path.suffix in {".py", ".json"}:
            names.append(f"hydra_cli/{path.name}")
    return names


def sh_files():
    text = (ROOT / "install.sh").read_text(encoding="utf-8")
    match = re.search(r'FILES="(.*?)"', text, re.S)
    assert match, "install.sh is missing its FILES block"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


def ps_files():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")
    match = re.search(r"\$Files = @\((.*?)\)", text, re.S)
    assert match, "install.ps1 is missing its $Files block"
    return re.findall(r"'([^']+)'", match.group(1))


def test_installer_manifests_match_runtime_tree():
    expected = runtime_files()
    assert sh_files() == expected
    assert ps_files() == expected


def test_installer_tree_prints_version(tmp_path):
    dest = tmp_path / "install"
    for rel in sh_files():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["USERPROFILE"] = env["HOME"]
    env.pop("HYDRA_HOME", None)
    env["PYTHONPATH"] = str(dest)
    result = subprocess.run(
        [sys.executable, str(dest / "bin" / "hydra"), "--version"],
        cwd=dest,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"hydra {__import__('hydra_cli').__version__}"
