"""
Interactive credential onboarding wizard, probe tester, and secure environment manager.
Provides atomic storage to ~/.hydra/.env with safe permissions and zero token leakage.
Standard library only.
"""

import getpass
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
import urllib.error
import urllib.request
import uuid
import webbrowser

from hydra_cli.config import hydra_home

# Provider onboarding directory with registration URLs and required keys
PROVIDER_DIRECTORY: Dict[str, Dict[str, Any]] = {
    "openrouter": {
        "name": "OpenRouter",
        "description": "Frontier model aggregator (Claude Opus 5.5, Sonnet 5.5, GPT-6.1 Sol, Free Forge)",
        "keys": ["OPENROUTER_API_KEY"],
        "registration_url": "https://openrouter.ai/keys",
        "probe_url": "https://openrouter.ai/api/v1/auth/key",
    },
    "cloudflare": {
        "name": "Cloudflare Workers AI",
        "description": "Zero-cost Workers AI inference (@cf/meta/llama-3.3-70b-instruct-fp8-fast)",
        "keys": ["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID"],
        "registration_url": "https://dash.cloudflare.com/",
        "probe_url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai",
    },
    "vercel": {
        "name": "Vercel AI Gateway",
        "description": "Vercel AI Gateway for fast Anthropic and OpenAI routing",
        "keys": ["AI_GATEWAY_API_KEY"],
        "registration_url": "https://vercel.com/docs/ai-gateway",
        "probe_url": None,
    },
    "cheaperinference": {
        "name": "CheaperInference",
        "description": "Ultra low-cost GLM-5.3, GLM-4.7, and DeepSeek endpoints",
        "keys": ["CHEAPERINFERENCE_API_KEY"],
        "registration_url": "https://cheaperinference.com/",
        "probe_url": None,
    },
    "runpod": {
        "name": "RunPod",
        "description": "Dedicated serverless GPU endpoints and vLLM clusters",
        "keys": ["RUNPOD_API_KEY"],
        "optional_keys": ["RUNPOD_ENDPOINT_ID", "RUNPOD_ENDPOINT_URL"],
        "registration_url": "https://www.runpod.io/console/serverless",
        "probe_url": None,
    },
    "modal": {
        "name": "Modal",
        "description": "Serverless container compute and open-weights model serving",
        "keys": ["MODAL_ENDPOINT_URL"],
        "optional_keys": ["MODAL_API_KEY"],
        "registration_url": "https://modal.com/",
        "probe_url": None,
    },
    "huggingface": {
        "name": "Hugging Face",
        "description": "Serverless Inference API and Hugging Face Router",
        "keys": ["HF_TOKEN"],
        "registration_url": "https://huggingface.co/settings/tokens",
        "probe_url": "https://huggingface.co/api/whoami-v2",
    },
}


def mask_secret(secret: str) -> str:
    """Mask secret string ensuring zero credential leakage in logs and terminal."""
    s = (secret or "").strip()
    if not s:
        return "[NOT SET]"
    if len(s) <= 8:
        return "********"
    return f"{s[:4]}...{s[-4:]}"


def get_masked_input(prompt_text: str = "API Key: ") -> str:
    """Secure credential input using getpass with raw console fallback."""
    try:
        val = getpass.getpass(prompt_text)
    except Exception:
        val = input(prompt_text)
    return val.strip()


def open_registration_page(provider_id: str) -> bool:
    """Launch provider registration portal via standard library webbrowser."""
    info = PROVIDER_DIRECTORY.get(provider_id.lower().strip())
    if not info or not info.get("registration_url"):
        return False
    try:
        return bool(webbrowser.open(info["registration_url"]))
    except Exception:
        return False


def probe_credential(
    provider_id: str,
    value: str,
    extra: Optional[Dict[str, str]] = None,
    timeout: float = 3.0,
) -> Tuple[bool, str]:
    """
    Probe credential before storage via format validation and lightweight HTTP test.
    Never leaks secret value into output message.
    """
    val = (value or "").strip()
    if not val:
        return False, "Empty credential provided."

    pid = provider_id.lower().strip()
    if pid not in PROVIDER_DIRECTORY:
        return len(val) >= 4, "Format verified."

    if pid == "openrouter":
        if len(val) < 16:
            return False, "OpenRouter key too short (minimum 16 characters required)."
        try:
            req = urllib.request.Request(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {val}", "User-Agent": "Hydra-Auth"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, "OpenRouter token verified (HTTP 200 OK)."
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                return False, f"OpenRouter rejected token (HTTP {he.code} Unauthorized)."
            return True, f"Format valid (HTTP {he.code})."
        except Exception:
            return True, "Format valid (network offline or probe timed out)."
        return True, "Format valid."

    elif pid == "cloudflare":
        if len(val) < 15:
            return False, "Cloudflare API token too short (minimum 15 characters required)."
        acct_id = (extra or {}).get("CLOUDFLARE_ACCOUNT_ID", "").strip() if extra else ""
        if acct_id and len(acct_id) < 15:
            return False, "Cloudflare Account ID format invalid."
        if acct_id:
            try:
                probe_url = f"https://api.cloudflare.com/client/v4/accounts/{acct_id}/ai"
                req = urllib.request.Request(
                    probe_url,
                    headers={"Authorization": f"Bearer {val}", "User-Agent": "Hydra-Auth"},
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status == 200:
                        return True, "Cloudflare credentials verified (HTTP 200 OK)."
            except urllib.error.HTTPError as he:
                if he.code in (401, 403):
                    return False, f"Cloudflare rejected credentials (HTTP {he.code} Unauthorized)."
                return True, f"Format valid (HTTP {he.code})."
            except Exception:
                return True, "Format valid (network offline or probe timed out)."
        return True, "Format valid."

    elif pid == "huggingface":
        if len(val) < 10:
            return False, "Hugging Face token format invalid (minimum 10 characters required)."
        try:
            req = urllib.request.Request(
                "https://huggingface.co/api/whoami-v2",
                headers={"Authorization": f"Bearer {val}", "User-Agent": "Hydra-Auth"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, "Hugging Face token verified (HTTP 200 OK)."
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                return False, f"Hugging Face rejected token (HTTP {he.code} Unauthorized)."
            return True, f"Format valid (HTTP {he.code})."
        except Exception:
            return True, "Format valid (network offline or probe timed out)."
        return True, "Format valid."

    elif pid == "modal":
        if not (val.startswith("http://") or val.startswith("https://")):
            return False, "Modal endpoint must be a valid HTTP or HTTPS URL."
        return True, "Format valid."

    elif pid in ("vercel", "cheaperinference", "runpod"):
        if len(val) < 8:
            return False, f"{PROVIDER_DIRECTORY[pid]['name']} token format invalid (too short)."
        return True, "Format valid."

    return len(val) >= 4, "Format valid."


def save_credentials(new_keys: Dict[str, str], env_path: Optional[str] = None) -> str:
    """
    Atomically store credentials to ~/.hydra/.env with safe permissions (0o600).
    Preserves existing lines, comments, and structure in .env file.
    Updates running process os.environ. Zero secret leakage into stdout or logs.
    """
    target_path = env_path or os.path.join(hydra_home(), ".env")
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)

    existing_lines: List[str] = []
    key_line_map: Dict[str, int] = {}

    if os.path.isfile(target_path):
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                existing_lines = f.read().splitlines()
        except OSError:
            existing_lines = []

    for idx, raw in enumerate(existing_lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k = line.split("=", 1)[0].strip()
        if k:
            key_line_map[k] = idx

    for k, v in new_keys.items():
        clean_k = k.strip()
        clean_v = v.strip()
        if not clean_k:
            continue
        entry = f"{clean_k}={clean_v}"
        if clean_k in key_line_map:
            existing_lines[key_line_map[clean_k]] = entry
        else:
            existing_lines.append(entry)
            key_line_map[clean_k] = len(existing_lines) - 1
        os.environ[clean_k] = clean_v

    tmp_path = os.path.join(target_dir, f".env.tmp_{uuid.uuid4().hex}")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write("\n".join(existing_lines) + "\n")
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, target_path)
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return target_path


def get_auth_status(env_path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Inspect configured providers without revealing secret token contents.
    """
    env_keys: Dict[str, str] = {}
    target = env_path or os.path.join(hydra_home(), ".env")
    if os.path.isfile(target):
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                for raw in f.read().splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):]
                    k, v = line.split("=", 1)
                    env_keys[k.strip()] = v.strip()
        except OSError:
            pass

    status: Dict[str, Dict[str, Any]] = {}
    for pid, info in PROVIDER_DIRECTORY.items():
        keys_status: Dict[str, Dict[str, Any]] = {}
        all_required_set = True

        for k in info.get("keys", []):
            val = (env_keys.get(k) if env_path is not None else (os.environ.get(k) or env_keys.get(k))) or ""
            is_set = bool(val.strip())
            if not is_set:
                all_required_set = False
            keys_status[k] = {
                "set": is_set,
                "masked": mask_secret(val) if is_set else "[NOT SET]",
            }

        for k in info.get("optional_keys", []):
            val = (env_keys.get(k) if env_path is not None else (os.environ.get(k) or env_keys.get(k))) or ""
            is_set = bool(val.strip())
            keys_status[k] = {
                "set": is_set,
                "masked": mask_secret(val) if is_set else "[NOT SET]",
                "optional": True,
            }

        status[pid] = {
            "name": info["name"],
            "configured": all_required_set,
            "description": info["description"],
            "registration_url": info["registration_url"],
            "keys": keys_status,
        }

    return status


def print_auth_status(env_path: Optional[str] = None) -> None:
    """Print human-readable table of provider credentials without leaking secrets."""
    from hydra_cli.ui import GREEN_BOLD, GREEN_BRIGHT, GREEN_MID, RESET, supports_color

    color_on = supports_color()
    c_bold = GREEN_BOLD if color_on else ""
    c_bright = GREEN_BRIGHT if color_on else ""
    c_mid = GREEN_MID if color_on else ""
    c_reset = RESET if color_on else ""

    statuses = get_auth_status(env_path)
    target = env_path or os.path.join(hydra_home(), ".env")

    sys.stdout.write(f"\n{c_bold}=== Hydra Model Provider Credentials ({target}) ==={c_reset}\n")
    for pid, s in statuses.items():
        tag = f"{c_bright}[CONFIGURED]{c_reset}" if s["configured"] else f"{c_mid}[NOT SET]{c_reset}"
        name = s["name"]
        sys.stdout.write(f"  * {name:<22} {tag}\n")
        for key_name, kinfo in s["keys"].items():
            opt_tag = " (optional)" if kinfo.get("optional") else ""
            sys.stdout.write(f"      - {key_name}{opt_tag}: {kinfo['masked']}\n")
    sys.stdout.write(f"\n{c_mid}Run 'hydra auth' to configure or update credentials interactively.{c_reset}\n\n")
    sys.stdout.flush()


def auth_wizard(provider_filter: Optional[str] = None, env_path: Optional[str] = None) -> int:
    """Interactive terminal credential onboarding wizard for hydra auth and hydra setup."""
    from hydra_cli.ui import GREEN_BOLD, GREEN_BRIGHT, GREEN_MID, RESET, supports_color

    color_on = supports_color()
    c_bold = GREEN_BOLD if color_on else ""
    c_bright = GREEN_BRIGHT if color_on else ""
    c_mid = GREEN_MID if color_on else ""
    c_reset = RESET if color_on else ""

    sys.stdout.write(f"\n{c_bold}================================================================================{c_reset}\n")
    sys.stdout.write(f"  {c_bright}HYDRA CREDENTIAL ONBOARDING WIZARD{c_reset} // Sovereign Provider Access\n")
    sys.stdout.write(f"{c_bold}================================================================================{c_reset}\n\n")
    sys.stdout.flush()

    providers = list(PROVIDER_DIRECTORY.items())
    if provider_filter:
        filt = provider_filter.lower().strip()
        matched = [(pid, info) for pid, info in providers if filt in pid or filt in info["name"].lower()]
        if matched:
            providers = matched

    if not provider_filter:
        sys.stdout.write("Select provider to configure:\n")
        for idx, (pid, info) in enumerate(providers, 1):
            sys.stdout.write(f"  [{idx}] {info['name']} - {info['description']}\n")
        sys.stdout.write("  [A] Configure All\n")
        sys.stdout.write("  [S] View Authentication Status\n")
        sys.stdout.write("  [Q] Quit\n\n")
        sys.stdout.flush()

        try:
            choice = input(f"{c_bright}Choice [1-{len(providers)}, A, S, Q]{c_reset}: ").strip()
        except (KeyboardInterrupt, EOFError):
            sys.stdout.write("\nAborted.\n")
            return 0

        choice_upper = choice.upper()
        if choice_upper in ("Q", "QUIT", "EXIT"):
            return 0
        elif choice_upper in ("S", "STATUS"):
            print_auth_status(env_path)
            return 0
        elif choice_upper in ("A", "ALL"):
            selected_providers = providers
        else:
            try:
                num = int(choice)
                if 1 <= num <= len(providers):
                    selected_providers = [providers[num - 1]]
                else:
                    sys.stderr.write("Invalid selection.\n")
                    return 1
            except ValueError:
                matched = [(pid, info) for pid, info in providers if choice.lower() in pid]
                if matched:
                    selected_providers = matched
                else:
                    sys.stderr.write("Invalid choice.\n")
                    return 1
    else:
        selected_providers = providers

    updates: Dict[str, str] = {}
    for pid, info in selected_providers:
        sys.stdout.write(f"\n{c_bold}--- Configuring {info['name']} ---{c_reset}\n")
        sys.stdout.write(f"Description: {info['description']}\n")
        sys.stdout.write(f"Portal URL : {info['registration_url']}\n")
        sys.stdout.flush()

        try:
            open_browser = input(f"Open registration page in browser? [y/N]: ").strip().lower()
            if open_browser in ("y", "yes"):
                open_registration_page(pid)
                sys.stdout.write(f"{c_mid}Opened {info['registration_url']} in default browser.{c_reset}\n")
        except (KeyboardInterrupt, EOFError):
            sys.stdout.write("\nAborted.\n")
            return 0

        prov_values: Dict[str, str] = {}
        for key in info.get("keys", []):
            current_val = os.environ.get(key, "")
            curr_hint = f" [current: {mask_secret(current_val)}]" if current_val else ""
            try:
                entered = get_masked_input(f"Enter {key}{curr_hint}: ")
            except (KeyboardInterrupt, EOFError):
                sys.stdout.write("\nAborted.\n")
                return 0
            if entered:
                prov_values[key] = entered
            elif current_val:
                prov_values[key] = current_val

        # Primary key probe check
        primary_key = info.get("keys", [None])[0]
        if primary_key and primary_key in prov_values:
            val_to_probe = prov_values[primary_key]
            is_valid, msg = probe_credential(pid, val_to_probe, extra=prov_values)
            if not is_valid:
                sys.stderr.write(f"{c_mid}[Probe Warning]: {msg}{c_reset}\\n")
                try:
                    confirm = input("Store this credential anyway? [y/N]: ").strip().lower()
                    if confirm not in ("y", "yes"):
                        continue
                except (KeyboardInterrupt, EOFError):
                    return 0
            else:
                sys.stdout.write(f"{c_bright}[Probe Check]: {msg}{c_reset}\n")

        updates.update(prov_values)

    if updates:
        saved_path = save_credentials(updates, env_path=env_path)
        sys.stdout.write(f"\n{c_bright}[SUCCESS] Stored {len(updates)} credential(s) safely to {saved_path}.{c_reset}\n\n")
    else:
        sys.stdout.write(f"\n{c_mid}Zero credential modifications recorded.{c_reset}\n\n")

    return 0


def execute_auth_command(args: List[str]) -> int:
    """CLI handler for 'hydra auth' subcommands."""
    if not args:
        return auth_wizard()

    sub = args[0].lower().strip()
    if sub in ("status", "list", "check", "--status"):
        print_auth_status()
        return 0
    elif sub in ("setup", "wizard", "configure", "login"):
        filt = args[1] if len(args) > 1 else None
        return auth_wizard(provider_filter=filt)
    elif sub == "test":
        if len(args) < 2:
            sys.stderr.write("Usage: hydra auth test <provider_id>\n")
            return 1
        target_pid = args[1].lower().strip()
        info = PROVIDER_DIRECTORY.get(target_pid)
        if not info:
            sys.stderr.write(f"Unknown provider '{target_pid}'. Available: {', '.join(PROVIDER_DIRECTORY.keys())}\n")
            return 1
        primary_key = info["keys"][0]
        val = os.environ.get(primary_key, "")
        if not val:
            sys.stderr.write(f"No credential found for {info['name']} ({primary_key} not set).\n")
            return 1
        extra = {k: os.environ.get(k, "") for k in info.get("keys", [])}
        ok, msg = probe_credential(target_pid, val, extra=extra)
        status_tag = "[OK]" if ok else "[FAIL]"
        sys.stdout.write(f"{status_tag} {info['name']} probe: {msg}\n")
        return 0 if ok else 1
    elif sub == "set":
        if len(args) < 2:
            sys.stderr.write("Usage: hydra auth set <KEY> [VALUE]\n")
            return 1
        key = args[1].strip()
        val = args[2].strip() if len(args) > 2 else get_masked_input(f"Enter value for {key}: ")
        save_credentials({key: val})
        sys.stdout.write(f"Successfully saved {key} to ~/.hydra/.env\n")
        return 0
    else:
        # Treat argument as provider filter
        return auth_wizard(provider_filter=sub)
