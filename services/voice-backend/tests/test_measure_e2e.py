import inspect
from pathlib import Path
import runpy


def test_measure_accepts_segment_requirement_used_by_cli():
    script = Path(__file__).parents[1] / "deploy" / "measure_e2e.py"
    namespace = runpy.run_path(str(script))

    assert "min_segments" in inspect.signature(namespace["measure"]).parameters
    assert "min_segments" not in inspect.signature(
        namespace["send_streamed_utterance"]
    ).parameters


def test_cached_ack_metrics_do_not_require_tts_latency():
    script = Path(__file__).parents[1] / "deploy" / "measure_e2e.py"
    validate = runpy.run_path(str(script))["validate_first_audio_metrics"]
    metrics = {}

    validate({
        "cached_ack": True,
        "duration_seconds": 0.98,
        "endpoint_to_first_audio_ms": 120,
        "transcript_to_ack_ms": 1,
    }, metrics)

    assert metrics == {
        "duration_seconds": 0.98,
        "endpoint_to_first_audio_ms": 120,
        "transcript_to_ack_ms": 1,
    }
    assert "tts_ms" not in metrics
    assert "first_token_to_first_audio_ms" not in metrics


def test_synthesized_first_audio_still_requires_tts_metrics():
    script = Path(__file__).parents[1] / "deploy" / "measure_e2e.py"
    validate = runpy.run_path(str(script))["validate_first_audio_metrics"]
    metrics = {}

    validate({
        "tts_ms": 300,
        "endpoint_to_first_audio_ms": 500,
        "first_token_to_first_audio_ms": 350,
    }, metrics)

    assert metrics["tts_ms"] == 300
    assert metrics["first_token_to_first_audio_ms"] == 350


def test_stage_c_context_metrics_require_consistent_categories():
    script = Path(__file__).parents[1] / "deploy" / "measure_e2e.py"
    validate = runpy.run_path(str(script))["validate_context_metrics"]
    event = {
        "type": "context.metrics",
        "input_tokens": 1000,
        "output_tokens": 80,
        "context_size": 128000,
        "context_used_tokens": 1000,
        "context_remaining_tokens": 127000,
        "context_breakdown_estimated": True,
        "categories": {
            "system_prompt": 100,
            "skills": 200,
            "tools": 300,
            "session": 400,
        },
    }
    assert validate(event) == event


def test_stage_c_context_metrics_reject_inconsistent_sum():
    script = Path(__file__).parents[1] / "deploy" / "measure_e2e.py"
    validate = runpy.run_path(str(script))["validate_context_metrics"]
    event = {
        "input_tokens": 1000,
        "output_tokens": 80,
        "context_size": 128000,
        "context_used_tokens": 1000,
        "context_remaining_tokens": 127000,
        "categories": {
            "system_prompt": 100,
            "skills": 200,
            "tools": 300,
            "session": 399,
        },
    }
    try:
        validate(event)
    except RuntimeError as error:
        assert "Suma kategorii" in str(error)
    else:
        raise AssertionError("inconsistent category sum was accepted")
