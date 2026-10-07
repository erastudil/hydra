"""
Push-to-talk dictation for the Hydra TUI: microphone capture with VAD
endpointing, WAV packaging, and a speech-to-text backend chain.

Backend order, first configured wins:
  1. HYDRA_STT_CMD    shell command; {wav} expands to a temp WAV path; stdout is the transcript
  2. faster-whisper   local model when the package is importable (HYDRA_WHISPER_MODEL, default base.en)
  3. HYDRA_STT_URL    OpenAI-compatible /audio/transcriptions endpoint (HYDRA_STT_KEY, HYDRA_STT_MODEL)
  4. Cloudflare       Workers AI Whisper with CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (HYDRA_STT_CF_MODEL)
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import struct
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from typing import Callable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000


class DictationError(Exception):
    """Capture or transcription failure with a user-facing reason."""


def microphone_status() -> Optional[str]:
    """None when capture is possible, else the reason it is not."""
    try:
        import sounddevice as sd
    except Exception:
        return "dictation needs the sounddevice package (pip install sounddevice)"
    try:
        sd.query_devices(kind="input")
    except Exception as exc:
        return f"no input device: {exc}"
    return None


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()


def _rms(chunk: bytes) -> float:
    n = len(chunk) // 2
    if n == 0:
        return 0.0
    samples = struct.unpack(f"<{n}h", chunk[: n * 2])
    return math.sqrt(sum(s * s for s in samples) / n) / 32768.0


class Recorder:
    """Captures 16 kHz mono PCM; exposes a level history for meters and fires on_autostop after speech then silence."""

    def __init__(
        self,
        on_autostop: Optional[Callable[[], None]] = None,
        silence_stop_s: float = 1.6,
        max_seconds: float = 120.0,
    ) -> None:
        self.on_autostop = on_autostop
        self.silence_stop_s = silence_stop_s
        self.max_seconds = max_seconds
        self.levels: List[float] = []
        self.heard_speech = False
        self._chunks: List[bytes] = []
        self._pending = b""
        self._stream = None
        self._lock = threading.Lock()
        self._started = 0.0
        self._fired = False
        self._vad = None
        autostop_env = os.environ.get("HYDRA_DICTATION_AUTOSTOP", "1").strip().lower()
        self._autostop = autostop_env not in ("0", "false", "no", "off")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started if self._started else 0.0

    def start(self) -> None:
        reason = microphone_status()
        if reason:
            raise DictationError(reason)
        import sounddevice as sd
        from hydra_cli.voice import SileroVADDetector

        self._vad = SileroVADDetector(
            sample_rate=SAMPLE_RATE,
            frame_size_ms=FRAME_MS,
            energy_threshold=0.012,
            hangover_onset_frames=4,
            hangover_offset_frames=max(1, int(self.silence_stop_s * 1000 / FRAME_MS)),
        )
        self._started = time.monotonic()
        try:
            self._stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise DictationError(f"microphone open failed: {exc}") from exc

    def _callback(self, indata, frames, time_info, status) -> None:
        data = bytes(indata)
        with self._lock:
            self._chunks.append(data)
            self._pending += data
            frame_bytes = FRAME_SAMPLES * 2
            ended = False
            while len(self._pending) >= frame_bytes:
                frame, self._pending = self._pending[:frame_bytes], self._pending[frame_bytes:]
                self.levels.append(_rms(frame))
                if self._vad is not None:
                    result = self._vad.process_frame(frame)
                    self.heard_speech = self.heard_speech or result.speech_started
                    ended = result.speech_ended or ended
            if len(self.levels) > 400:
                del self.levels[:-200]
        over = self.elapsed >= self.max_seconds
        if (ended and self._autostop or over) and not self._fired and self.on_autostop:
            self._fired = True
            self.on_autostop()

    def stop(self) -> bytes:
        """Stop capture; return the recording as WAV bytes."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            pcm = b"".join(self._chunks)
            self._chunks.clear()
        return pcm_to_wav(pcm)

    def cancel(self) -> None:
        self.stop()


def wav_duration(wav: bytes) -> float:
    with wave.open(io.BytesIO(wav), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate() or SAMPLE_RATE)


def _clean(value: Optional[str]) -> str:
    return (value or "").strip().strip('"').strip("'")


def _stt_command(wav: bytes) -> str:
    template = os.environ["HYDRA_STT_CMD"]
    fd, path = tempfile.mkstemp(suffix=".wav", prefix="hydra-dictation-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(wav)
        cmd = template.replace("{wav}", f'"{path}"') if "{wav}" in template else f'{template} "{path}"'
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise DictationError(f"HYDRA_STT_CMD exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        return proc.stdout.strip()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


_WHISPER_MODEL = None


def _stt_faster_whisper(wav: bytes) -> str:
    global _WHISPER_MODEL
    from faster_whisper import WhisperModel

    if _WHISPER_MODEL is None:
        _WHISPER_MODEL = WhisperModel(os.environ.get("HYDRA_WHISPER_MODEL", "base.en"), device="auto", compute_type="int8")
    segments, _info = _WHISPER_MODEL.transcribe(io.BytesIO(wav), vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()


def _stt_openai_compatible(wav: bytes) -> str:
    url = _clean(os.environ.get("HYDRA_STT_URL"))
    boundary = f"hydra-{uuid.uuid4().hex}"
    model = _clean(os.environ.get("HYDRA_STT_MODEL")) or "whisper-1"
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{model}\r\n'.encode(),
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.wav"\r\n'
        "Content-Type: audio/wav\r\n\r\n".encode(),
        wav,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    key = _clean(os.environ.get("HYDRA_STT_KEY"))
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = Request(url, data=b"".join(parts), headers=headers, method="POST")
    with urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return str(payload.get("text", "")).strip()


def _stt_cloudflare(wav: bytes) -> str:
    token = _clean(os.environ.get("CLOUDFLARE_API_TOKEN"))
    account = _clean(os.environ.get("CLOUDFLARE_ACCOUNT_ID"))
    model = _clean(os.environ.get("HYDRA_STT_CF_MODEL")) or "@cf/openai/whisper-large-v3-turbo"
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
    body = json.dumps({"audio": base64.b64encode(wav).decode("ascii")}).encode("utf-8")
    req = Request(url, data=body, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise DictationError(f"Cloudflare Whisper HTTP {exc.code}: {detail}") from exc
    if not payload.get("success", True):
        raise DictationError(f"Cloudflare Whisper error: {payload.get('errors')}")
    result = payload.get("result") or {}
    return str(result.get("text", "")).strip()


def backends() -> List[Tuple[str, Callable[[bytes], str]]]:
    """Configured speech-to-text backends in priority order."""
    chain: List[Tuple[str, Callable[[bytes], str]]] = []
    if _clean(os.environ.get("HYDRA_STT_CMD")):
        chain.append(("command", _stt_command))
    try:
        import importlib.util

        if importlib.util.find_spec("faster_whisper") is not None:
            chain.append(("faster-whisper", _stt_faster_whisper))
    except Exception:
        pass
    if _clean(os.environ.get("HYDRA_STT_URL")):
        chain.append(("openai-compatible", _stt_openai_compatible))
    if _clean(os.environ.get("CLOUDFLARE_API_TOKEN")) and _clean(os.environ.get("CLOUDFLARE_ACCOUNT_ID")):
        chain.append(("cloudflare", _stt_cloudflare))
    return chain


def transcribe(wav: bytes) -> Tuple[str, str]:
    """Return (transcript, backend name); raise DictationError when every backend fails."""
    chain = backends()
    if not chain:
        raise DictationError(
            "no speech-to-text backend: set HYDRA_STT_CMD, HYDRA_STT_URL, Cloudflare credentials, or pip install faster-whisper"
        )
    errors: List[str] = []
    for name, fn in chain:
        try:
            return fn(wav), name
        except (DictationError, HTTPError, URLError, OSError, ValueError, KeyError, ImportError) as exc:
            errors.append(f"{name}: {exc}")
    raise DictationError("; ".join(errors))
