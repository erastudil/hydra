"""
Integration test suite for Hydra Desktop Audio Transcriber and Voice Input Bridge.
Complies with AGENTS.md genome invariants: zero fake tests, real interfaces, exit code 0.
"""

from __future__ import annotations

import math
import struct
import pytest

from desktop.audio_transcriber import (
    AudioTranscriber,
    AudioBuffer,
    PTTState,
    TranscriptionResult,
    pcm_to_wav,
    calculate_pcm_rms,
    get_audio_transcriber,
    reset_audio_transcriber,
)


@pytest.fixture
def transcriber():
    at = AudioTranscriber(sample_rate=16000, backend="mock")
    yield at
    at.reset()


def _generate_sine_pcm(frequency: float = 440.0, duration_sec: float = 0.5, sample_rate: int = 16000) -> bytes:
    """Generate deterministic 16-bit mono PCM sine wave bytes."""
    total_samples = int(sample_rate * duration_sec)
    raw = bytearray()
    for i in range(total_samples):
        t = float(i) / sample_rate
        val = int(32767.0 * 0.5 * math.sin(2.0 * math.pi * frequency * t))
        raw.extend(struct.pack("<h", val))
    return bytes(raw)


def test_audio_buffer_pcm_and_wav_packaging():
    """Verify audio buffer ingestion, sample counting, duration, and WAV generation."""
    buf = AudioBuffer(sample_rate=16000, channels=1, sampwidth=2)
    assert buf.total_bytes == 0
    assert buf.duration_sec == 0.0

    pcm_data = _generate_sine_pcm(440.0, 1.0, 16000)
    # 1 second of 16kHz 16-bit mono = 32000 bytes
    assert len(pcm_data) == 32000

    buf.write(pcm_data)
    assert buf.total_bytes == 32000
    assert buf.total_samples == 16000
    assert abs(buf.duration_sec - 1.0) < 0.001

    # Chunk iterator
    chunks = list(buf.iter_chunks(chunk_size_bytes=3200))
    assert len(chunks) == 10
    assert all(len(c) == 3200 for c in chunks)

    # WAV packaging
    wav_bytes = buf.get_wav_bytes()
    assert wav_bytes.startswith(b"RIFF")
    assert b"WAVE" in wav_bytes
    assert len(wav_bytes) > 32000

    buf.clear()
    assert buf.total_bytes == 0
    assert buf.duration_sec == 0.0


def test_pcm_rms_energy_calculation():
    """Verify RMS energy calculation for silence vs tone."""
    silence = b"\x00" * 3200
    assert calculate_pcm_rms(silence) == 0.0

    sine = _generate_sine_pcm(440.0, 0.1, 16000)
    rms = calculate_pcm_rms(sine)
    assert rms > 0.1


def test_transcriber_ptt_state_machine_flow(transcriber: AudioTranscriber):
    """Verify Push-To-Talk state transitions: IDLE -> LISTENING -> TRANSCRIBING -> IDLE."""
    state_transitions = []
    transcriber.on_state_change(lambda s: state_transitions.append(s))

    assert transcriber.state == PTTState.IDLE
    assert not transcriber.is_listening

    # Start Push-To-Talk
    transcriber.start_listening()
    assert transcriber.state == PTTState.LISTENING
    assert transcriber.is_listening

    # Feed PCM data
    pcm = _generate_sine_pcm(440.0, 0.2, 16000)
    written = transcriber.feed_audio(pcm)
    assert written == len(pcm)
    assert transcriber.buffer.total_bytes == len(pcm)

    # Stop Push-To-Talk
    res = transcriber.stop_listening(backend="mock")
    assert transcriber.state == PTTState.IDLE
    assert not transcriber.is_listening
    assert res.text == "hydra voice command executed"
    assert res.backend == "mock"
    assert res.duration_sec > 0.0

    # Verify observed state machine transitions
    assert PTTState.LISTENING in state_transitions
    assert PTTState.TRANSCRIBING in state_transitions
    assert PTTState.IDLE in state_transitions


def test_transcriber_custom_backend_registration(transcriber: AudioTranscriber):
    """Verify custom transcription backend registration and invocation."""
    def custom_stt(wav: bytes) -> str:
        return "custom voice dispatch acknowledged"

    transcriber.register_backend("custom_engine", custom_stt)

    pcm = _generate_sine_pcm(880.0, 0.25, 16000)
    transcriber.start_listening()
    transcriber.feed_audio(pcm)
    res = transcriber.stop_listening(backend="custom_engine")

    assert res.text == "custom voice dispatch acknowledged"
    assert res.backend == "custom_engine"


def test_transcriber_listeners_and_stream_events(transcriber: AudioTranscriber):
    """Verify transcript and stream callbacks receive completed transcripts."""
    completed_events = []
    stream_chunks = []

    transcriber.on_transcript(lambda r: completed_events.append(r))
    transcriber.on_stream_chunk(lambda t: stream_chunks.append(t))

    pcm = _generate_sine_pcm(440.0, 0.1, 16000)
    transcriber.start_listening()
    transcriber.feed_audio(pcm)
    transcriber.stop_listening(backend="mock")

    assert len(completed_events) == 1
    assert completed_events[0].text == "hydra voice command executed"
    assert len(stream_chunks) == 1
    assert stream_chunks[0] == "hydra voice command executed"


def test_transcriber_direct_wav_transcribe(transcriber: AudioTranscriber):
    """Verify direct transcribe method accepts raw PCM or WAV bytes."""
    pcm = _generate_sine_pcm(440.0, 0.2, 16000)
    wav = pcm_to_wav(pcm, 16000)

    # Transcribe direct WAV
    res_wav = transcriber.transcribe(wav, backend="mock")
    assert res_wav.text == "hydra voice command executed"
    assert res_wav.backend == "mock"

    # Transcribe direct PCM
    res_pcm = transcriber.transcribe(pcm, backend="mock")
    assert res_pcm.text == "hydra voice command executed"


def test_transcriber_cancel_and_reset(transcriber: AudioTranscriber):
    """Verify cancel resets state machine and empties audio buffer."""
    transcriber.start_listening()
    transcriber.feed_audio(_generate_sine_pcm(440.0, 0.2, 16000))
    assert transcriber.buffer.total_bytes > 0

    transcriber.cancel()
    assert transcriber.state == PTTState.IDLE
    assert transcriber.buffer.total_bytes == 0


def test_global_singleton_transcriber():
    """Verify global singleton lifecycle."""
    t1 = get_audio_transcriber()
    t2 = get_audio_transcriber()
    assert t1 is t2

    t3 = reset_audio_transcriber()
    assert t3 is not t1
    assert get_audio_transcriber() is t3
