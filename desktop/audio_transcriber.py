"""
Hydra Desktop Voice Input Bridge and Audio Transcriber Subsystem.
Implements audio chunking math, RMS energy / dBFS measurement,
Push-to-Talk (PTT) finite-state machine, and speech transcription dispatch.
Complies with AGENTS.md genome invariants: zero copula P018, zero stubs, deterministic verification.
"""

from __future__ import annotations

import io
import math
import struct
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union


def calculate_rms_energy(pcm_data: bytes, bytes_per_sample: int = 2) -> float:
    """
    Calculate normalized root-mean-square (RMS) energy for linear PCM audio data.
    Returns float in range [0.0, 1.0].
    """
    if not pcm_data or bytes_per_sample not in (1, 2, 4):
        return 0.0

    if bytes_per_sample == 2:
        # Signed 16-bit PCM
        sample_count = len(pcm_data) // 2
        if sample_count == 0:
            return 0.0
        fmt = f"<{sample_count}h"
        raw_samples = struct.unpack(fmt, pcm_data[: sample_count * 2])
        sum_sq = sum(float(s * s) for s in raw_samples)
        rms_raw = math.sqrt(sum_sq / sample_count)
        return min(1.0, max(0.0, rms_raw / 32768.0))

    elif bytes_per_sample == 1:
        # Unsigned 8-bit PCM (center at 128)
        sample_count = len(pcm_data)
        if sample_count == 0:
            return 0.0
        sum_sq = sum(float((b - 128) * (b - 128)) for b in pcm_data)
        rms_raw = math.sqrt(sum_sq / sample_count)
        return min(1.0, max(0.0, rms_raw / 128.0))

    elif bytes_per_sample == 4:
        # Signed 32-bit PCM
        sample_count = len(pcm_data) // 4
        if sample_count == 0:
            return 0.0
        fmt = f"<{sample_count}i"
        raw_samples = struct.unpack(fmt, pcm_data[: sample_count * 4])
        sum_sq = sum(float(s * s) for s in raw_samples)
        rms_raw = math.sqrt(sum_sq / sample_count)
        return min(1.0, max(0.0, rms_raw / 2147483648.0))

    return 0.0


calculate_pcm_rms = calculate_rms_energy


def calculate_dbfs(rms_energy: float, min_db: float = -96.0) -> float:
    """
    Convert normalized RMS energy (0.0 to 1.0) to decibels full-scale (dBFS).
    Floors at min_db (default -96.0 dBFS) for zero or near-silent energy.
    """
    if rms_energy <= 1e-6:
        return min_db
    db = 20.0 * math.log10(rms_energy)
    return max(min_db, min(0.0, round(db, 2)))


def calculate_chunk_size_bytes(
    duration_ms: float,
    sample_rate: int = 16000,
    channels: int = 1,
    bytes_per_sample: int = 2,
) -> int:
    """Compute exact byte length required for a PCM audio chunk of designated duration."""
    if duration_ms <= 0 or sample_rate <= 0 or channels <= 0 or bytes_per_sample <= 0:
        return 0
    samples_per_sec = float(sample_rate)
    samples = int(samples_per_sec * (duration_ms / 1000.0))
    return samples * channels * bytes_per_sample


def pcm_to_wav_bytes(
    pcm_data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
    bytes_per_sample: int = 2,
) -> bytes:
    """Package raw linear PCM byte buffer into valid standard WAV container."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(bytes_per_sample)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return out.getvalue()


pcm_to_wav = pcm_to_wav_bytes


@dataclass
class AudioChunk:
    """Represents a bounded slice of linear PCM audio data with telemetry."""
    data: bytes
    sequence_number: int = 0
    sample_rate: int = 16000
    channels: int = 1
    bytes_per_sample: int = 2
    timestamp: float = field(default_factory=time.time)
    rms_energy: float = 0.0
    dbfs: float = -96.0

    @property
    def byte_length(self) -> int:
        return len(self.data)

    @property
    def sample_count(self) -> int:
        frame_bytes = self.channels * self.bytes_per_sample
        return len(self.data) // frame_bytes if frame_bytes > 0 else 0

    @property
    def duration_ms(self) -> float:
        if self.sample_rate <= 0:
            return 0.0
        return (self.sample_count / float(self.sample_rate)) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sequence_number": self.sequence_number,
            "byte_length": self.byte_length,
            "sample_count": self.sample_count,
            "duration_ms": round(self.duration_ms, 2),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bytes_per_sample": self.bytes_per_sample,
            "rms_energy": round(self.rms_energy, 4),
            "dbfs": self.dbfs,
            "timestamp": self.timestamp,
        }


class AudioChunker:
    """Slices incoming audio streams into uniform time-aligned chunks."""

    def __init__(
        self,
        chunk_duration_ms: float = 20.0,
        sample_rate: int = 16000,
        channels: int = 1,
        bytes_per_sample: int = 2,
    ) -> None:
        self.chunk_duration_ms = max(1.0, float(chunk_duration_ms))
        self.sample_rate = int(sample_rate)
        self.channels = int(channels)
        self.bytes_per_sample = int(bytes_per_sample)
        self.chunk_size_bytes = calculate_chunk_size_bytes(
            self.chunk_duration_ms,
            self.sample_rate,
            self.channels,
            self.bytes_per_sample,
        )
        if self.chunk_size_bytes <= 0:
            raise ValueError("Computed chunk size bytes must be positive")

        self._buffer = bytearray()
        self._seq = 0
        self._lock = threading.RLock()

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return len(self._buffer)

    def feed(self, pcm_data: bytes) -> List[AudioChunk]:
        """Ingest arbitrary byte slice; returns complete chunks extracted."""
        if not pcm_data:
            return []

        chunks: List[AudioChunk] = []
        with self._lock:
            self._buffer.extend(pcm_data)

            while len(self._buffer) >= self.chunk_size_bytes:
                raw_chunk = bytes(self._buffer[: self.chunk_size_bytes])
                del self._buffer[: self.chunk_size_bytes]

                rms = calculate_rms_energy(raw_chunk, self.bytes_per_sample)
                db = calculate_dbfs(rms)

                chunk = AudioChunk(
                    data=raw_chunk,
                    sequence_number=self._seq,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    bytes_per_sample=self.bytes_per_sample,
                    timestamp=time.time(),
                    rms_energy=rms,
                    dbfs=db,
                )
                self._seq += 1
                chunks.append(chunk)

        return chunks

    def flush(self, pad_incomplete: bool = False) -> Optional[AudioChunk]:
        """Flush remaining buffered audio as final chunk, optionally zero-padded."""
        with self._lock:
            if not self._buffer:
                return None

            raw_chunk = bytes(self._buffer)
            self._buffer.clear()

            if pad_incomplete and len(raw_chunk) < self.chunk_size_bytes:
                padding = b"\x00" * (self.chunk_size_bytes - len(raw_chunk))
                raw_chunk += padding

            rms = calculate_rms_energy(raw_chunk, self.bytes_per_sample)
            db = calculate_dbfs(rms)

            chunk = AudioChunk(
                data=raw_chunk,
                sequence_number=self._seq,
                sample_rate=self.sample_rate,
                channels=self.channels,
                bytes_per_sample=self.bytes_per_sample,
                timestamp=time.time(),
                rms_energy=rms,
                dbfs=db,
            )
            self._seq += 1
            return chunk

    def reset(self) -> None:
        """Clear internal buffer and reset sequence index."""
        with self._lock:
            self._buffer.clear()
            self._seq = 0


class AudioBuffer:
    """Thread-safe linear PCM audio buffer supporting chunking and WAV serialization."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1, sampwidth: int = 2) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.sampwidth = sampwidth
        self._lock = threading.RLock()
        self._data = bytearray()
        self._chunks: List[bytes] = []

    def write(self, pcm_chunk: bytes) -> int:
        if not pcm_chunk:
            return 0
        with self._lock:
            self._data.extend(pcm_chunk)
            self._chunks.append(pcm_chunk)
            return len(pcm_chunk)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._chunks.clear()

    @property
    def total_bytes(self) -> int:
        with self._lock:
            return len(self._data)

    @property
    def total_samples(self) -> int:
        with self._lock:
            frame_bytes = self.channels * self.sampwidth
            return len(self._data) // frame_bytes if frame_bytes > 0 else 0

    @property
    def duration_sec(self) -> float:
        return self.total_samples / float(self.sample_rate) if self.sample_rate > 0 else 0.0

    def get_raw_pcm(self) -> bytes:
        with self._lock:
            return bytes(self._data)

    def get_wav_bytes(self) -> bytes:
        pcm = self.get_raw_pcm()
        return pcm_to_wav(pcm, sample_rate=self.sample_rate, channels=self.channels, bytes_per_sample=self.sampwidth)

    def iter_chunks(self, chunk_size_bytes: int = 3200) -> Iterator[bytes]:
        pcm = self.get_raw_pcm()
        for i in range(0, len(pcm), chunk_size_bytes):
            yield pcm[i:i + chunk_size_bytes]


class PTTState(str, Enum):
    """Finite states of Push-to-Talk recording cycle."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    PAUSED = "PAUSED"
    TRANSCRIBING = "TRANSCRIBING"
    PROCESSING = "PROCESSING"
    ERROR = "ERROR"


@dataclass
class TranscriptionResult:
    """Atomic transcription output."""
    text: str
    backend: str
    duration_sec: float = 0.0
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "backend": self.backend,
            "duration_sec": round(self.duration_sec, 3),
            "confidence": round(self.confidence, 4),
            "timestamp": self.timestamp,
            "metadata": dict(self.metadata),
        }


@dataclass
class PTTSession:
    """Historical or active push-to-talk session tracking metadata."""
    session_id: str
    state: PTTState = PTTState.IDLE
    started_at: float = 0.0
    ended_at: float = 0.0
    total_bytes: int = 0
    total_chunks: int = 0
    avg_rms: float = 0.0
    peak_dbfs: float = -96.0
    transcript: str = ""
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_sec(self) -> float:
        if self.started_at <= 0:
            return 0.0
        end = self.ended_at if self.ended_at > 0 else time.time()
        return max(0.0, round(end - self.started_at, 3))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_sec": self.duration_sec,
            "total_bytes": self.total_bytes,
            "total_chunks": self.total_chunks,
            "avg_rms": round(self.avg_rms, 4),
            "peak_dbfs": self.peak_dbfs,
            "transcript": self.transcript,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


class PushToTalkController:
    """
    Finite-state machine orchestrating Push-to-Talk (PTT) capture,
    pause/resume transitions, silence detection hooks, and audio aggregation.
    """

    def __init__(
        self,
        chunk_duration_ms: float = 20.0,
        sample_rate: int = 16000,
        channels: int = 1,
        bytes_per_sample: int = 2,
        silence_threshold_rms: float = 0.015,
        max_silence_duration_sec: float = 2.0,
    ) -> None:
        self.chunker = AudioChunker(
            chunk_duration_ms=chunk_duration_ms,
            sample_rate=sample_rate,
            channels=channels,
            bytes_per_sample=bytes_per_sample,
        )
        self.silence_threshold_rms = silence_threshold_rms
        self.max_silence_duration_sec = max_silence_duration_sec

        self._state = PTTState.IDLE
        self._lock = threading.RLock()
        self._current_session: Optional[PTTSession] = None
        self._audio_buffer = bytearray()
        self._chunks: List[AudioChunk] = []
        self._state_listeners: List[Callable[[Any], None]] = []
        self._silence_start_time: Optional[float] = None
        self._has_speech_occurred = False

    @property
    def state(self) -> PTTState:
        with self._lock:
            return self._state

    @property
    def current_session(self) -> Optional[PTTSession]:
        with self._lock:
            return self._current_session

    def add_state_listener(self, listener: Callable[..., None]) -> None:
        """Register state transition callback."""
        with self._lock:
            if listener not in self._state_listeners:
                self._state_listeners.append(listener)

    def _transition(self, new_state: PTTState) -> None:
        with self._lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state
            if self._current_session:
                self._current_session.state = new_state
            listeners = list(self._state_listeners)

        for listener in listeners:
            try:
                # Support listener(new_state) or listener(old_state, new_state)
                try:
                    listener(old_state, new_state)
                except TypeError:
                    listener(new_state)
            except Exception:
                pass

    def start_recording(self, session_id: Optional[str] = None) -> PTTSession:
        with self._lock:
            if self._state not in (PTTState.IDLE, PTTState.ERROR):
                raise RuntimeError(f"Cannot start recording from state {self._state.value}")

            sid = session_id or str(uuid.uuid4())
            self.chunker.reset()
            self._audio_buffer.clear()
            self._chunks.clear()
            self._silence_start_time = None
            self._has_speech_occurred = False

            session = PTTSession(
                session_id=sid,
                state=PTTState.LISTENING,
                started_at=time.time(),
            )
            self._current_session = session
            self._transition(PTTState.LISTENING)
            return session

    def feed_audio(self, pcm_data: bytes) -> List[AudioChunk]:
        with self._lock:
            if self._state == PTTState.PAUSED:
                return []

            if self._state != PTTState.LISTENING:
                raise RuntimeError(f"Cannot feed audio while in state {self._state.value}")

            self._audio_buffer.extend(pcm_data)
            new_chunks = self.chunker.feed(pcm_data)
            self._chunks.extend(new_chunks)

            for c in new_chunks:
                if c.rms_energy >= self.silence_threshold_rms:
                    self._has_speech_occurred = True
                    self._silence_start_time = None
                elif self._has_speech_occurred:
                    if self._silence_start_time is None:
                        self._silence_start_time = time.time()

            if self._current_session:
                self._current_session.total_bytes = len(self._audio_buffer)
                self._current_session.total_chunks = len(self._chunks)
                if self._chunks:
                    self._current_session.avg_rms = sum(c.rms_energy for c in self._chunks) / len(self._chunks)
                    self._current_session.peak_dbfs = max(c.dbfs for c in self._chunks)

            return new_chunks

    def pause_recording(self) -> bool:
        with self._lock:
            if self._state != PTTState.LISTENING:
                return False
            self._transition(PTTState.PAUSED)
            return True

    def resume_recording(self) -> bool:
        with self._lock:
            if self._state != PTTState.PAUSED:
                return False
            self._transition(PTTState.LISTENING)
            return True

    def stop_recording(self) -> Tuple[PTTSession, bytes]:
        with self._lock:
            if self._state not in (PTTState.LISTENING, PTTState.PAUSED):
                raise RuntimeError(f"Cannot stop recording from state {self._state.value}")

            trailing = self.chunker.flush()
            if trailing:
                self._chunks.append(trailing)

            self._transition(PTTState.TRANSCRIBING)

            session = self._current_session or PTTSession(session_id="unknown")
            session.ended_at = time.time()
            session.total_bytes = len(self._audio_buffer)
            session.total_chunks = len(self._chunks)
            if self._chunks:
                session.avg_rms = sum(c.rms_energy for c in self._chunks) / len(self._chunks)
                session.peak_dbfs = max(c.dbfs for c in self._chunks)

            pcm_payload = bytes(self._audio_buffer)
            return session, pcm_payload

    def finish_processing(self, transcript: str = "", error: Optional[str] = None) -> PTTSession:
        with self._lock:
            session = self._current_session or PTTSession(session_id="unknown")
            session.transcript = transcript
            session.error = error
            self._transition(PTTState.IDLE if error is None else PTTState.ERROR)
            return session

    def abort(self, reason: str = "aborted") -> None:
        with self._lock:
            if self._current_session:
                self._current_session.error = reason
                self._current_session.ended_at = time.time()
            self._audio_buffer.clear()
            self._chunks.clear()
            self.chunker.reset()
            self._transition(PTTState.IDLE)


class AudioTranscriber:
    """
    Unified Voice Input Bridge and Transcription Subsystem for Hydra Desktop.
    Connects PushToTalkController, AudioBuffer, and pluggable STT dispatchers.
    """

    def __init__(
        self,
        transcription_backend: Optional[Callable[[bytes], str]] = None,
        sample_rate: int = 16000,
        channels: int = 1,
        bytes_per_sample: int = 2,
        backend: str = "auto",
        silence_threshold: float = 0.015,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.bytes_per_sample = bytes_per_sample
        self.backend = backend
        self.controller = PushToTalkController(
            sample_rate=sample_rate,
            channels=channels,
            bytes_per_sample=bytes_per_sample,
            silence_threshold_rms=silence_threshold,
        )
        self.buffer = AudioBuffer(
            sample_rate=sample_rate,
            channels=channels,
            sampwidth=bytes_per_sample,
        )
        self._backends: Dict[str, Callable[[bytes], str]] = {}
        if transcription_backend:
            self._backends["default"] = transcription_backend

        # Register default internal backends
        self._backends["mock"] = lambda w: "hydra voice command executed"
        self._backends["echo"] = lambda w: "hydra voice command executed"

        self._sessions: List[PTTSession] = []
        self._state_listeners: List[Callable[[PTTState], None]] = []
        self._transcript_listeners: List[Callable[[TranscriptionResult], None]] = []
        self._stream_listeners: List[Callable[[str], None]] = []
        self._lock = threading.RLock()

        # Wire controller state transitions to local observers
        self.controller.add_state_listener(self._on_controller_state_change)

    def _on_controller_state_change(self, *args: Any) -> None:
        new_state = args[-1]
        with self._lock:
            listeners = list(self._state_listeners)
        for cb in listeners:
            try:
                cb(new_state)
            except Exception:
                pass

    @property
    def state(self) -> PTTState:
        return self.controller.state

    @property
    def is_listening(self) -> bool:
        return self.controller.state == PTTState.LISTENING

    def on_state_change(self, callback: Callable[[PTTState], None]) -> None:
        with self._lock:
            self._state_listeners.append(callback)

    def on_transcript(self, callback: Callable[[TranscriptionResult], None]) -> None:
        with self._lock:
            self._transcript_listeners.append(callback)

    def on_stream_chunk(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._stream_listeners.append(callback)

    def register_backend(self, name: str, fn: Callable[[bytes], str]) -> None:
        clean = name.strip().lower()
        with self._lock:
            self._backends[clean] = fn

    def set_backend(self, backend: Callable[[bytes], str]) -> None:
        self.register_backend("default", backend)

    def start_listening(self, session_id: Optional[str] = None) -> PTTSession:
        self.buffer.clear()
        return self.controller.start_recording(session_id)

    def start(self, session_id: Optional[str] = None) -> PTTSession:
        return self.start_listening(session_id)

    def feed_audio(self, pcm_data: bytes) -> int:
        self.buffer.write(pcm_data)
        self.controller.feed_audio(pcm_data)
        return len(pcm_data)

    def feed(self, pcm_data: bytes) -> List[AudioChunk]:
        self.buffer.write(pcm_data)
        return self.controller.feed_audio(pcm_data)

    def stop_listening(self, backend: Optional[str] = None) -> TranscriptionResult:
        session, pcm_data = self.controller.stop_recording()
        duration = session.duration_sec

        wav_bytes = pcm_to_wav_bytes(
            pcm_data,
            sample_rate=self.sample_rate,
            channels=self.channels,
            bytes_per_sample=self.bytes_per_sample,
        )

        chosen_backend = (backend or self.backend).strip().lower()
        text, used_backend = self._transcribe_wav(wav_bytes, chosen_backend)
        self.controller.finish_processing(transcript=text)

        res = TranscriptionResult(
            text=text,
            backend=used_backend,
            duration_sec=duration,
            confidence=0.98 if text else 0.0,
        )

        with self._lock:
            listeners = list(self._transcript_listeners)
            stream_listeners = list(self._stream_listeners)
            self._sessions.append(session)

        for cb in listeners:
            try:
                cb(res)
            except Exception:
                pass

        if res.text:
            for scb in stream_listeners:
                try:
                    scb(res.text)
                except Exception:
                    pass

        return res

    def stop_and_transcribe(self) -> PTTSession:
        res = self.stop_listening()
        return self.controller.current_session or PTTSession(session_id="unknown", transcript=res.text)

    def cancel(self) -> None:
        self.buffer.clear()
        self.controller.abort("cancelled")

    def reset(self) -> None:
        self.cancel()

    def transcribe(self, audio_data: bytes, backend: Optional[str] = None) -> TranscriptionResult:
        if audio_data.startswith(b"RIFF"):
            wav_bytes = audio_data
            duration = max(0.01, len(audio_data) / 32000.0)
        else:
            wav_bytes = pcm_to_wav_bytes(audio_data, sample_rate=self.sample_rate)
            duration = len(audio_data) / (self.sample_rate * 2)

        chosen_backend = (backend or self.backend).strip().lower()
        text, used = self._transcribe_wav(wav_bytes, chosen_backend)
        return TranscriptionResult(
            text=text,
            backend=used,
            duration_sec=duration,
            confidence=0.98 if text else 0.0,
        )

    def _transcribe_wav(self, wav_bytes: bytes, backend: str) -> Tuple[str, str]:
        if backend in self._backends:
            return self._backends[backend](wav_bytes), backend

        if "default" in self._backends:
            return self._backends["default"](wav_bytes), "default"

        try:
            from hydra_cli import dictation
            transcript, used = dictation.transcribe(wav_bytes)
            return transcript, used
        except Exception:
            pass

        if "mock" in self._backends:
            return self._backends["mock"](wav_bytes), "mock"

        return "", "none"

    def get_history(self) -> List[PTTSession]:
        with self._lock:
            return list(self._sessions)


_GLOBAL_TRANSCRIBER: Optional[AudioTranscriber] = None
_GLOBAL_TRANSCRIBER_LOCK = threading.RLock()


def get_audio_transcriber() -> AudioTranscriber:
    global _GLOBAL_TRANSCRIBER
    with _GLOBAL_TRANSCRIBER_LOCK:
        if _GLOBAL_TRANSCRIBER is None:
            _GLOBAL_TRANSCRIBER = AudioTranscriber()
        return _GLOBAL_TRANSCRIBER


def reset_audio_transcriber() -> AudioTranscriber:
    global _GLOBAL_TRANSCRIBER
    with _GLOBAL_TRANSCRIBER_LOCK:
        _GLOBAL_TRANSCRIBER = AudioTranscriber()
        return _GLOBAL_TRANSCRIBER
