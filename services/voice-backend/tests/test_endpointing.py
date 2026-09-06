import asyncio

from voice_agent.endpointing import (
    FRAME_BYTES,
    EndpointingConfig,
    SpeechEndpointDetector,
)


FRAME = (5_000).to_bytes(2, "little", signed=True) * (FRAME_BYTES // 2)


class SequenceSource:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)
        self.reset_count = 0

    async def speech_probability(self, pcm16, sample_rate=16_000):
        assert len(pcm16) == FRAME_BYTES
        assert sample_rate == 16_000
        return next(self.probabilities)

    def reset(self):
        self.reset_count += 1


async def feed(detector, count, *, output_active=False):
    updates = []
    for _ in range(count):
        updates.extend(await detector.feed(FRAME, output_active=output_active))
    return updates


def test_natural_silence_commits_once_with_prefix_and_trimmed_suffix():
    async def scenario():
        source = SequenceSource([0.0] * 2 + [0.9] * 5 + [0.0] * 24)
        detector = SpeechEndpointDetector(source, EndpointingConfig())

        assert await feed(detector, 2) == []
        started = await feed(detector, 5)
        assert len(started) == 1
        assert started[0].started is not None
        assert started[0].started.barge_in is False
        assert started[0].started.pre_roll_ms == 64

        assert await feed(detector, 23) == []
        stopped = await feed(detector, 1)
        assert len(stopped) == 1
        update = stopped[0]
        assert update.stopped is not None
        assert update.stopped.silence_ms == 768
        assert update.committed is not None
        assert update.committed.reason == "silence"
        # Two prefix, five speech and five retained suffix frames.
        assert len(update.committed.audio) == 12 * FRAME_BYTES
        assert source.reset_count == 1

    asyncio.run(scenario())


def test_short_polish_pause_does_not_end_turn():
    async def scenario():
        probabilities = [0.9] * 5 + [0.0] * 18 + [0.8] + [0.0] * 24
        detector = SpeechEndpointDetector(SequenceSource(probabilities), EndpointingConfig())
        assert (await feed(detector, 5))[0].started is not None
        assert await feed(detector, 18) == []
        assert await feed(detector, 1) == []
        update = (await feed(detector, 24))[0]
        assert update.committed is not None

    asyncio.run(scenario())


def test_short_noise_impulse_never_starts_or_auto_commits():
    async def scenario():
        detector = SpeechEndpointDetector(
            SequenceSource([0.9] * 4 + [0.0] * 30), EndpointingConfig()
        )
        assert await feed(detector, 34) == []
        assert detector.active is False

    asyncio.run(scenario())


def test_barge_in_requires_eight_stable_frames():
    async def scenario():
        detector = SpeechEndpointDetector(
            SequenceSource([0.8] * 8), EndpointingConfig()
        )
        assert await feed(detector, 7, output_active=True) == []
        update = (await feed(detector, 1, output_active=True))[0]
        assert update.started is not None
        assert update.started.barge_in is True

    asyncio.run(scenario())


def test_max_duration_forces_commit():
    async def scenario():
        config = EndpointingConfig(max_audio_ms=256)
        detector = SpeechEndpointDetector(SequenceSource([0.9] * 8), config)
        assert (await feed(detector, 5))[0].started is not None
        assert await feed(detector, 2) == []
        update = (await feed(detector, 1))[0]
        assert update.committed is not None
        assert update.committed.reason == "max_duration"
        assert update.committed.audio_ms == 256

    asyncio.run(scenario())


def test_manual_commit_rejects_audio_without_confirmed_speech():
    source = SequenceSource([0.0])
    detector = SpeechEndpointDetector(source, EndpointingConfig())

    async def scenario():
        assert await detector.feed(b"\x00\x00" * 512, output_active=False) == []

    asyncio.run(scenario())
    assert detector.force_commit() is None


def test_manual_commit_preserves_partial_audio_after_confirmed_speech():
    detector = SpeechEndpointDetector(
        SequenceSource([0.9] * 5), EndpointingConfig()
    )

    async def scenario():
        assert (await feed(detector, 5))[0].started is not None
        assert await detector.feed(b"\x88\x13" * 100, output_active=False) == []

    asyncio.run(scenario())
    commit = detector.force_commit()
    assert commit is not None
    assert commit.reason == "manual"
    assert len(commit.audio) == 5 * FRAME_BYTES + 200


def test_energy_gate_rejects_digitally_silent_false_positive():
    detector = SpeechEndpointDetector(
        SequenceSource([0.99] * 10), EndpointingConfig()
    )

    async def scenario():
        for _ in range(10):
            assert await detector.feed(b"\x00\x00" * 512, output_active=False) == []

    asyncio.run(scenario())
    assert detector.active is False
    assert detector.snapshot.rms_dbfs == -96.0
    assert detector.snapshot.zero_fraction == 1.0
