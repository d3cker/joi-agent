from voice_agent.llm import StreamDelta, ToolCallDelta, parse_sse_data


def test_stream_parser_keeps_reasoning_and_content_separate():
    parsed = parse_sse_data(
        'data: {"choices":[{"delta":{"reasoning":"ukryty tok", "content":"jawna odpowiedź"}}]}'
    )
    assert parsed == StreamDelta(reasoning="ukryty tok", content="jawna odpowiedź")


def test_stream_parser_supports_reasoning_content_alias():
    parsed = parse_sse_data('data: {"choices":[{"delta":{"reasoning_content":"analiza"}}]}')
    assert parsed == StreamDelta(reasoning="analiza", content="")


def test_stream_parser_ignores_done_and_non_data_lines():
    assert parse_sse_data("data: [DONE]") is None
    assert parse_sse_data("event: message") is None


def test_stream_parser_keeps_completion_usage_without_choices():
    parsed = parse_sse_data(
        'data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":37}}'
    )
    assert parsed == StreamDelta(prompt_tokens=12, completion_tokens=37)


def test_stream_parser_ignores_invalid_usage_counts():
    parsed = parse_sse_data(
        'data: {"choices":[],"usage":{"prompt_tokens":"12","completion_tokens":null}}'
    )
    assert parsed == StreamDelta()


def test_stream_parser_preserves_fragmented_tool_call_deltas():
    parsed = parse_sse_data(
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_7","type":"function","function":{"name":"web_","arguments":"{\\"q\\":"}}]}}]}'
    )
    assert parsed == StreamDelta(tool_calls=(ToolCallDelta(
        index=0,
        id="call_7",
        name="web_",
        arguments='{"q":',
    ),))
