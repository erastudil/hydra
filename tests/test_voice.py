import io
import json
import struct
import wave
import pytest

from hydra_cli.voice import (
    AudioChunk,
    AudioStreamBuffer,
    KokoroTTSClient,
    LatencyBreakdown,
    SileroVADDetector,
    VADFrameResult,
    VADState,
    VoicePipelineBenchmark,
    execute_voice_command,
    format_voice_benchmark_human,
)
from hydra_cli.router import route_command


def make_pcm_frame(amplitude: int = 8000, num_samples: int = 320) -> bytes:
    """Generate 16-bit mono PCM frame."""
    return struct.pack(f"<{num_samples}h", *([amplitude] * num_samples))


def make_silent_frame(num_samples: int = 320) -> bytes:
    """Generate 16-bit mono silent frame."""
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


class TestSileroVADDetector:
    def test_invalid_parameters_raise(self):
        with pytest.raises(ValueError, match="frame_size_ms"):
            SileroVADDetector(frame_size_ms=15)
        with pytest.raises(ValueError, match="sample_rate"):
            SileroVADDetector(sample_rate=44100)

    def test_initial_state_silence(self):
        vad = SileroVADDetector(sample_rate=16000, frame_size_ms=20)
        assert vad.state == VADState.SILENCE

    def test_continuous_silence_remains_silence(self):
        vad = SileroVADDetector(sample_rate=16000, frame_size_ms=20)
        silent = make_silent_frame(320)
        for _ in range(5):
            res = vad.process_frame(silent)
            assert not res.is_speech
            assert res.state == VADState.SILENCE
            assert not res.speech_started
            assert not res.speech_ended

    def test_speech_onset_detection_with_hangover(self):
        # Requires 2 consecutive frames to trigger onset to active SPEECH
        vad = SileroVADDetector(
            sample_rate=16000,
            frame_size_ms=20,
            hangover_onset_frames=2,
            energy_threshold=0.01,
        )
        speech_frame = make_pcm_frame(amplitude=12000, num_samples=320)

        # Frame 1: candidate onset
        res1 = vad.process_frame(speech_frame)
        assert res1.state == VADState.SPEECH_ONSET
        assert not res1.speech_started

        # Frame 2: satisfies hangover onset -> enters SPEECH state
        res2 = vad.process_frame(speech_frame)
        assert res2.state == VADState.SPEECH
        assert res2.is_speech
        assert res2.speech_started

    def test_speech_offset_detection_with_hangover(self):
        vad = SileroVADDetector(
            sample_rate=16000,
            frame_size_ms=20,
            hangover_onset_frames=1,
            hangover_offset_frames=3,
            energy_threshold=0.01,
        )
        speech = make_pcm_frame(amplitude=12000, num_samples=320)
        silent = make_silent_frame(320)

        # Trigger speech
        vad.process_frame(speech)
        assert vad.state == VADState.SPEECH

        # Silence frame 1 -> enters SPEECH_OFFSET
        res_s1 = vad.process_frame(silent)
        assert res_s1.state == VADState.SPEECH_OFFSET
        assert res_s1.is_speech  # Hangover keeps active speech flag
        assert not res_s1.speech_ended

        # Silence frame 2 -> remains SPEECH_OFFSET
        res_s2 = vad.process_frame(silent)
        assert res_s2.state == VADState.SPEECH_OFFSET
        assert not res_s2.speech_ended

        # Silence frame 3 -> hangover expired, transitions to SILENCE with speech_ended=True
        res_s3 = vad.process_frame(silent)
        assert res_s3.state == VADState.SILENCE
        assert not res_s3.is_speech
        assert res_s3.speech_ended

    def test_reset_clears_state(self):
        vad = SileroVADDetector(hangover_onset_frames=1)
        vad.process_frame(make_pcm_frame(amplitude=10000))
        assert vad.state == VADState.SPEECH
        vad.reset()
        assert vad.state == VADState.SILENCE


class TestAudioStreamBuffer:
    def test_write_and_read_frame(self):
        buf = AudioStreamBuffer(sample_rate=16000, frame_size_ms=20)
        frame_bytes = 640  # 320 samples * 2 bytes
        assert not buf.has_frame()
        assert buf.read_frame() is None

        # Write partial frame
        buf.write_chunk(b"\x00" * 300)
        assert not buf.has_frame()

        # Complete frame
        buf.write_chunk(b"\x00" * 340)
        assert buf.has_frame()
        frame = buf.read_frame()
        assert frame is not None
        assert len(frame) == frame_bytes
        assert not buf.has_frame()

    def test_speech_segment_accumulation_and_endpointing(self):
        buf = AudioStreamBuffer(sample_rate=16000, frame_size_ms=20, pre_speech_pad_frames=2)
        frame1 = make_silent_frame(320)
        frame2 = make_pcm_frame(amplitude=8000, num_samples=320)
        frame3 = make_silent_frame(320)

        # Write and read frame 1 (silence)
        buf.write_chunk(frame1)
        f1 = buf.read_frame()
        buf.on_vad_result(f1, VADFrameResult(False, 0.1, VADState.SILENCE, 0.0))
        assert not buf.is_recording_speech

        # Write and read frame 2 (speech started)
        buf.write_chunk(frame2)
        f2 = buf.read_frame()
        buf.on_vad_result(f2, VADFrameResult(True, 0.9, VADState.SPEECH, 0.2, speech_started=True))
        assert buf.is_recording_speech
        assert not buf.endpoint_detected()

        # Write and read frame 3 (speech ended)
        buf.write_chunk(frame3)
        f3 = buf.read_frame()
        buf.on_vad_result(f3, VADFrameResult(False, 0.1, VADState.SILENCE, 0.0, speech_ended=True))
        assert not buf.is_recording_speech
        assert buf.endpoint_detected()

        # Segment should include pre-speech pad (frame1) + speech (frame2) + end (frame3)
        segment = buf.get_speech_segment(reset=True)
        assert len(segment) >= len(frame2)
        assert not buf.endpoint_detected()

    def test_buffer_overflow_bounds_memory(self):
        # 1-second max buffer = 32000 bytes
        buf = AudioStreamBuffer(sample_rate=16000, max_buffer_seconds=1.0)
        huge_data = b"\x01" * 50000
        buf.write_chunk(huge_data)
        assert len(buf._buffer) == 32000


class TestKokoroTTSClient:
    def test_synthesize_chunk_properties(self):
        tts = KokoroTTSClient(sample_rate=24000)
        chunk = tts.synthesize_chunk("Order confirmed.", is_first=True)
        assert isinstance(chunk, AudioChunk)
        assert chunk.is_first_chunk
        assert chunk.sample_rate == 24000
        assert chunk.duration_ms > 100.0
        assert len(chunk.data) > 0

    def test_audio_chunk_wav_container(self):
        tts = KokoroTTSClient(sample_rate=24000)
        chunk = tts.synthesize_chunk("Test audio wav header.")
        wav_bytes = chunk.to_wav()
        assert wav_bytes.startswith(b"RIFF")
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 24000
            assert wf.getnframes() > 0

    def test_stream_synthesis_clause_chunking(self):
        tts = KokoroTTSClient()
        tokens = ["Order ", "confirmed,", " shipping ", "today ", "via ", "courier."]
        chunks = list(tts.stream_synthesis(tokens, chunk_token_threshold=4))
        # Clause boundary ',' should trigger chunk 1 early
        assert len(chunks) >= 2
        assert chunks[0].is_first_chunk
        assert not chunks[1].is_first_chunk
        assert "confirmed," in chunks[0].text

    def test_synthesize_full_text(self):
        tts = KokoroTTSClient()
        pcm = tts.synthesize("Complete sentence.")
        assert len(pcm) > 0
        assert len(pcm) % 2 == 0  # 16-bit aligned


class TestVoicePipelineBenchmark:
    def test_sub_500ms_latency_budget_passes(self):
        bench = VoicePipelineBenchmark(budget_ms=500.0)
        breakdown = bench.run_benchmark(simulated_asr_ms=50.0, simulated_ttft_ms=30.0)
        assert isinstance(breakdown, LatencyBreakdown)
        assert breakdown.vad_latency_ms < 15.0
        assert breakdown.asr_latency_ms < 75.0
        assert breakdown.llm_ttft_ms < 50.0
        assert breakdown.tts_first_chunk_ms < 90.0
        assert breakdown.total_latency_ms < 500.0
        assert breakdown.passed

    def test_latency_budget_exceeded_fails(self):
        bench = VoicePipelineBenchmark(budget_ms=50.0)  # Unrealistic 50ms budget
        breakdown = bench.run_benchmark(simulated_asr_ms=60.0, simulated_ttft_ms=40.0)
        assert not breakdown.passed
        assert breakdown.total_latency_ms > breakdown.budget_ms

    def test_human_report_formatting(self):
        breakdown = LatencyBreakdown(
            vad_latency_ms=0.5,
            asr_latency_ms=55.0,
            llm_ttft_ms=30.0,
            tts_first_chunk_ms=25.0,
            audio_playback_ms=12.0,
            total_latency_ms=122.5,
            budget_ms=500.0,
        )
        report = format_voice_benchmark_human(breakdown)
        assert "SUB-500MS TARGET ACHIEVED" in report
        assert "122.50 ms" in report


class TestVoiceCLICommands:
    def test_cli_voice_benchmark_pass(self, capsys):
        ret = route_command(["voice", "benchmark"])
        assert ret == 0
        captured = capsys.readouterr()
        assert "PASS" in captured.out
        assert "LATENCY TRACE" in captured.out

    def test_cli_voice_benchmark_json(self, capsys):
        ret = route_command(["voice", "benchmark", "--json"])
        assert ret == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["passed"] is True
        assert data["total_latency_ms"] < 500.0
        assert "vad_latency_ms" in data

    def test_cli_voice_stream(self, capsys):
        ret = route_command(["voice", "stream", "--text", "Testing voice audio stream."])
        assert ret == 0
        captured = capsys.readouterr()
        assert "Synthesized" in captured.out
        assert "audio chunk" in captured.out

    def test_cli_voice_benchmark_budget_fail(self, capsys):
        # 10ms budget will fail
        ret = route_command(["voice", "benchmark", "--budget", "10.0"])
        assert ret == 1
