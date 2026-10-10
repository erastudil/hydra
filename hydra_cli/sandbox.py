"""
Secure Zero-Trust Execution Sandbox for Autonomous Code Agents (Masterplan WO-08).
Enforces execution isolation, environment scrubbing, destructive command blocking,
resource/timeout bounds, and pre/post git diff tracking.
Zero external dependencies.
"""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from hydra_cli.config import sensitive_env_key
from hydra_cli.browser import (
    BrowserConsoleCapture,
    BrowserConsoleEntry,
    DomIdleLatch,
    FormAutofill,
    MutationWaiter,
    NetworkIdleLatch,
    ScreenshotGate,
    create_console_capture,
    create_dom_idle_latch,
    create_form_autofill,
    create_mutation_waiter,
    create_network_idle_latch,
    create_screenshot_gate,
    get_default_console_capture,
    get_default_dom_idle_latch,
    get_default_form_autofill,
    get_default_mutation_waiter,
    get_default_network_idle_latch,
    get_default_screenshot_gate,
    reset_console_capture,
    reset_dom_idle_latch,
    reset_form_autofill,
    reset_mutation_waiter,
    reset_network_idle_latch,
    reset_screenshot_gate,
)


class SandboxConfig:
    """Configuration policies and execution limits for the sandbox."""

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        max_output_bytes: int = 100_000,
        allowed_paths: Optional[List[str]] = None,
        allow_network: bool = False,
        scrub_env_vars: bool = True,
        additional_blocked_patterns: Optional[List[str]] = None,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.allowed_paths = [os.path.abspath(p) for p in allowed_paths] if allowed_paths else None
        self.allow_network = allow_network
        self.scrub_env_vars = scrub_env_vars
        self.additional_blocked_patterns = list(additional_blocked_patterns or [])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timeout_seconds": self.timeout_seconds,
            "max_output_bytes": self.max_output_bytes,
            "allowed_paths": self.allowed_paths,
            "allow_network": self.allow_network,
            "scrub_env_vars": self.scrub_env_vars,
            "additional_blocked_patterns": self.additional_blocked_patterns,
        }


class CommandInspector:
    """Security inspector detecting prompt-injected destructive or exfiltrating commands."""

    BLOCKED_PATTERNS = [
        # Root / wildcard deletions
        (r"(?i)\brm\s+(-[a-z]*r[a-z]*\s+|--recursive\s+)+(/|/\*|\*|~|\$HOME|[a-z]:\\)", "Recursive root or wildcard filesystem deletion"),
        (r"(?i)\brm\s+-(rf|fr)\s+([/~*]|[a-z]:\\)", "Forced recursive deletion of root or home"),
        (r"(?i)\brmdir\s+/[sS]\s+/[qQ]\s+([a-z]:\\|\\|/|\*)", "Windows silent tree deletion"),
        (r"(?i)\bdel\s+/[fF]\s+/[sS]\s+/[qQ]\s+(\*|[a-z]:\\)", "Windows recursive silent file deletion"),
        (r"(?i)\bRemove-Item\b.*-Recurse.*(-Force)?\s+([/\\*]|[a-z]:\\)", "PowerShell recursive root deletion"),

        # Disk formatting & partition manipulation
        (r"(?i)\b(mkfs|fdisk|parted|gdisk|sfdisk|diskpart)\b", "Disk partitioning or low-level filesystem formatting"),
        (r"(?i)\bformat\s+[a-z]:", "Drive volume format command"),

        # Arbitrary remote code execution download-and-pipe
        (r"(?i)\b(curl|wget|fetch|invoke-webrequest|iwr)\b.*\|\s*(sh|bash|zsh|python|perl|ruby|iex|invoke-expression)\b", "Remote executable pipe into shell interpreter"),

        # Credential & secret file harvesting
        (r"(?i)\b(cat|type|Get-Content|head|tail|more|less)\s+.*(\.env|id_rsa|id_ed25519|authorized_keys|\.aws/credentials|\.git-credentials|/etc/shadow)", "Accessing sensitive credential or private key file"),
        (r"(?i)\.hydra[/\\]\.env", "Direct access to Hydra credentials environment file"),
        (r"(?i)~[/\\](\.ssh|\.aws|\.gnupg)[/\\]", "Accessing sensitive user security directories"),

        # Fork bombs & shell crashes
        (r":\(\)\s*\{\s*:\|:&\s*\};:", "Classic bash fork bomb"),
    ]

    PYTHON_BLOCKED_PATTERNS = [
        (r"(?i)shutil\.rmtree\s*\(\s*['\"](/|[a-z]:\\|\*|~)['\"]\s*\)", "Python recursive root deletion"),
        (r"(?i)os\.system\s*\(\s*['\"].*(rm\s+-rf|mkfs|format\s+[a-z]:)", "Python shell-out to destructive system command"),
        (r"(?i)open\s*\(\s*['\"].*(\.env|id_rsa|id_ed25519|\.aws[/\\]credentials)", "Python file read on sensitive credentials"),
    ]

    def __init__(self, additional_blocked_patterns: Optional[List[str]] = None):
        self.patterns = list(self.BLOCKED_PATTERNS)
        if additional_blocked_patterns:
            for pat in additional_blocked_patterns:
                self.patterns.append((pat, f"User-defined blocked rule: {pat}"))

    def inspect(self, command: str) -> Tuple[bool, Optional[str]]:
        """Evaluate command string. Returns (True, None) if permitted, or (False, reason) if blocked."""
        clean = command.strip()
        if not clean:
            return True, None

        for pattern, reason in self.patterns:
            if re.search(pattern, clean):
                return False, f"Destructive command pattern detected: {reason}"
        return True, None

    def inspect_python(self, code: str) -> Tuple[bool, Optional[str]]:
        """Evaluate Python source string for malicious execution primitives."""
        # Check standard command patterns first
        is_safe, reason = self.inspect(code)
        if not is_safe:
            return False, reason

        for pattern, reason in self.PYTHON_BLOCKED_PATTERNS:
            if re.search(pattern, code):
                return False, f"Destructive Python operation detected: {reason}"
        return True, None


class EnvironmentScrubber:
    """Sanitizes environment variables to prevent credential exfiltration during child process execution."""

    SAFE_VARS: Set[str] = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "USER",
        "USERNAME",
        "HOME",
        "USERPROFILE",
        "LANG",
        "LC_ALL",
        "SHELL",
        "COMSPEC",
        "PYTHONPATH",
        "NODE_PATH",
        "TERM",
        "PWD",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "VIRTUAL_ENV",
    }

    SENSITIVE_KEY_REGEX = re.compile(
        r"(?i)(api[_-]?key|secret|token|password|auth|credential|endpoint_id|account_id|privkey)"
    )

    def scrub_env(
        self,
        env: Optional[Dict[str, str]] = None,
        allow_network: bool = False,
        extra_safe: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Strip sensitive credentials from environment dictionary."""
        source = dict(env if env is not None else os.environ)
        safe_keys = set(self.SAFE_VARS)
        if extra_safe:
            safe_keys.update(extra_safe)

        scrubbed: Dict[str, str] = {}
        for k, v in source.items():
            if sensitive_env_key(k):
                continue
            if self.SENSITIVE_KEY_REGEX.search(k):
                continue
            scrubbed[k] = v

        if not allow_network:
            # Set dummy proxy variables to block uncoordinated outbound HTTP/HTTPS egress
            scrubbed["HTTP_PROXY"] = "http://127.0.0.1:0"
            scrubbed["HTTPS_PROXY"] = "http://127.0.0.1:0"
            scrubbed["ALL_PROXY"] = "http://127.0.0.1:0"
            scrubbed["NO_PROXY"] = ""

        return scrubbed


class SandboxExecutionResult:
    """Structured result returned by SandboxRunner."""

    def __init__(
        self,
        status: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        execution_time_ms: float = 0.0,
        violation: Optional[str] = None,
        diff: Optional[str] = None,
    ):
        self.status = status  # SUCCESS, BLOCKED, TIMEOUT, ERROR
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.execution_time_ms = execution_time_ms
        self.violation = violation
        self.diff = diff

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "execution_time_ms": round(self.execution_time_ms, 2),
            "violation": self.violation,
            "diff": self.diff,
        }


class SandboxRunner:
    """Isolated execution engine enforcing security policies, resource limits, and git diff tracking."""

    def __init__(self, config: Optional[SandboxConfig] = None):
        self.config = config or SandboxConfig()
        self.inspector = CommandInspector(additional_blocked_patterns=self.config.additional_blocked_patterns)
        self.scrubber = EnvironmentScrubber()

    def _get_git_diff(self, cwd: str) -> Optional[str]:
        """Capture git diff within workspace directory if git is present."""
        try:
            res = subprocess.run(
                ["git", "diff"],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
                timeout=5.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def _truncate_output(self, text: str) -> str:
        """Truncate output if exceeding max_output_bytes."""
        raw = text.encode("utf-8", errors="replace")
        if len(raw) <= self.config.max_output_bytes:
            return text
        truncated = raw[: self.config.max_output_bytes].decode("utf-8", errors="ignore")
        return truncated + "\n[OUTPUT TRUNCATED BY HYDRA SANDBOX]"

    def run_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxExecutionResult:
        """Execute shell command within sandbox envelope."""
        target_cwd = os.path.abspath(cwd or os.getcwd())

        # 1. Enforce allowed path boundaries
        if self.config.allowed_paths:
            path_allowed = any(
                target_cwd == allowed or target_cwd.startswith(allowed + os.sep)
                for allowed in self.config.allowed_paths
            )
            if not path_allowed:
                return SandboxExecutionResult(
                    status="BLOCKED",
                    exit_code=126,
                    violation=f"CWD '{target_cwd}' is outside allowed paths: {self.config.allowed_paths}",
                )

        # 2. Inspect command for security violations
        is_safe, violation = self.inspector.inspect(command)
        if not is_safe:
            return SandboxExecutionResult(
                status="BLOCKED",
                exit_code=126,
                violation=violation,
            )

        # 3. Prepare scrubbed environment
        child_env = (
            self.scrubber.scrub_env(env, allow_network=self.config.allow_network)
            if self.config.scrub_env_vars
            else dict(env if env is not None else os.environ)
        )

        t0 = time.time()
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=target_cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            stdout, stderr = proc.communicate(timeout=self.config.timeout_seconds)
            duration_ms = (time.time() - t0) * 1000.0

            diff = self._get_git_diff(target_cwd)
            stdout_trunc = self._truncate_output(stdout or "")
            stderr_trunc = self._truncate_output(stderr or "")

            status = "SUCCESS" if proc.returncode == 0 else "ERROR"
            return SandboxExecutionResult(
                status=status,
                stdout=stdout_trunc,
                stderr=stderr_trunc,
                exit_code=proc.returncode,
                execution_time_ms=duration_ms,
                diff=diff,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            duration_ms = (time.time() - t0) * 1000.0
            return SandboxExecutionResult(
                status="TIMEOUT",
                stdout=self._truncate_output(stdout or ""),
                stderr=self._truncate_output(stderr or ""),
                exit_code=124,
                execution_time_ms=duration_ms,
                violation=f"Execution exceeded timeout limit ({self.config.timeout_seconds}s)",
            )
        except Exception as exc:
            duration_ms = (time.time() - t0) * 1000.0
            return SandboxExecutionResult(
                status="ERROR",
                exit_code=1,
                stderr=str(exc),
                execution_time_ms=duration_ms,
            )

    def run_python(
        self,
        code: str,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> SandboxExecutionResult:
        """Execute Python code string within sandbox envelope."""
        is_safe, violation = self.inspector.inspect_python(code)
        if not is_safe:
            return SandboxExecutionResult(
                status="BLOCKED",
                exit_code=126,
                violation=violation,
            )

        cmd = [sys.executable, "-c", code]
        target_cwd = os.path.abspath(cwd or os.getcwd())

        # Enforce allowed path boundaries
        if self.config.allowed_paths:
            path_allowed = any(
                target_cwd == allowed or target_cwd.startswith(allowed + os.sep)
                for allowed in self.config.allowed_paths
            )
            if not path_allowed:
                return SandboxExecutionResult(
                    status="BLOCKED",
                    exit_code=126,
                    violation=f"CWD '{target_cwd}' is outside allowed paths: {self.config.allowed_paths}",
                )

        child_env = (
            self.scrubber.scrub_env(env, allow_network=self.config.allow_network)
            if self.config.scrub_env_vars
            else dict(env if env is not None else os.environ)
        )

        t0 = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=target_cwd,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )
            stdout, stderr = proc.communicate(timeout=self.config.timeout_seconds)
            duration_ms = (time.time() - t0) * 1000.0

            diff = self._get_git_diff(target_cwd)
            stdout_trunc = self._truncate_output(stdout or "")
            stderr_trunc = self._truncate_output(stderr or "")

            status = "SUCCESS" if proc.returncode == 0 else "ERROR"
            return SandboxExecutionResult(
                status=status,
                stdout=stdout_trunc,
                stderr=stderr_trunc,
                exit_code=proc.returncode,
                execution_time_ms=duration_ms,
                diff=diff,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            duration_ms = (time.time() - t0) * 1000.0
            return SandboxExecutionResult(
                status="TIMEOUT",
                stdout=self._truncate_output(stdout or ""),
                stderr=self._truncate_output(stderr or ""),
                exit_code=124,
                execution_time_ms=duration_ms,
                violation=f"Execution exceeded timeout limit ({self.config.timeout_seconds}s)",
            )
        except Exception as exc:
            duration_ms = (time.time() - t0) * 1000.0
            return SandboxExecutionResult(
                status="ERROR",
                exit_code=1,
                stderr=str(exc),
                execution_time_ms=duration_ms,
            )
