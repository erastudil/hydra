"""Read a prompt in layers before picking a shelf.

act, frame, slots, tool, command, then the shelves still worth trying.
The reading is data. It does not answer the question by itself.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urlparse

IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".flac", ".m4a")

URL = re.compile(r"https?://[^\s<>\"]+")
PATH = re.compile(
    r"(?:(?:\./|\.\./|/(?!/))[^\s\"']+\.(?:png|jpe?g|gif|webp|wav|mp3|ogg|flac|m4a)|(?:[\w.-]+/)+[\w.-]+\.(?:png|jpe?g|gif|webp|wav|mp3|ogg|flac|m4a))",
    re.I,
)


@dataclass
class Reading:
    act: str
    frame: str
    tool: str
    subject: str
    command: str
    shelves: List[str] = field(default_factory=list)
    layers: List[str] = field(default_factory=list)
    authority: str = "card"

    def as_dict(self) -> dict:
        return {
            "act": self.act,
            "frame": self.frame,
            "tool": self.tool,
            "subject": self.subject,
            "command": self.command,
            "authority": self.authority,
            "shelves": list(self.shelves),
            "layers": list(self.layers),
        }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _task_after(text: str, pattern: str) -> str:
    cleaned = _clean(text)
    cleaned = re.sub(pattern, "", cleaned, count=1, flags=re.I).strip(" :.-")
    return cleaned or _clean(text)


def swarm_command(task: str) -> str:
    body = task.strip() or "Review the task and name the gaps."
    return "hydra swarm " + shlex.quote(body) + " --heads architect,coder,auditor"


def agent_command(task: str, personality: str = "coder") -> str:
    body = task.strip() or "Inspect the tree and report what is true."
    voice = personality if personality in {"coder", "researcher", "chat", "writer"} else "coder"
    return "hydra agent --personality " + voice + " " + shlex.quote(body)


def interpret(text: str, personality: str = "chat") -> Reading:
    raw = _clean(text)
    lower = raw.lower()
    url_match = URL.search(raw)
    url = url_match.group(0).rstrip(".,)") if url_match else ""
    path_match = PATH.search(raw)
    path = path_match.group(0) if path_match else ""

    def done(
        act: str,
        frame: str,
        tool: str,
        subject: str,
        command: str,
        shelves: List[str],
        authority: str,
    ) -> Reading:
        layers = [
            f"speech: {act}",
            f"frame: {frame}",
            f"slots: {subject or '-'}",
            f"instrument: {tool or '-'}",
            f"authority: {authority}",
            "shelves: " + ", ".join(shelves),
        ]
        if command:
            layers.append(f"command: {command}")
        if url:
            layers.append(f"url: {url}")
        if path:
            layers.append(f"path: {path}")
        layers.append(f"voice: {personality}")
        return Reading(act, frame, tool, subject, command, shelves, layers, authority)

    if re.search(r"\b(draw|sketch|illustrate)\b", lower) or lower.startswith("svg"):
        return done("make", "draw", "draw", raw, "", ["sense:draw"], "instrument")

    if url and re.search(r"\b(browse|open|playwright|visit|screenshot|read the page|look at)\b", lower):
        command = f"playwright open {url}"
        return done("do", "browse", "browse", url, command, ["oss:playwright", "frontier:browse"], "instrument")

    image_words = re.search(r"\b(screenshot|png|jpg|jpeg|picture|photo|image)\b", lower)
    look_words = re.search(r"\b(look|see|describe|what is in|what's in)\b", lower)
    if path.lower().endswith(IMAGE_EXT) or (image_words and look_words):
        return done("perceive", "see", "see", path or raw, "", ["sense:see", "oss:tesseract", "frontier:vision"], "instrument")

    wants_transcript = re.search(r"\btranscribe\b", lower) and not re.search(
        r"\b(library|tool|install|which)\b", lower
    )
    if path.lower().endswith(AUDIO_EXT) or wants_transcript:
        return done(
            "perceive",
            "hear",
            "hear",
            path or raw,
            "",
            ["sense:hear", "oss:ffmpeg", "oss:whisper", "frontier:speech"],
            "instrument",
        )

    quoted = re.search(r"[\"']([^\"']{1,120})[\"']", raw)
    if quoted and re.search(r"\brg\b|\bripgrep\b|\bsearch the (code|repo|tree)\b", lower):
        needle = quoted.group(1)
        command = "rg -n " + shlex.quote(needle)
        return done("do", "search", "rg", needle, command, ["oss:rg"], "instrument")

    if re.search(r"\bswarm\b|\bsummon (the )?heads\b|\bmulti-agent\b", lower):
        task = _task_after(raw, r"^.*?\b(?:swarm|heads)\b(?:\s+to|\s+for)?")
        command = swarm_command(task)
        return done("orchestrate", "swarm", "hydra.swarm", task, command, ["hydra:swarm"], "cited-command")

    if re.search(r"\bhydra agent\b|\breact loop\b|\btool loop\b", lower) or re.search(
        r"\b(use|run|start) the agent\b", lower
    ):
        task = _task_after(raw, r"^.*?\bagent\b(?:\s+to|\s+for)?")
        command = agent_command(task, personality)
        return done("orchestrate", "agent", "hydra.agent", task, command, ["hydra:agent"], "cited-command")

    if re.search(r"\bhydra serve\b|\bopenai gateway\b|\bport 7777\b", lower):
        return done(
            "orchestrate",
            "serve",
            "hydra.serve",
            "127.0.0.1:7777",
            "hydra serve --port 7777",
            ["hydra:serve"],
            "cited-command",
        )

    if re.search(r"\bhydra mcp\b|\bmcp init\b", lower):
        return done("orchestrate", "mcp", "hydra.mcp", "mcp", "hydra mcp init", ["hydra:mcp"], "cited-command")

    shelves = ["compute", "lexicon", "stacks", "features", "knowledge", "hand"]
    frame = "ask"
    authority = "card"
    if re.search(r"\b(code|refactor|implement|pytest|function|bug|patch)\b", lower):
        frame = "code"
        shelves = ["compute", "knowledge", "stacks", "hydra:agent"]
        authority = "card"
    elif re.search(r"\b(how do i|how to|what (?:tool|library)|which library)\b", lower):
        frame = "procedure"
        shelves = ["features", "stacks", "knowledge"]
        authority = "card"
    return done("ask", frame, "", raw, "", shelves, authority)


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
