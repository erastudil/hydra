"""Instruments: browser, image header, audio container, SVG drawing.

Each one reports what it read. A missing model stays a named gap.
"""

from __future__ import annotations

import ipaddress
import os
import re
import shutil
import signal
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac", ".m4a"}

COLORS = {
    "red": "#c0392b",
    "blue": "#2471a3",
    "green": "#1e8449",
    "black": "#1c1c1c",
    "white": "#f7f7f7",
    "gold": "#b7950b",
    "gray": "#7f8c8d",
    "grey": "#7f8c8d",
    "orange": "#d35400",
    "purple": "#6c3483",
}


def _inet_aton(name: str):
    """Browser-style IPv4: 127.1, 2130706433, and dotted quads. Leading zeros are refused by the caller."""
    parts = name.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    if any(len(part) > 1 and part.startswith("0") for part in parts):
        return "ambiguous"
    nums = [int(part) for part in parts]
    if len(nums) == 1:
        value = nums[0]
    elif len(nums) == 2 and nums[1] <= 0xFFFFFF:
        value = (nums[0] << 24) | nums[1]
    elif len(nums) == 3 and nums[1] <= 0xFF and nums[2] <= 0xFFFF:
        value = (nums[0] << 24) | (nums[1] << 16) | nums[2]
    elif len(nums) == 4 and all(num <= 0xFF for num in nums):
        value = (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]
    else:
        return None
    if value > 0xFFFFFFFF:
        return None
    return ipaddress.IPv4Address(value)


def _blocked_ip(ip) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if isinstance(ip, ipaddress.IPv4Address) and int(ip) & 0xFFC00000 == 0x64400000:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _private_host(host: str) -> bool:
    name = host.lower().rstrip(".").strip("[]")
    if not name or name in {"localhost", "localhost.localdomain"} or name.endswith(".local") or name.endswith(".localhost"):
        return True
    try:
        return _blocked_ip(ipaddress.ip_address(name))
    except ValueError:
        parsed = _inet_aton(name)
        if parsed == "ambiguous":
            return True
        if isinstance(parsed, ipaddress.IPv4Address):
            return _blocked_ip(parsed)
    return False


def public_https(url: str) -> Optional[str]:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if _private_host(parsed.hostname):
        return None
    return parsed.geturl()


def _strip_html(html: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.I | re.S)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    body = re.sub(r"(?is)<[^>]+>", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return title, body[:1500]


def browse(url: str, timeout: int = 25) -> dict:
    """Open a public page. Playwright if it is installed, otherwise system Chrome."""
    target = public_https(url)
    if not target:
        return {
            "ok": False,
            "act": "silence-gap",
            "source": "tool:browse",
            "answer": "Browse takes a public http or https URL. Private hosts are refused.",
            "next": "give a public https URL",
        }
    if shutil.which("python3") and _playwright_importable():
        return _browse_playwright(target, timeout)
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:browse",
            "answer": "No browser instrument. Install Playwright or Chrome.",
            "next": "pip install playwright, or install google-chrome",
        }
    return _browse_chrome(chrome, target, timeout)


def _playwright_importable() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def _browse_playwright(url: str, timeout: int) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            page = browser.new_page()

            def _allow(route):
                if public_https(route.request.url):
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", _allow)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if not public_https(page.url):
                return {
                    "ok": False,
                    "act": "silence-gap",
                    "source": "tool:playwright",
                    "answer": "The page left the public web. The body was not read.",
                    "next": "give a public https URL that stays public",
                }
            title = page.title()
            text = page.inner_text("body")[:1500]
        finally:
            browser.close()
    shown = re.sub(r"\s+", " ", text).strip()
    return {
        "ok": True,
        "act": "say",
        "source": "tool:playwright",
        "answer": f"Page: {title or url}\n{shown}",
        "next": "",
    }


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        proc.kill()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _read_dom(proc: subprocess.Popen, timeout: int) -> str:
    """Chrome dump-dom prints the page and then often refuses to exit. Read until </html>."""
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    os.set_blocking(fd, False)
    chunks: list[bytes] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            piece = os.read(fd, 65536)
        except BlockingIOError:
            piece = b""
        if piece:
            chunks.append(piece)
            if b"</html>" in b"".join(chunks).lower():
                break
        elif proc.poll() is not None:
            break
        else:
            time.sleep(0.1)
    _kill_process_group(proc)
    return b"".join(chunks).decode("utf-8", "replace")


def _browse_chrome(chrome: str, url: str, timeout: int) -> dict:
    profile = Path(tempfile.mkdtemp(prefix="alice-chrome-"))
    proc = subprocess.Popen(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            "--dump-dom",
            url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    stdout = _read_dom(proc, timeout)
    if "<html" not in stdout.lower():
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:chrome",
            "answer": "The browser did not return a page.",
            "next": "retry the public URL, or install Playwright",
        }
    title, text = _strip_html(stdout)
    return {
        "ok": True,
        "act": "say",
        "source": "tool:chrome",
        "answer": f"Page: {title or url}\n{text}",
        "next": "",
    }


def image_header(path: Path) -> Optional[dict]:
    data = path.read_bytes()[:64]
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        return {"format": "png", "width": width, "height": height}
    if data.startswith(b"\xff\xd8"):
        # Scan the file for a SOF marker. Enough for a size, not a caption.
        blob = path.read_bytes()
        index = 2
        while index + 9 < len(blob):
            if blob[index] != 0xFF:
                break
            marker = blob[index + 1]
            if marker in {0xC0, 0xC1, 0xC2}:
                height, width = struct.unpack(">HH", blob[index + 5 : index + 9])
                return {"format": "jpeg", "width": width, "height": height}
            if index + 4 > len(blob):
                break
            size = struct.unpack(">H", blob[index + 2 : index + 4])[0]
            if size < 2:
                break
            index += 2 + size
    if data.startswith((b"GIF87a", b"GIF89a")) and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return {"format": "gif", "width": width, "height": height}
    return None


def see_image(path: Path) -> dict:
    if not path.is_file():
        return {
            "ok": False,
            "act": "silence-gap",
            "source": "tool:see",
            "answer": f"No image at {path}.",
            "next": "give a png, jpg, gif, or webp path",
        }
    if path.suffix.lower() not in IMAGE_EXT:
        return {
            "ok": False,
            "act": "silence-gap",
            "source": "tool:see",
            "answer": "See reads png, jpg, gif, or webp.",
            "next": "give an image path",
        }
    header = image_header(path)
    if not header:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:see",
            "answer": "The file does not have a png, jpeg, or gif header I can read.",
            "next": "export the picture as png",
        }
    tesseract = shutil.which("tesseract")
    ocr = ""
    if tesseract:
        proc = subprocess.run(
            [tesseract, str(path), "stdout"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        ocr = re.sub(r"\s+", " ", proc.stdout).strip()[:400]
    lines = [
        f"{header['format']} {header['width']}x{header['height']}",
    ]
    next_shelf = "a vision model for object names"
    if ocr:
        lines.append(f"Printed text: {ocr}")
    else:
        lines.append("No OCR instrument. Object names were not read.")
        if not tesseract:
            next_shelf = "tesseract for printed text, then a vision model for objects"
    return {
        "ok": True,
        "act": "say",
        "source": "tool:see",
        "answer": " ".join(lines),
        "next": next_shelf,
    }


def hear_audio(path: Path) -> dict:
    if not path.is_file() or path.suffix.lower() not in AUDIO_EXT:
        return {
            "ok": False,
            "act": "silence-gap",
            "source": "tool:hear",
            "answer": "Hear reads a wav, mp3, ogg, flac, or m4a file.",
            "next": "give an audio path",
        }
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:hear",
            "answer": "ffprobe is not installed.",
            "next": "install ffmpeg",
        }
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,sample_rate,channels",
            "-of",
            "default=noprint_wrappers=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if proc.returncode != 0:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:ffprobe",
            "answer": (proc.stderr or "ffprobe failed").strip()[:300],
            "next": "confirm the file is audio",
        }
    summary = re.sub(r"\s+", " ", proc.stdout).strip()
    whisper = shutil.which("whisper")
    if whisper:
        return {
            "ok": True,
            "act": "say",
            "source": "tool:ffprobe",
            "answer": summary + " Whisper is installed; run it on this file for words.",
            "next": f"whisper {path}",
        }
    return {
        "ok": True,
        "act": "say",
        "source": "tool:ffprobe",
        "answer": summary + " No words were transcribed.",
        "next": "install whisper or whisper.cpp for the transcript",
    }


def draw_svg(prompt: str) -> str:
    lower = prompt.lower()
    color = "#1c2833"
    for name, hex_color in COLORS.items():
        if re.search(rf"\b{name}\b", lower):
            color = hex_color
            break
    label_match = re.search(r"[\"']([^\"']{1,80})[\"']", prompt)
    label = label_match.group(1) if label_match else ""
    if not label:
        word = re.search(r"\blabel\s+([a-z0-9][a-z0-9 -]{0,40})", prompt, flags=re.I)
        label = word.group(1).strip() if word else ""
    if re.search(r"\b(square|rectangle|box)\b", lower):
        shape = f'<rect x="48" y="48" width="160" height="160" rx="8" fill="{color}"/>'
    elif re.search(r"\b(line|stroke)\b", lower):
        shape = f'<line x1="32" y1="200" x2="288" y2="56" stroke="{color}" stroke-width="8"/>'
    else:
        shape = f'<circle cx="160" cy="128" r="72" fill="{color}"/>'
    text = ""
    if label:
        safe = (
            label.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        text = f'<text x="160" y="250" text-anchor="middle" font-family="sans-serif" font-size="20" fill="#1c1c1c">{safe}</text>'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="280" viewBox="0 0 320 280">\n'
        '<rect width="320" height="280" fill="#fbfbfb"/>\n'
        f"{shape}\n{text}\n</svg>\n"
    )


def search_code(pattern: str, root: Path, timeout: int = 8) -> dict:
    rg = shutil.which("rg")
    if not rg:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:rg",
            "answer": "ripgrep is not installed.",
            "next": "install rg",
        }
    if not pattern.strip():
        return {
            "ok": False,
            "act": "silence-gap",
            "source": "tool:rg",
            "answer": "A code search needs a quoted pattern.",
            "next": 'rg "pattern"',
        }
    proc = subprocess.run(
        [rg, "-n", "--max-count", "20", "-g", "!*.jsonl", "-g", "!.git", pattern, str(root)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode not in {0, 1}:
        return {
            "ok": False,
            "act": "silence-instrument",
            "source": "tool:rg",
            "answer": (proc.stderr or "rg failed").strip()[:300],
            "next": "narrow the pattern",
        }
    lines = [line for line in proc.stdout.splitlines() if line.strip()][:20]
    if not lines:
        return {
            "ok": True,
            "act": "say",
            "source": "tool:rg",
            "answer": f"No matches for {pattern} under {root}.",
            "next": "",
        }
    return {
        "ok": True,
        "act": "say",
        "source": "tool:rg",
        "answer": "\n".join(lines),
        "next": "",
    }


def write_drawing(prompt: str, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    svg = draw_svg(prompt)
    dest.write_text(svg, encoding="utf-8")
    return {
        "ok": True,
        "act": "say",
        "source": "tool:draw",
        "answer": f"Wrote {dest} ({dest.stat().st_size} bytes).",
        "next": "",
        "svg": svg,
    }
