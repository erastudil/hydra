"""Media routes: image generation and audio transcription for Hydra.

Routes are data (media.json beside this module), not code:

- image, local first: the Imagine Toy's ComfyUI on 127.0.0.1:8188,
  running the workflow that won the RTX 4060 fit test (see
  HYDRA-PASS-2026-11-01.md). Generation runs inside
  gpu_arbiter.gpu_job: the front desk steps aside first and returns
  after. Fallback: the HF Inference API route in media.json.
- audio transcription: Groq Whisper (whisper-large-v3-turbo) on the
  Groq key already configured in ~/.hydra/.env.
- video: endpoint routes only, named in media.json. Nothing local is
  wired or claimed.

Keys are read from the environment / ~/.hydra/.env via config.load_dotenv
and are never printed.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_DATA: Optional[Dict[str, Any]] = None


def routes() -> Dict[str, Any]:
    global _DATA
    if _DATA is None:
        _DATA = json.loads(Path(__file__).with_name("media.json").read_text(encoding="utf-8"))
    return _DATA


def _key(name: str) -> str:
    from hydra_cli.config import load_dotenv

    load_dotenv()
    return os.environ.get(name, "").strip()


def output_dir() -> Path:
    path = Path.home() / ".hydra" / "media"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ------------------------------------------------------------- transcription

def transcribe_audio(path: str, model: Optional[str] = None) -> Dict[str, Any]:
    """Transcribe an audio file via the Groq Whisper route."""
    cfg = routes()["audio_transcription"]["groq"]
    key = _key(cfg["key_env"])
    if not key:
        return {"ok": False, "error": f"{cfg['key_env']} is not configured"}
    audio = Path(path)
    if not audio.is_file():
        return {"ok": False, "error": f"no such audio file: {path}"}
    model = model or cfg["model"]
    boundary = f"----hydra{uuid.uuid4().hex}"
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += f'Content-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body += audio.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        cfg["url"],
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            # Groq's edge 403s the bare python-urllib agent string.
            "User-Agent": "hydra-media/1.2.2",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "route": "groq", "model": model}
    return {
        "ok": True,
        "route": "groq",
        "model": model,
        "text": data.get("text", ""),
        "seconds": round(time.time() - started, 2),
    }


# ------------------------------------------------------------------- images

def _comfy_base() -> str:
    return str(routes()["image"]["local_comfyui"]["url"]).rstrip("/")


def _comfy_alive() -> bool:
    try:
        with urllib.request.urlopen(f"{_comfy_base()}/system_stats", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


# The local workflow graph is built from the winner recorded in
# media.json (image.local_comfyui.workflow) by the 2026-11-01 fit test.
# Until a winner is recorded there, build_workflow returns None and
# generate_image uses the endpoint route rather than guessing a graph.
def build_workflow(prompt: str, width: int, height: int) -> Optional[Dict[str, Any]]:
    cfg = routes()["image"]["local_comfyui"].get("workflow")
    if not isinstance(cfg, dict) or not cfg.get("kind"):
        return None
    kind = cfg["kind"]
    seed = int(time.time()) % 2_147_483_647
    if kind == "flux2-klein":
        # Node class names verified against the Imagine Toy's ComfyUI
        # 0.34.0 /object_info on 2026-11-01.
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": cfg["clip"], "type": "flux2"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
            "6": {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "7": {"class_type": "ModelSamplingFlux", "inputs": {
                "model": ["1", 0], "max_shift": cfg.get("max_shift", 1.15), "base_shift": cfg.get("base_shift", 0.5),
                "width": width, "height": height,
            }},
            "8": {"class_type": "KSampler", "inputs": {
                "model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
                "seed": seed, "steps": cfg.get("steps", 4), "cfg": cfg.get("cfg", 1.0),
                "sampler_name": cfg.get("sampler", "euler"), "scheduler": cfg.get("scheduler", "simple"), "denoise": 1.0,
            }},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": "hydra-imagine"}},
        }
    if kind == "z-image-turbo":
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": cfg["unet"], "weight_dtype": "default"}},
            "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": cfg["clip"], "type": "lumina2"}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": cfg["vae"]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
            "5": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
            "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": cfg.get("shift", 3.0)}},
            "7": {"class_type": "KSampler", "inputs": {
                "model": ["6", 0], "positive": ["4", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                "seed": seed, "steps": cfg.get("steps", 9), "cfg": cfg.get("cfg", 1.0),
                "sampler_name": cfg.get("sampler", "res_multistep"), "scheduler": cfg.get("scheduler", "simple"), "denoise": 1.0,
            }},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
            "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "hydra-imagine"}},
        }
    return None


def _comfy_generate(prompt: str, width: int, height: int, workflow: Dict[str, Any]) -> Dict[str, Any]:
    base = _comfy_base()
    payload = json.dumps({"prompt": workflow, "client_id": uuid.uuid4().hex}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/prompt", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        queued = json.loads(resp.read().decode("utf-8", "replace"))
    prompt_id = queued.get("prompt_id")
    if not prompt_id:
        return {"ok": False, "error": f"ComfyUI did not queue the prompt: {queued}"}
    started = time.time()
    while time.time() - started < 1800:
        with urllib.request.urlopen(f"{base}/history/{prompt_id}", timeout=10) as resp:
            history = json.loads(resp.read().decode("utf-8", "replace"))
        entry = history.get(prompt_id) or {}
        outputs = entry.get("outputs") or {}
        for node_out in outputs.values():
            for image in node_out.get("images") or []:
                params = urllib.parse.urlencode(
                    {"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")}
                )
                with urllib.request.urlopen(f"{base}/view?{params}", timeout=60) as resp:
                    blob = resp.read()
                out = output_dir() / f"imagine-{int(time.time())}.png"
                out.write_bytes(blob)
                return {"ok": True, "route": "local-comfyui", "path": str(out), "seconds": round(time.time() - started, 2)}
        if entry.get("status", {}).get("status_str") == "error":
            return {"ok": False, "error": f"ComfyUI execution error: {entry.get('status')}"}
        time.sleep(1.0)
    return {"ok": False, "error": "ComfyUI generation timed out (1800s)"}


def _hf_generate(prompt: str) -> Dict[str, Any]:
    cfg = routes()["image"]["endpoint_fallback"]
    key = _key(cfg["key_env"])
    if not key:
        return {"ok": False, "error": f"{cfg['key_env']} is not configured"}
    payload = json.dumps({"inputs": prompt}).encode("utf-8")
    req = urllib.request.Request(
        cfg["url"], data=payload,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            blob = resp.read()
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "route": "hf-inference", "model": cfg["model"]}
    out = output_dir() / f"imagine-{int(time.time())}.png"
    out.write_bytes(blob)
    return {"ok": True, "route": "hf-inference", "model": cfg["model"], "path": str(out), "seconds": round(time.time() - started, 2)}


def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> Dict[str, Any]:
    """Generate one image. Local ComfyUI when it answers (with the front
    desk stepped aside); otherwise the configured endpoint route."""
    workflow = build_workflow(prompt, width, height)
    if workflow is not None and _comfy_alive():
        from hydra_cli import gpu_arbiter

        with gpu_arbiter.gpu_job("image generation"):
            return _comfy_generate(prompt, width, height, workflow)
    return _hf_generate(prompt)


def describe_routes() -> str:
    data = routes()
    lines = ["media routes:"]
    img = data["image"]
    lines.append(f"  image local : {img['local_comfyui']['url']} [{img['local_comfyui']['status']}] {img['local_comfyui']['workflow']}")
    lines.append(f"  image remote: {img['endpoint_fallback']['model']} via HF Inference [{img['endpoint_fallback']['status']}]")
    tr = data["audio_transcription"]["groq"]
    lines.append(f"  transcribe  : {tr['model']} via Groq [{tr['status']}]")
    for route in data["video"]["routes"]:
        lines.append(f"  video       : {route['name']} [{route['status']}]")
    return "\n".join(lines)
