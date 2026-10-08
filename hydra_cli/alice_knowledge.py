"""Shelves Alice reads before she guesses.

These cards are procedures and names, not news. A hit is cited.
A missing instrument is named. It is not filled in.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

STOP = {
    "a", "an", "the", "of", "and", "or", "to", "in", "on", "for", "is", "it",
    "what", "whats", "does", "how", "why", "with", "from", "this", "that",
    "are", "be", "about", "which", "do", "i", "me", "my", "you", "can", "please",
}

# Each card is a stored sentence plus the tokens that may select it.
CARDS: List[Dict[str, str]] = [
    {
        "card_id": "hydra:swarm",
        "kind": "hydra",
        "topic": "hydra swarm",
        "keys": "swarm",
        "cues": "heads architect coder auditor synthesizer orchestrate fanout",
        "comment": (
            "hydra swarm runs specialist heads, then a synthesizer. "
            "Architect plans, coder implements, auditor reviews after the others, synthesizer writes the joint result. "
            "Default heads are architect, coder, and auditor. The auditor waits so it can read the others. "
            "A swarm spends frontier credentials. Alice writes the command and does not launch it on her own."
        ),
    },
    {
        "card_id": "hydra:agent",
        "kind": "hydra",
        "topic": "hydra agent",
        "keys": "agent react",
        "cues": "tool loop mcp turns",
        "comment": (
            "hydra agent is a tool loop of at most 15 turns. It needs frontier credentials. "
            "hydra free cannot call tools. Local hands in that loop are calc, units, datetime, stacks, and memory. "
            "Community MCP tools load only after hydra mcp init. History keeps the last 5 turns in full."
        ),
    },
    {
        "card_id": "hydra:serve",
        "kind": "hydra",
        "topic": "hydra serve",
        "keys": "serve gateway",
        "cues": "openai endpoint hermes pi",
        "comment": (
            "hydra serve opens an OpenAI-compatible gateway on 127.0.0.1:7777. "
            "Hermes and Pi can point OPENAI_BASE_URL there. Aliases resolve inside Hydra. Tools pass through."
        ),
    },
    {
        "card_id": "hydra:free",
        "kind": "hydra",
        "topic": "hydra free",
        "keys": "free forge",
        "cues": "cloudflare openrouter repair",
        "comment": (
            "hydra free is one completion on Cloudflare, then an OpenRouter free model. "
            "It has no tools and no session. It is the repair pool. The CheaperInference glm alias stays closed while that ledger is at or above $5."
        ),
    },
    {
        "card_id": "hydra:mcp",
        "kind": "hydra",
        "topic": "hydra mcp",
        "keys": "mcp",
        "cues": "filesystem fetch sqlite git github brave memory playwright",
        "comment": (
            "hydra mcp init installs filesystem, fetch, sqlite, git, GitHub, Brave search, memory, and Playwright. "
            "Sqlite opens ~/.hydra/workspace.db, not emap.db. Brave and fetch are not the stack whitelist. "
            "Provider keys are not passed into MCP server processes."
        ),
    },
    {
        "card_id": "hydra:hands",
        "kind": "hydra",
        "topic": "hydra hands",
        "keys": "hands",
        "cues": "local calc units stacks memory",
        "comment": (
            "hydra hands answers from calc, units, the clock, stack cards, and memory, with no model call. "
            "--personality selects coder, researcher, chat, or writer. The voice does not change the sentence."
        ),
    },
    {
        "card_id": "house:easylm",
        "kind": "house",
        "topic": "easylm",
        "keys": "easylm",
        "cues": "webgpu browser vercel qwen stacks studio atmem",
        "comment": (
            "EasyLM is the browser app at easylm.app. Inference is WebGPU on the device. "
            "The default weight is Qwen 2.5 3B Instruct. Stacks, math, and the clock run before a weight download when the prompt matches them. "
            "Studio reads, writes, edits code, graphs, and draws. AtMem keeps typed notes inside about 256 tokens. Kid-safe mode lives here, not in Hydra."
        ),
    },
    {
        "card_id": "house:alice",
        "kind": "house",
        "topic": "alice emap",
        "keys": "alice emap",
        "cues": "lattice receipt card abstain",
        "comment": (
            "Alice answers from a card or a tool. The map is emap.db: lemmas, senses, and edges. "
            "A stack card beats a whitelist fetch. A model sample is not a ground. "
            "The Hugging Face Alice adapter is a PEFT LoRA, the same bytes as the Hands 3B adapter, and WebLLM cannot load it as a full model."
        ),
    },
    {
        "card_id": "house:stacks",
        "kind": "house",
        "topic": "the stacks",
        "keys": "stacks",
        "cues": "facts dewey textbook door progen",
        "comment": (
            "The stacks are 32 Dewey shelves of undergraduate notes. FACTS.md lines compile to pinned cards. "
            "A card has a topic, a comment, a Dewey code, and a door. Wikipedia orients. NIST, BIPM, an RFC, or a statute wins on the same topic."
        ),
    },
    {
        "card_id": "house:github",
        "kind": "house",
        "topic": "house repos",
        "keys": "github erastudil",
        "cues": "vercel progen zcabs gfc hydra easylm",
        "comment": (
            "The house git account is erastudil. alice-emap holds the map and this session. "
            "hydra is the shell. easylm is the web app. progen is the fact dialect. zcabs fails closed on retrieval. gfc is the writing standard. "
            "EasyLM deploys on Vercel."
        ),
    },
    {
        "card_id": "craft:pytest",
        "kind": "craft",
        "topic": "pytest",
        "keys": "pytest",
        "cues": "python test tests",
        "comment": (
            "Python tests in this house use pytest. From alice-emap: python -m pytest -q tests. "
            "Hydra verification is one command: python scripts/verify.py. The TUI needs prompt_toolkit."
        ),
    },
    {
        "card_id": "craft:vitest",
        "kind": "craft",
        "topic": "vitest",
        "keys": "vitest npm typescript",
        "cues": "easylm javascript test",
        "comment": (
            "EasyLM tests use vitest. The gate is npm test, which compiles stacks and courses first. Typecheck is npx tsc --noEmit. Node 18 or newer."
        ),
    },
    {
        "card_id": "craft:git",
        "kind": "craft",
        "topic": "git",
        "keys": "git commit branch",
        "cues": "diff push merge",
        "comment": (
            "Commit small, with a message that names the change. Do not force-push. "
            "Do not commit secrets. A feature lands on its own branch and opens against main."
        ),
    },
    {
        "card_id": "craft:webgpu",
        "kind": "craft",
        "topic": "webgpu",
        "keys": "webgpu mlc",
        "cues": "browser weight qwen wasm",
        "comment": (
            "EasyLM runs WebLLM. A model repo must contain mlc-chat-config and parameter shards, plus a wasm lib. "
            "A PEFT adapter_model.safetensors file is not that layout. Chrome or Edge 113, or Safari 18, is the browser bar."
        ),
    },
    {
        "card_id": "oss:playwright",
        "kind": "oss",
        "topic": "playwright",
        "keys": "playwright browser",
        "cues": "chromium headless screenshot dom",
        "comment": (
            "Playwright drives a browser: open a public https URL, read the DOM, take a screenshot. "
            "Alice prefers the Playwright Python driver on the system Chrome channel. "
            "If that package is absent she uses google-chrome --headless. She does not open private hosts."
        ),
    },
    {
        "card_id": "oss:ffmpeg",
        "kind": "oss",
        "topic": "ffmpeg",
        "keys": "ffmpeg ffprobe audio video",
        "cues": "wav mp3 duration codec transcode",
        "comment": (
            "ffmpeg and ffprobe read media containers: codec, sample rate, channels, duration. "
            "They do not transcribe speech. Transcription is a separate model."
        ),
    },
    {
        "card_id": "oss:whisper",
        "kind": "oss",
        "topic": "whisper",
        "keys": "whisper transcribe transcribes asr",
        "cues": "speech listen hear audio voice",
        "comment": (
            "OpenAI Whisper, or whisper.cpp, turns speech audio into text. "
            "It is not installed in this environment until the whisper command exists. "
            "Until then Alice reports the container with ffprobe and names the missing ear."
        ),
    },
    {
        "card_id": "oss:tesseract",
        "kind": "oss",
        "topic": "tesseract",
        "keys": "tesseract ocr",
        "cues": "read text image scan",
        "comment": (
            "Tesseract reads printed text in a picture. It does not name objects. "
            "Object labels need a vision model. Neither is assumed installed."
        ),
    },
    {
        "card_id": "oss:sqlite",
        "kind": "oss",
        "topic": "sqlite",
        "keys": "sqlite",
        "cues": "emap database sql",
        "comment": (
            "emap.db, the memory atoms, and the Hydra workspace db are SQLite. "
            "Alice reads them with the stdlib sqlite3 module. She does not invent rows."
        ),
    },
    {
        "card_id": "oss:rg",
        "kind": "oss",
        "topic": "ripgrep",
        "keys": "ripgrep rg",
        "cues": "search code grep",
        "comment": "ripgrep (rg) searches file contents in a working tree. It is the code search hand when a path is in the prompt.",
    },
    {
        "card_id": "frontier:map",
        "kind": "frontier",
        "topic": "frontier functions",
        "keys": "frontier labs chatgpt claude gemini",
        "cues": "tools browse vision speech agents image",
        "comment": (
            "A frontier chat product is a bundle of functions. The local map is: "
            "facts to stack cards, arithmetic to the calc hand, tools to Hydra hands, "
            "multi-agent to hydra swarm, browse to Playwright, memory to atoms, "
            "files to ripgrep and sqlite, pictures to the see hand, audio to the hear hand, drawings to the draw hand. "
            "A function with no instrument stays a named gap."
        ),
    },
    {
        "card_id": "frontier:code",
        "kind": "frontier",
        "topic": "coding agent",
        "keys": "refactor implement patch debug",
        "cues": "code coder pytest repository",
        "comment": (
            "A coding task goes to the coder voice and then hydra agent, which can read the tree through MCP filesystem and git. "
            "Tests decide. Alice does not claim a patch works without a test command and its exit code."
        ),
    },
    {
        "card_id": "sense:see",
        "kind": "sense",
        "topic": "see a picture",
        "keys": "image png jpg jpeg picture photo screenshot",
        "cues": "vision look describe",
        "comment": (
            "The see hand reads an image header: format, width, height. "
            "Printed text needs Tesseract. Object names need a vision model. "
            "Alice reports the header she read and names whichever of those instruments is missing."
        ),
    },
    {
        "card_id": "sense:hear",
        "kind": "sense",
        "topic": "hear a sound",
        "keys": "wav mp3 ogg flac audio listen",
        "cues": "transcribe voice microphone",
        "comment": (
            "The hear hand uses ffprobe for codec, rate, channels, and duration. "
            "A transcript waits on Whisper. She does not invent words that were spoken."
        ),
    },
    {
        "card_id": "sense:draw",
        "kind": "sense",
        "topic": "draw",
        "keys": "draw sketch svg illustrate",
        "cues": "circle square picture paint",
        "comment": (
            "The draw hand writes an SVG she can open again: named shapes and a short label. "
            "That file is the picture. A diffusion sample would be a different act and is not this hand."
        ),
    },
    {
        "card_id": "std:http",
        "kind": "standard",
        "topic": "http",
        "keys": "http https rfc9110",
        "cues": "request response status header method",
        "comment": (
            "HTTP semantics are RFC 9110. A request has a method, a target, and headers. "
            "2xx means the server completed it, 4xx is the client, 5xx is the server. "
            "Alice fetches public https only. A private host is refused."
        ),
    },
    {
        "card_id": "std:json",
        "kind": "standard",
        "topic": "json",
        "keys": "json rfc8259",
        "cues": "schema object array structured",
        "comment": (
            "JSON is RFC 8259. Tools, receipts, and MCP messages are JSON objects. "
            "A receipt has act, route, source, and answer. Alice does not add a field the tool did not return."
        ),
    },
    {
        "card_id": "std:semver",
        "kind": "standard",
        "topic": "semantic versioning",
        "keys": "semver",
        "cues": "major minor patch version",
        "comment": (
            "Semantic Versioning is MAJOR.MINOR.PATCH. A breaking change bumps major. "
            "Hydra is 1.2.1. The Alice spec is 1.1.0."
        ),
    },
    {
        "card_id": "oss:node",
        "kind": "oss",
        "topic": "node",
        "keys": "node npm",
        "cues": "javascript package easylm",
        "comment": (
            "EasyLM is a Node app. npm test compiles the shelves and then runs vitest. Node 18 or newer. The package manager is npm."
        ),
    },
    {
        "card_id": "oss:typescript",
        "kind": "oss",
        "topic": "typescript",
        "keys": "typescript tsx tsc",
        "cues": "types interface easylm",
        "comment": (
            "EasyLM is TypeScript. npx tsc --noEmit typechecks. "
            "The web route that cites a card before WebGPU is src/engine/alice_drive.ts."
        ),
    },
    {
        "card_id": "oss:vite",
        "kind": "oss",
        "topic": "vite",
        "keys": "vite",
        "cues": "dev bundle easylm browser",
        "comment": (
            "Vite serves the EasyLM dev build and bundles the page. WebGPU weights download at runtime. They are not inside the bundle."
        ),
    },
    {
        "card_id": "oss:docker",
        "kind": "oss",
        "topic": "docker",
        "keys": "docker container",
        "cues": "image mcp github",
        "comment": (
            "Hydra's GitHub MCP server runs in Docker. Hydra itself is one Python process and takes no third-party packages."
        ),
    },
    {
        "card_id": "oss:espeak",
        "kind": "oss",
        "topic": "espeak",
        "keys": "espeak tts",
        "cues": "speak aloud synthesis voice",
        "comment": (
            "espeak is the local speech synthesizer. It is not assumed installed. "
            "Until that binary exists, speaking a sentence aloud is a named gap. Hearing a file is the hear hand, not this one."
        ),
    },
    {
        "card_id": "house:vercel",
        "kind": "house",
        "topic": "vercel",
        "keys": "vercel",
        "cues": "deploy preview easylm.app production",
        "comment": (
            "EasyLM deploys on Vercel. Production is easylm.app. A branch gets a preview deployment. "
            "The page is static. Inference runs in the browser on WebGPU."
        ),
    },
    {
        "card_id": "house:progen",
        "kind": "house",
        "topic": "progen",
        "keys": "progen",
        "cues": "facts dialect door compile",
        "comment": (
            "progen is the fact dialect. A line is 'topic : comment' and may carry a door URL after //. "
            "The compiler turns those lines into stack cards."
        ),
    },
    {
        "card_id": "house:zcabs",
        "kind": "house",
        "topic": "zcabs",
        "keys": "zcabs",
        "cues": "retrieval closed miss",
        "comment": (
            "zcabs fails closed on retrieval. A miss stays a miss. A model sample does not fill it."
        ),
    },
    {
        "card_id": "house:gfc",
        "kind": "house",
        "topic": "gfc",
        "keys": "gfc",
        "cues": "writing standard prose",
        "comment": (
            "gfc is the house writing standard. It governs prose. It does not supply facts."
        ),
    },
    {
        "card_id": "frontier:retrieve",
        "kind": "frontier",
        "topic": "retrieval",
        "keys": "retrieval rag grounding",
        "cues": "search citation passage",
        "comment": (
            "Frontier retrieval quotes a fetched passage. Alice's version is a stack card or a whitelist hand. "
            "The card is the sentence. A search snippet is not promoted to a ground."
        ),
    },
    {
        "card_id": "frontier:browse",
        "kind": "frontier",
        "topic": "computer use",
        "keys": "browse playwright",
        "cues": "computer page chrome screenshot",
        "comment": (
            "Computer use here opens a public https page with Playwright, or with system Chrome if Playwright is absent. "
            "She reads the title and the visible text. Private hosts are refused. She does not launch a paid swarm to do it."
        ),
    },
    {
        "card_id": "frontier:vision",
        "kind": "frontier",
        "topic": "vision",
        "keys": "vision objects",
        "cues": "recognize caption image",
        "comment": (
            "Frontier vision names objects in a picture. Alice's see hand reads format, width, and height, then Tesseract if it is installed. "
            "Object names stay a named gap until a vision model is present. She does not invent labels."
        ),
    },
    {
        "card_id": "frontier:speech",
        "kind": "frontier",
        "topic": "speech",
        "keys": "speech",
        "cues": "voice transcript speak hear",
        "comment": (
            "Frontier speech can hear a voice and speak back. Alice hears a file with ffprobe, and transcribes only when the whisper command exists. "
            "She does not invent words. Speaking aloud waits on espeak."
        ),
    },
    {
        "card_id": "frontier:structured",
        "kind": "frontier",
        "topic": "structured output",
        "keys": "schema structured",
        "cues": "json receipt fields",
        "comment": (
            "A frontier tool call returns a schema. Alice's receipt is the schema: act, route, source, answer, and the shelves she tried. "
            "Missing evidence is a silence act, not an empty success."
        ),
    },
]


def _tokens(text: str) -> List[str]:
    return [tok for tok in re.findall(r"[a-z0-9]+", text.lower()) if len(tok) > 1 and tok not in STOP]


def search_knowledge(query: str) -> Optional[dict]:
    terms = set(_tokens(query))
    if not terms:
        return None
    best = None
    best_score = 0
    for card in CARDS:
        keys = set(card["keys"].split())
        cues = set(card["cues"].split())
        key_hits = len(terms.intersection(keys))
        if key_hits <= 0:
            continue
        score = key_hits * 10 + len(terms.intersection(cues))
        if score > best_score:
            best = card
            best_score = score
    return best
