"""
Energy voice-activity detection and a PCM frame buffer for dictation.

The detector scores RMS energy and zero-crossing rate. It does not load a neural model.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional, Tuple, Union


class VADState(str, Enum):
    SILENCE = "SILENCE"
    SPEECH_ONSET = "SPEECH_ONSET"
    SPEECH = "SPEECH"
    SPEECH_OFFSET = "SPEECH_OFFSET"


@dataclass
class VADFrameResult:
    is_speech: bool
    probability: float
    state: VADState
    energy: float
    speech_started: bool = False
    speech_ended: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_speech": self.is_speech,
            "probability": round(self.probability, 4),
            "state": self.state.value,
            "energy": round(self.energy, 5),
            "speech_started": self.speech_started,
            "speech_ended": self.speech_ended,
        }


class EnergyVAD:
    """RMS and zero-crossing voice-activity state machine."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size_ms: int = 20,
        energy_threshold: float = 0.015,
        speech_probability_threshold: float = 0.50,
        hangover_onset_frames: int = 2,
        hangover_offset_frames: int = 10,
    ) -> None:
        if frame_size_ms not in (10, 20, 30):
            raise ValueError(f"frame_size_ms must be 10, 20, or 30 ms, got {frame_size_ms}")
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError(f"sample_rate must be 8000, 16000, 32000, or 48000 Hz, got {sample_rate}")

        self.sample_rate = sample_rate
        self.frame_size_ms = frame_size_ms
        self.frame_samples = int(sample_rate * frame_size_ms / 1000)
        self.frame_bytes = self.frame_samples * 2  # 16-bit PCM (2 bytes per sample)
        self.energy_threshold = energy_threshold
        self.speech_prob_threshold = speech_probability_threshold
        self.hangover_onset_frames = max(1, hangover_onset_frames)
        self.hangover_offset_frames = max(1, hangover_offset_frames)

        self._state = VADState.SILENCE
        self._consecutive_speech = 0
        self._consecutive_silence = 0

    @property
    def state(self) -> VADState:
        return self._state

    def reset(self) -> None:
        self._state = VADState.SILENCE
        self._consecutive_speech = 0
        self._consecutive_silence = 0

    def calculate_energy_and_probability(
        self,
        samples: list[float],
    ) -> Tuple[float, float]:
        """Compute RMS energy and calibrated speech probability for normalized audio samples."""
        if not samples:
            return 0.0, 0.0

        n = len(samples)
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / n)

        # Zero-crossing rate: measures high frequency vs voiced formant energy
        zero_crossings = 0
        for i in range(1, n):
            if (samples[i] >= 0 and samples[i - 1] < 0) or (samples[i] < 0 and samples[i - 1] >= 0):
                zero_crossings += 1
        zcr = zero_crossings / (n - 1) if n > 1 else 0.0

        # Calibrated Sigmoid scoring
        # Speech typically features energy > threshold and balanced ZCR (0.04 to 0.45)
        energy_diff = rms - self.energy_threshold
        zcr_factor = 1.0 if 0.03 <= zcr <= 0.50 else -0.5
        logit = (energy_diff * 60.0) + (zcr_factor * 0.5)

        # Logistic sigmoid clamped to avoid overflow
        logit_clamped = max(-20.0, min(20.0, logit))
        prob = 1.0 / (1.0 + math.exp(-logit_clamped))

        return rms, prob

    def process_frame(
        self,
        pcm_chunk: Union[bytes, list[float]],
    ) -> VADFrameResult:
        """Process one audio frame and advance the VAD state machine."""
        if isinstance(pcm_chunk, bytes):
            sample_count = len(pcm_chunk) // 2
            if sample_count == 0:
                return VADFrameResult(False, 0.0, self._state, 0.0)
            fmt = f"<{sample_count}h"
            raw_samples = struct.unpack(fmt, pcm_chunk[: sample_count * 2])
            samples = [s / 32768.0 for s in raw_samples]
        else:
            samples = pcm_chunk

        rms, prob = self.calculate_energy_and_probability(samples)
        is_candidate_speech = prob >= self.speech_prob_threshold

        speech_started = False
        speech_ended = False

        if is_candidate_speech:
            self._consecutive_speech += 1
            self._consecutive_silence = 0

            if self._state == VADState.SILENCE:
                if self._consecutive_speech >= self.hangover_onset_frames:
                    self._state = VADState.SPEECH
                    speech_started = True
                else:
                    self._state = VADState.SPEECH_ONSET
            elif self._state == VADState.SPEECH_ONSET:
                if self._consecutive_speech >= self.hangover_onset_frames:
                    self._state = VADState.SPEECH
                    speech_started = True
            elif self._state == VADState.SPEECH_OFFSET:
                # Regained speech before offset expired
                self._state = VADState.SPEECH
        else:
            self._consecutive_silence += 1
            self._consecutive_speech = 0

            if self._state == VADState.SPEECH:
                self._state = VADState.SPEECH_OFFSET

            if self._state in (VADState.SPEECH_OFFSET, VADState.SPEECH_ONSET):
                if self._consecutive_silence >= self.hangover_offset_frames:
                    if self._state == VADState.SPEECH_OFFSET:
                        speech_ended = True
                    self._state = VADState.SILENCE
            elif self._state == VADState.SILENCE:
                pass

        is_active_speech = self._state in (VADState.SPEECH, VADState.SPEECH_OFFSET)
        return VADFrameResult(
            is_speech=is_active_speech,
            probability=prob,
            state=self._state,
            energy=rms,
            speech_started=speech_started,
            speech_ended=speech_ended,
        )


class AudioStreamBuffer:
    """Ring buffer for streaming 16kHz mono audio frames, chunking, and speech endpointing."""

    def __init__(
        self,
        sample_rate: int = 16000,
        frame_size_ms: int = 20,
        max_buffer_seconds: float = 30.0,
        pre_speech_pad_frames: int = 5,
    ) -> None:
        self.sample_rate = sample_rate
        self.frame_size_ms = frame_size_ms
        self.frame_bytes = int(sample_rate * frame_size_ms / 1000) * 2
        self.max_bytes = int(sample_rate * max_buffer_seconds) * 2
        self.pre_speech_pad_frames = pre_speech_pad_frames

        self._buffer = bytearray()
        self._speech_buffer = bytearray()
        self._history_frames: list[bytes] = []
        self._is_recording_speech = False
        self._endpoint_detected = False

    def write_chunk(self, data: bytes) -> None:
        """Append incoming PCM data to ring buffer."""
        self._buffer.extend(data)
        if len(self._buffer) > self.max_bytes:
            # Drop oldest overflow bytes
            overflow = len(self._buffer) - self.max_bytes
            del self._buffer[:overflow]

    def has_frame(self) -> bool:
        return len(self._buffer) >= self.frame_bytes

    def read_frame(self) -> Optional[bytes]:
        """Extract exactly one frame from the buffer, advancing read pointer."""
        if not self.has_frame():
            return None
        frame = bytes(self._buffer[: self.frame_bytes])
        del self._buffer[: self.frame_bytes]

        # Maintain pre-speech pad history
        self._history_frames.append(frame)
        if len(self._history_frames) > self.pre_speech_pad_frames:
            self._history_frames.pop(0)

        return frame

    def on_vad_result(self, frame: bytes, vad_result: VADFrameResult) -> None:
        """Route frame into active speech segment buffer based on VAD state."""
        if vad_result.speech_started:
            self._is_recording_speech = True
            self._endpoint_detected = False
            self._speech_buffer.clear()
            # Include pre-speech padding to catch early consonant onsets
            for hist in self._history_frames[:-1]:
                self._speech_buffer.extend(hist)
            self._speech_buffer.extend(frame)
        elif self._is_recording_speech:
            self._speech_buffer.extend(frame)
            if vad_result.speech_ended:
                self._is_recording_speech = False
                self._endpoint_detected = True

    @property
    def is_recording_speech(self) -> bool:
        return self._is_recording_speech

    def endpoint_detected(self) -> bool:
        return self._endpoint_detected

    def get_speech_segment(self, reset: bool = True) -> bytes:
        """Retrieve the captured speech segment bytes."""
        segment = bytes(self._speech_buffer)
        if reset:
            self._speech_buffer.clear()
            self._endpoint_detected = False
        return segment

    def clear(self) -> None:
        self._buffer.clear()
        self._speech_buffer.clear()
        self._history_frames.clear()
        self._is_recording_speech = False
        self._endpoint_detected = False
