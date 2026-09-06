import asyncio
import time

from voice_agent.llm import ChatMessage, StreamDelta, ToolCallDelta
from voice_agent.ack import ACK_TEXT, CachedAcknowledgement
from voice_agent.endpointing import FRAME_BYTES
from voice_agent.protocol import ClientCommand, ReasoningEffort, SessionPhase
from voice_agent.session import (
    ConversationSession,
    StreamingMarkdownSpeechSegmenter,
    TextSegmenter,
    context_usage_payload,
)
from voice_agent.speech_text import MarkdownSpeechRenderer
from voice_agent.stt import MockSTT, MockVAD, TranscriptionResult
from voice_agent.storage import SQLiteSessionStore
from voice_agent.tools import ToolDefinition, ToolExecutionResult, ToolRegistry
from voice_agent.tts import AudioResult, MockTTS


class MemoryTransport:
    def __init__(self):
        self.json = []
        self.binary = []
        self.sequence = []

    async def send_json(self, value):
        self.json.append(value)
        self.sequence.append(("json", value))

    async def send_bytes(self, value):
        self.binary.append(value)
        self.sequence.append(("binary", value))


class SlowBinaryTransport(MemoryTransport):
    def __init__(self):
        super().__init__()
        self.binary_started = asyncio.Event()
        self.release_binary = asyncio.Event()

    async def send_bytes(self, value):
        self.binary_started.set()
        await self.release_binary.wait()
        await super().send_bytes(value)


class SequenceVAD:
    def __init__(self, probabilities):
        self.probabilities = iter(probabilities)

    async def speech_probability(self, pcm16, sample_rate=16_000):
        return next(self.probabilities)

    async def contains_speech(self, pcm16, sample_rate=16_000):
        return (await self.speech_probability(pcm16, sample_rate)) >= 0.5

    def reset(self):
        pass


FRAME = (5_000).to_bytes(2, "little", signed=True) * (FRAME_BYTES // 2)


async def append_frames(session, count):
    for _ in range(count):
        await session.append_audio(FRAME)


def test_context_usage_reconciles_breakdown_to_exact_prompt_total():
    payload = context_usage_payload(
        base_system_prompt="You are a local agent.",
        skills_prompt="- web-research — private search",
        messages=(
            ChatMessage("system", "composed"),
            ChatMessage("user", "Find the current value."),
        ),
        tools=({"type": "function", "function": {"name": "web_search"}},),
        context_size=128_000,
        input_tokens=321,
        output_tokens=17,
        prompt_processing_seconds=0.5,
    )

    assert payload["input_tokens"] == 321
    assert payload["output_tokens"] == 17
    assert payload["context_size"] == 128_000
    assert sum(payload["categories"].values()) == 321
    assert set(payload["categories"]) == {
        "system_prompt", "skills", "tools", "session",
    }
    assert payload["prompt_processing_tokens_per_second"] == 642
    assert payload["input_tokens_estimated"] is False
    assert payload["context_breakdown_estimated"] is True


class RecordingLLM:
    def __init__(self):
        self.effort = None

    async def stream(self, messages, reasoning_effort):
        self.effort = reasoning_effort
        yield StreamDelta(reasoning="NIE WOLNO MÓWIĆ. ")
        yield StreamDelta(content="Witaj. ")
        yield StreamDelta(reasoning="nadal ukryte")
        yield StreamDelta(content="Jak mogę pomóc?")


class PromptRecordingLLM:
    def __init__(self):
        self.system_prompts = []

    async def stream(self, messages, reasoning_effort):
        self.system_prompts.append(messages[0].content)
        yield StreamDelta(content="Gotowe.")


class BlockingLLM:
    async def stream(self, messages, reasoning_effort):
        yield StreamDelta(content="Zaczynam odpowiedź")
        await asyncio.Event().wait()


class BlockingAudioLLM:
    async def stream(self, messages, reasoning_effort):
        yield StreamDelta(content="Pierwszy segment. ")
        await asyncio.Event().wait()


class FastSegmentedLLM:
    async def stream(self, messages, reasoning_effort):
        yield StreamDelta(content="Pierwszy segment. ")
        yield StreamDelta(content="Drugi segment. ")
        yield StreamDelta(content="Trzeci segment. ")
        yield StreamDelta(prompt_tokens=240, completion_tokens=60)


class MarkdownLLM:
    async def stream(self, messages, reasoning_effort):
        yield StreamDelta(content="# Result\n\n- **First** item with ")
        yield StreamDelta(content="[documentation](https://example.com).\n")
        yield StreamDelta(content="```python\nprint('not spoken')\n```\n")


class StreamingMarkdownLLM:
    def __init__(self):
        self.unclosed_emphasis = asyncio.Event()
        self.close_emphasis = asyncio.Event()
        self.finish_response = asyncio.Event()

    async def stream(self, messages, reasoning_effort):
        yield StreamDelta(content="**Pierwsze zdanie.")
        self.unclosed_emphasis.set()
        await self.close_emphasis.wait()
        yield StreamDelta(content="** ")
        await self.finish_response.wait()
        yield StreamDelta(content="Drugie zdanie z (liczbą 1969) 😊.")
        yield StreamDelta(completion_tokens=20)


class StreamingTableLLM:
    response = """Oto lista umiejętności:

| Slug | Nazwa | Opis | Wersja |
|---|---|---|---|
| open-terminal-work | Open Terminal Work | Przegląda pliki i uruchamia kod. | 1 |
| web-research | Web research | Wyszukuje przez prywatny SearXNG. | 1 |

Dostępne są dwie umiejętności. To jest ostatnie zdanie.
"""

    async def stream(self, messages, reasoning_effort):
        for offset in range(0, len(self.response), 13):
            yield StreamDelta(content=self.response[offset : offset + 13])
        yield StreamDelta(completion_tokens=80)


class ImmediateRecordingTTS:
    def __init__(self):
        self.texts = []

    async def synthesize(self, text, language="pl"):
        self.texts.append(text)
        return AudioResult(data=("wav:" + text).encode(), sample_rate=24_000)


class LanguageRecordingSTT:
    def __init__(self):
        self.languages = []

    async def transcribe(self, pcm16, sample_rate=16_000, language="pl"):
        self.languages.append(language)
        text = (
            "Tell me what happened in 1969 and include 3.14."
            if language == "en"
            else "Powiedz, co wydarzyło się w 1969 roku."
        )
        return TranscriptionResult(
            text=text,
            accepted=True,
            confidence=0.99,
            language=language,
        )


class LanguageAwareLLM:
    def __init__(self):
        self.prompts = []

    async def stream(self, messages, reasoning_effort):
        prompt = messages[0].content
        self.prompts.append(prompt)
        if "English (en)" in prompt:
            yield StreamDelta(content="In 1969, the value was 3.14.")
        else:
            yield StreamDelta(content="W 1969 roku wartość wynosiła 3.14.")


class GatedTTS:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.texts = []
        self.active = 0
        self.max_active = 0

    async def synthesize(self, text, language="pl"):
        self.texts.append(text)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            return AudioResult(data=("wav:" + text).encode(), sample_rate=24_000)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1


class ToolCallingLLM:
    def __init__(self):
        self.round = 0
        self.tools = None

    async def stream(self, messages, reasoning_effort, tools=()):
        self.tools = tools
        if self.round == 0:
            self.round += 1
            yield StreamDelta(tool_calls=(ToolCallDelta(
                0, id="call-", name="ec", arguments='{"text":"'
            ),))
            yield StreamDelta(tool_calls=(ToolCallDelta(
                0, id="1", name="ho", arguments='witaj"}'
            ),), completion_tokens=8)
            return
        assert messages[-1].role == "tool"
        assert messages[-1].content == "witaj"
        yield StreamDelta(content="Narzędzie odpowiedziało: witaj.")
        yield StreamDelta(completion_tokens=12)


class BlockingToolLLM:
    async def stream(self, messages, reasoning_effort, tools=()):
        yield StreamDelta(tool_calls=(ToolCallDelta(
            0, id="call-block", name="block", arguments="{}"
        ),))


class ToolUntilDisabledLLM:
    def __init__(self):
        self.round = 0
        self.tool_counts = []

    async def stream(self, messages, reasoning_effort, tools=()):
        self.tool_counts.append(len(tools))
        if tools:
            self.round += 1
            yield StreamDelta(tool_calls=(ToolCallDelta(
                0,
                id=f"call-{self.round}",
                name="echo",
                arguments=f'{{"text":"round-{self.round}"}}',
            ),))
            return
        yield StreamDelta(content="Finalna odpowiedź bez kolejnego narzędzia.")
        yield StreamDelta(completion_tokens=9)


class ReloadingDefinitionsExecutor:
    def __init__(self):
        self.reloaded = False

    def definitions(self):
        return (
            ToolDefinition("reload_skills", "Reload", {"type": "object"}),
            ToolDefinition("read_skill", "Read", {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["old-skill", "new-skill"]
                        if self.reloaded else ["old-skill"],
                    }
                },
            }),
        )

    async def execute(self, name, arguments):
        assert name == "reload_skills"
        self.reloaded = True
        return ToolExecutionResult('{"status":"active","revision":2}')


class ReloadingDefinitionsLLM:
    def __init__(self):
        self.read_skill_enums = []

    async def stream(self, messages, reasoning_effort, tools=()):
        read_skill = next(
            item for item in tools if item["function"]["name"] == "read_skill"
        )
        self.read_skill_enums.append(
            read_skill["function"]["parameters"]["properties"]["name"]["enum"]
        )
        if len(self.read_skill_enums) == 1:
            yield StreamDelta(tool_calls=(ToolCallDelta(
                0, id="reload-1", name="reload_skills", arguments="{}"
            ),))
            return
        yield StreamDelta(content="Katalog odświeżony.")


def event_types(transport):
    return [item["type"] for item in transport.json]


def test_segmenter_prefers_sentence_boundaries_and_flushes_tail():
    segmenter = TextSegmenter()
    assert segmenter.push("Pierwsze zdanie. Dru") == ["Pierwsze zdanie."]
    assert segmenter.push("gie") == []
    assert segmenter.flush() == "Drugie"


def test_agent_loop_executes_fragmented_tool_call_and_persists_the_turn():
    async def scenario():
        transport = MemoryTransport()
        llm = ToolCallingLLM()
        registry = ToolRegistry()

        async def echo(arguments):
            return ToolExecutionResult(arguments["text"])

        registry.register(ToolDefinition(
            "echo",
            "Return text for a contract test",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ), echo)
        persistence = SQLiteSessionStore(":memory:")
        opened = persistence.open("durable-session", "system")
        session = ConversationSession(
            transport,
            llm,
            MockSTT("Użyj narzędzia"),
            MockVAD(),
            MockTTS(),
            "system",
            session_id="durable-session",
            session_store=persistence,
            tool_executor=registry,
            messages=list(opened.messages),
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        assert llm.tools[0]["function"]["name"] == "echo"
        assert event_types(transport).count("tool.call.started") == 1
        assert event_types(transport).count("tool.call.completed") == 1
        assert "tool.call.failed" not in event_types(transport)
        assert [item.role for item in session.messages] == [
            "system", "user", "assistant", "tool", "assistant"
        ]
        persisted = persistence.get("durable-session")
        assert persisted is not None
        assert persisted.messages == tuple(session.messages)
        assert [
            item["text"] for item in transport.json
            if item["type"] == "assistant.done"
        ] == ["Narzędzie odpowiedziało: witaj."]

        resumed_transport = MemoryTransport()
        resumed = ConversationSession(
            resumed_transport,
            RecordingLLM(),
            MockSTT(),
            MockVAD(),
            MockTTS(),
            "system",
            session_id="durable-session",
            resumed=True,
            messages=list(persisted.messages),
        )
        await resumed.start()
        ready = resumed_transport.json[0]
        history = resumed_transport.json[1]
        assert ready["resumed"] is True
        assert ready["session_id"] == "durable-session"
        assert history["type"] == "session.history"
        assert [item["role"] for item in history["messages"]] == [
            "user", "assistant"
        ]

    asyncio.run(scenario())


def test_response_cancel_propagates_into_active_tool_call():
    async def scenario():
        transport = MemoryTransport()
        registry = ToolRegistry()
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def block(_arguments):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        registry.register(ToolDefinition(
            "block", "Wait until cancelled", {"type": "object"}
        ), block)
        session = ConversationSession(
            transport,
            BlockingToolLLM(),
            MockSTT("Zatrzymaj narzędzie"),
            MockVAD(),
            MockTTS(),
            "system",
            tool_executor=registry,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.wait_for(started.wait(), timeout=0.2)
        await session.handle_command(ClientCommand("response.cancel"))

        assert cancelled.is_set()
        assert not session.response_active
        assert event_types(transport).count("tool.call.started") == 1
        assert event_types(transport).count("response.cancelled") == 1
        assert session.phase is SessionPhase.LISTENING

    asyncio.run(scenario())


def test_tool_budget_forces_a_final_round_without_tool_definitions():
    async def scenario():
        transport = MemoryTransport()
        llm = ToolUntilDisabledLLM()
        registry = ToolRegistry()
        executed = []

        async def echo(arguments):
            executed.append(arguments["text"])
            return ToolExecutionResult(arguments["text"])

        registry.register(ToolDefinition(
            "echo",
            "Return text for a tool-budget test",
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ), echo)
        session = ConversationSession(
            transport,
            llm,
            MockSTT("Wykonaj zadanie"),
            MockVAD(),
            MockTTS(),
            "system",
            tool_executor=registry,
            max_tool_steps=2,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.wait_for(session.wait_for_response(), timeout=1)

        assert llm.tool_counts == [1, 1, 0]
        assert executed == ["round-1", "round-2"]
        assert event_types(transport).count("tool.call.completed") == 2
        done = [
            item for item in transport.json if item["type"] == "assistant.done"
        ]
        assert done[-1]["text"] == "Finalna odpowiedź bez kolejnego narzędzia."

    asyncio.run(scenario())


def test_tool_definitions_are_refreshed_after_reload_round():
    async def scenario():
        transport = MemoryTransport()
        llm = ReloadingDefinitionsLLM()
        session = ConversationSession(
            transport,
            llm,
            MockSTT("Odśwież skille"),
            MockVAD(),
            MockTTS(),
            "system",
            tool_executor=ReloadingDefinitionsExecutor(),
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        assert llm.read_skill_enums == [
            ["old-skill"], ["old-skill", "new-skill"]
        ]
        assert event_types(transport).count("tool.call.completed") == 1

    asyncio.run(scenario())


def test_segmenter_limits_words_and_chars_without_cutting_urls_or_decimals():
    segmenter = TextSegmenter(soft_limit=78, hard_limit=84, max_words=12)
    text = (
        "To jest dłuższy fragment odpowiedzi, który powinien zostać podzielony "
        "przy przecinku bez uszkodzenia https://example.com/a.b ani liczby 12.75."
    )
    parts = segmenter.push(text)
    tail = segmenter.flush()
    if tail:
        parts.append(tail)
    assert " ".join(parts) == text
    assert all(len(part) <= 84 or "https://" in part for part in parts)
    assert all(len(part.split()) <= 12 for part in parts)
    assert any("https://example.com/a.b" in part for part in parts)
    assert any("12.75" in part for part in parts)


def test_segmenter_does_not_treat_polish_abbreviations_as_sentence_end():
    examples = (
        "Sprawdź (np. CoinMarketCap) i opisz wynik.",
        "Kup owoce itp. przed powrotem do domu.",
        "Pomogli m.in. dr Kowalski i inni eksperci.",
        "Rozmawiał dr. Kowalski oraz prof. Nowak.",
    )
    for text in examples:
        segmenter = TextSegmenter()
        assert segmenter.push(text + " ") == [text]


def test_streaming_number_abbreviations_wait_for_the_following_value():
    polish = StreamingMarkdownSpeechSegmenter(
        MarkdownSpeechRenderer("pl"), soft_limit=180, hard_limit=240, max_words=36
    )
    assert polish.push("Cena to ok. ") == []
    assert polish.push("200 000 zł. ") == [
        "Cena to około dwieście tysięcy złotych."
    ]

    english = StreamingMarkdownSpeechSegmenter(
        MarkdownSpeechRenderer("en"), soft_limit=180, hard_limit=240, max_words=36
    )
    assert english.push("There were approx. ") == []
    assert english.push("200,000 users. ") == [
        "There were approximately two hundred thousand users."
    ]


def test_streaming_parenthesized_approximation_and_temperature_are_atomic():
    segmenter = StreamingMarkdownSpeechSegmenter(
        MarkdownSpeechRenderer("pl"), soft_limit=180, hard_limit=240, max_words=36
    )
    assert segmenter.push("Wynik (") == []
    assert segmenter.push("ok. ") == []
    assert segmenter.push("14), a temperatura to ok. ") == []
    assert segmenter.push("–170°C. ") == [
        "Wynik, około czternaście, a temperatura to około minus sto siedemdziesiąt "
        "stopni Celsjusza."
    ]


def test_streaming_markdown_speech_waits_for_balanced_markup_and_normalizes():
    segmenter = StreamingMarkdownSpeechSegmenter(
        MarkdownSpeechRenderer(), soft_limit=180, hard_limit=240, max_words=36
    )
    assert segmenter.push("To jest **ważne zdanie.") == []
    assert segmenter.push("** Kolejne zdanie z (rokiem 1969) 😊. ") == [
        "To jest ważne zdanie.",
        "Kolejne zdanie z, rokiem tysiąc dziewięćset sześćdziesiąt dziewięć.",
    ]
    assert segmenter.flush() == []


def test_streaming_markdown_speech_keeps_complete_table_rows_and_final_tail():
    source = """Oto lista wszystkich dostępnych umiejętności:

| Slug | Nazwa | Opis | Wersja |
|---|---|---|---|
| open-terminal-work | Open Terminal Work | Przegląda pliki i uruchamia kod. | 1 |
| project-engineering | Project engineering | Wprowadza zmiany w repozytorium. | 1 |
| skill-authoring | Skill authoring | Rozszerza agenta przez SKILL.md. | 1 |
| web-research | Web research | Wyszukuje przez prywatny SearXNG. | 1 |

Dostępne są cztery umiejętności. Daj znać, jeśli któraś może pomóc.
"""
    renderer = MarkdownSpeechRenderer()
    segmenter = StreamingMarkdownSpeechSegmenter(
        renderer, soft_limit=78, hard_limit=84, max_words=12
    )
    spoken: list[str] = []
    for offset in range(0, len(source), 13):
        spoken.extend(segmenter.push(source[offset : offset + 13]))
    spoken.extend(segmenter.flush())
    joined = " ".join(spoken)

    assert "Slug: open terminal work" in joined
    assert "Slug: project engineering" in joined
    assert "Slug: skill authoring" in joined
    assert "Slug: web research" in joined
    assert "Dostępne są cztery umiejętności." in joined
    assert joined.endswith("Daj znać, jeśli któraś może pomóc.")
    assert "---" not in joined


def test_streaming_speech_verbalizes_english_lists_and_paths_before_chunking():
    source = (
        "1. Open `/home/user/first_script.py`.\n"
        "2. Run it with `MODE=fast`.\n"
        "3. Verify version v2.3.4 and the value 3.14."
    )
    segmenter = StreamingMarkdownSpeechSegmenter(
        MarkdownSpeechRenderer("en"),
        soft_limit=78,
        hard_limit=100,
        max_words=18,
    )
    spoken: list[str] = []
    for offset in range(0, len(source), 7):
        spoken.extend(segmenter.push(source[offset : offset + 7]))
    spoken.extend(segmenter.flush())
    joined = " ".join(spoken)

    assert joined.startswith("First, Open slash home slash user")
    assert "Second, Run it with MODE equals fast." in joined
    assert "Third, Verify version two point three point four" in joined
    assert "three point one four" in joined
    assert all(character not in joined for character in "_/@=")


def test_session_emits_only_content_and_passes_explicit_effort():
    async def scenario():
        transport = MemoryTransport()
        llm = RecordingLLM()
        session = ConversationSession(transport, llm, MockSTT("Cześć"), MockVAD(), MockTTS(), "system")
        await session.start()
        ready = next(item for item in transport.json if item["type"] == "session.ready")
        assert ready["turn_detection"]["type"] == "server_vad"
        assert ready["turn_detection"]["silence_ms"] == 768
        await session.handle_command(ClientCommand("session.configure", ReasoningEffort.HIGH))
        await append_frames(session, 5)
        await session.handle_command(ClientCommand("input_audio.commit"))
        await session.wait_for_response()

        assert llm.effort == "high"
        deltas = "".join(item["text"] for item in transport.json if item["type"] == "assistant.delta")
        assert deltas == "Witaj. Jak mogę pomóc?"
        assert "NIE WOLNO" not in deltas
        assert session.messages[-1].content == "Witaj. Jak mogę pomóc?"
        assert event_types(transport).count("audio.chunk") == len(transport.binary) == 2
        transcript = next(
            item for item in transport.json if item["type"] == "transcript.final"
        )
        assert transcript["utterance_id"]
        assert transcript["stt_ms"] >= 0
        assert transcript["endpoint_to_stt_ms"] >= transcript["stt_ms"]
        first_delta = next(
            item
            for item in transport.json
            if item["type"] == "assistant.delta" and "llm_ttft_ms" in item
        )
        assert first_delta["llm_ttft_ms"] >= 0
        assert first_delta["endpoint_to_first_token_ms"] >= 0
        first_marker = next(
            item
            for item in transport.json
            if item["type"] == "audio.chunk" and item["index"] == 0
        )
        assert first_marker["tts_ms"] >= 0
        assert first_marker["endpoint_to_first_audio_ms"] >= 0
        assert first_marker["first_token_to_first_audio_ms"] >= 0
        response_ids = {
            item["response_id"]
            for item in transport.json
            if item["type"] in {"assistant.delta", "assistant.done", "audio.start", "audio.chunk", "audio.end"}
        }
        assert len(response_ids) == 1
        response_id = response_ids.pop()
        assert session.phase is SessionPhase.SPEAKING
        await session.handle_command(
            ClientCommand("output_audio.playback.done", response_id=response_id)
        )
        assert transport.json[-1] == {"type": "session.state", "state": "listening"}

        markers = [
            index
            for index, entry in enumerate(transport.sequence)
            if entry[0] == "json" and entry[1]["type"] == "audio.chunk"
        ]
        assert len(markers) == 2
        assert all(transport.sequence[index + 1][0] == "binary" for index in markers)

    asyncio.run(scenario())


def test_conversation_language_controls_stt_llm_and_tts_then_switches_back():
    async def scenario():
        transport = MemoryTransport()
        stt = LanguageRecordingSTT()
        llm = LanguageAwareLLM()
        tts = ImmediateRecordingTTS()
        session = ConversationSession(
            transport,
            llm,
            stt,
            MockVAD(),
            tts,
            "base prompt",
            conversation_language="pl",
        )
        await session.start()
        await session.handle_command(ClientCommand(
            "session.configure",
            ReasoningEffort.LOW,
            conversation_language="en",
        ))
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()
        first_response_id = next(
            item["response_id"]
            for item in transport.json
            if item["type"] == "audio.start"
        )
        await session.handle_command(ClientCommand(
            "output_audio.playback.done", response_id=first_response_id
        ))

        assert stt.languages == ["en"]
        assert "Respond entirely in English" in llm.prompts[0]
        assert tts.texts == [
            "In nineteen sixty-nine, the value was three point one four."
        ]
        assert not any(item.get("cached_ack") for item in transport.json)

        await session.handle_command(ClientCommand(
            "session.configure",
            ReasoningEffort.LOW,
            conversation_language="pl",
        ))
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        assert stt.languages == ["en", "pl"]
        assert "Respond entirely in Polish" in llm.prompts[1]
        assert " ".join(tts.texts[1:]) == (
            "W tysiąc dziewięćset sześćdziesiątym dziewiątym roku wartość "
            "wynosiła trzy przecinek jeden cztery."
        )

    asyncio.run(scenario())


def test_existing_session_uses_current_system_prompt_on_each_llm_turn():
    async def scenario():
        transport = MemoryTransport()
        llm = PromptRecordingLLM()
        current = {"value": "Base prompt\n\n- old-skill"}
        session = ConversationSession(
            transport,
            llm,
            MockSTT("Pytanie"),
            MockVAD(),
            MockTTS(),
            "stored historic prompt",
            system_prompt_provider=lambda: current["value"],
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()
        first_response_id = next(
            item["response_id"]
            for item in transport.json
            if item["type"] == "audio.start"
        )
        await session.handle_command(ClientCommand(
            "output_audio.playback.done", response_id=first_response_id
        ))

        current["value"] = "Base prompt\n\n- old-skill\n- new-skill"
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        assert llm.system_prompts[0].startswith("Base prompt\n\n- old-skill\n")
        assert "- new-skill" not in llm.system_prompts[0]
        assert llm.system_prompts[1].startswith(
            "Base prompt\n\n- old-skill\n- new-skill\n"
        )
        assert all(
            "## Active conversation language" in prompt
            for prompt in llm.system_prompts
        )
        assert session.messages[0].content == "stored historic prompt"

    asyncio.run(scenario())


def test_llm_stream_and_done_are_not_blocked_by_slow_tts_and_audio_stays_ordered():
    async def scenario():
        transport = MemoryTransport()
        tts = GatedTTS()
        session = ConversationSession(
            transport,
            FastSegmentedLLM(),
            MockSTT("Cześć"),
            MockVAD(),
            tts,
            "system",
            tts_queue_max_segments=2,
        )
        await append_frames(session, 5)
        started_at = time.monotonic()
        await session.commit_audio()
        await asyncio.wait_for(tts.started.wait(), timeout=0.2)

        # The bounded queue is already full/backlogged, yet LLM forwarding has
        # completed without waiting for the first synthesis to be released.
        for _ in range(20):
            if "assistant.done" in event_types(transport):
                break
            await asyncio.sleep(0)
        assert "assistant.done" in event_types(transport)
        assert time.monotonic() - started_at < 0.2
        assert "".join(
            item["text"] for item in transport.json
            if item["type"] == "assistant.delta"
        ) == "Pierwszy segment. Drugi segment. Trzeci segment. "
        assert "audio.chunk" not in event_types(transport)
        llm_metrics = [
            item for item in transport.json
            if item["type"] == "response.metrics" and "llm_generation_ms" in item
        ][-1]
        assert llm_metrics["completion_tokens"] == 60
        assert llm_metrics["input_tokens"] == 240
        assert llm_metrics["output_tokens"] == 60
        assert llm_metrics["context_size"] == 128_000
        assert llm_metrics["prompt_processing_tokens_per_second"] > 0
        assert llm_metrics["tokens_estimated"] is False
        assert llm_metrics["tokens_per_second"] >= 60
        context_metrics = [
            item for item in transport.json
            if item["type"] == "context.metrics" and item["input_tokens"] == 240
        ][-1]
        assert sum(context_metrics["categories"].values()) == 240
        assert context_metrics["output_tokens"] == 60
        assert context_metrics["context_breakdown_estimated"] is True

        tts.release.set()
        await asyncio.wait_for(session.wait_for_response(), timeout=1)
        markers = [
            item for item in transport.json if item["type"] == "audio.chunk"
        ]
        assert [item["index"] for item in markers] == [0, 1, 2]
        assert [item["text"] for item in markers] == [
            "Pierwszy segment.", "Drugi segment.", "Trzeci segment."
        ]
        assert len(transport.binary) == 3
        assert tts.max_active == 1
        assert event_types(transport).index("assistant.done") < event_types(transport).index("audio.chunk")
        assert event_types(transport)[-1] == "audio.end"
        final_metrics = [
            item for item in transport.json
            if item["type"] == "response.metrics" and "tts_total_ms" in item
        ][-1]
        assert final_metrics["tts_queue_depth"] == 0
        assert final_metrics["tts_segments"] == 3
        assert final_metrics["tts_total_ms"] >= 0

    asyncio.run(scenario())


def test_cancel_stops_active_tts_and_drops_queued_segments_without_orphan_audio():
    async def scenario():
        transport = MemoryTransport()
        tts = GatedTTS()
        session = ConversationSession(
            transport,
            FastSegmentedLLM(),
            MockSTT("Cześć"),
            MockVAD(),
            tts,
            "system",
            tts_queue_max_segments=1,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.wait_for(tts.started.wait(), timeout=0.2)
        for _ in range(20):
            if "assistant.done" in event_types(transport):
                break
            await asyncio.sleep(0)
        assert "assistant.done" in event_types(transport)

        await asyncio.wait_for(
            session.handle_command(ClientCommand("response.cancel")), timeout=1
        )
        assert tts.cancelled.is_set()
        assert tts.texts == ["Pierwszy segment."]
        assert "audio.chunk" not in event_types(transport)
        audio_end = [item for item in transport.json if item["type"] == "audio.end"]
        assert audio_end[-1]["cancelled"] is True
        cancelled = [
            item for item in transport.json if item["type"] == "response.cancelled"
        ]
        assert cancelled[-1]["reason"] == "client"
        assert session.phase is SessionPhase.LISTENING

        # Releasing the abandoned fixture later cannot produce queued audio.
        tts.release.set()
        await asyncio.sleep(0)
        assert "audio.chunk" not in event_types(transport)

    asyncio.run(scenario())


def test_deferred_tts_renders_complete_markdown_before_segmenting():
    async def scenario():
        transport = MemoryTransport()
        tts = ImmediateRecordingTTS()
        session = ConversationSession(
            transport,
            MarkdownLLM(),
            MockSTT("Question"),
            MockVAD(),
            tts,
            "system",
            tts_defer_until_llm_done=True,
            tts_segment_soft_limit=400,
            tts_segment_hard_limit=480,
            tts_segment_max_words=80,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        assert tts.texts == ["Result.", "First item with documentation."]
        displayed = [
            item for item in transport.json if item["type"] == "assistant.done"
        ][-1]["text"]
        assert "**First**" in displayed
        assert "```python" in displayed

    asyncio.run(scenario())


def test_streaming_tts_starts_after_first_complete_markdown_sentence():
    async def scenario():
        transport = MemoryTransport()
        llm = StreamingMarkdownLLM()
        tts = GatedTTS()
        session = ConversationSession(
            transport,
            llm,
            MockSTT("Pytanie"),
            MockVAD(),
            tts,
            "system",
            tts_defer_until_llm_done=False,
            tts_segment_soft_limit=180,
            tts_segment_hard_limit=240,
            tts_segment_max_words=36,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.wait_for(llm.unclosed_emphasis.wait(), timeout=0.2)
        await asyncio.sleep(0)
        assert tts.texts == []

        llm.close_emphasis.set()
        await asyncio.wait_for(tts.started.wait(), timeout=0.2)
        assert tts.texts == ["Pierwsze zdanie."]
        assert "assistant.done" not in event_types(transport)

        llm.finish_response.set()
        tts.release.set()
        await asyncio.wait_for(session.wait_for_response(), timeout=1)
        assert len(tts.texts) == 2
        assert "1969" not in tts.texts[1]
        assert "😊" not in tts.texts[1]
        assert "(" not in tts.texts[1] and ")" not in tts.texts[1]
        displayed = [
            item for item in transport.json if item["type"] == "assistant.done"
        ][-1]["text"]
        assert displayed == "**Pierwsze zdanie.** Drugie zdanie z (liczbą 1969) 😊."
        metrics = [
            item for item in transport.json
            if item["type"] == "response.metrics" and "llm_generation_ms" in item
        ][-1]
        assert metrics["tts_streaming"] is True

    asyncio.run(scenario())


def test_session_streaming_tts_speaks_table_body_and_last_sentence():
    async def scenario():
        transport = MemoryTransport()
        tts = ImmediateRecordingTTS()
        session = ConversationSession(
            transport,
            StreamingTableLLM(),
            MockSTT("Pokaż skille"),
            MockVAD(),
            tts,
            "system",
            tts_defer_until_llm_done=False,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        spoken = " ".join(tts.texts)
        assert "Slug: open terminal work" in spoken
        assert "Slug: web research" in spoken
        assert "Dostępne są dwie umiejętności." in spoken
        assert spoken.endswith("To jest ostatnie zdanie.")
        assert "---" not in spoken
        done = [
            item for item in transport.json if item["type"] == "assistant.done"
        ][-1]
        assert done["text"] == StreamingTableLLM.response.strip()

    asyncio.run(scenario())


def test_cached_ack_precedes_llm_and_tts_without_duplicate_audio_start():
    async def scenario():
        transport = MemoryTransport()
        ack = CachedAcknowledgement(b"cached-wav", 24_000, 0.8, "/cache/ack.wav")
        session = ConversationSession(
            transport,
            FastSegmentedLLM(),
            MockSTT("Przeanalizuj dokładnie wszystkie dane i przygotuj dłuższe podsumowanie."),
            MockVAD(),
            MockTTS(),
            "system",
            acknowledgement=ack,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()

        markers = [item for item in transport.json if item["type"] == "audio.chunk"]
        assert markers[0]["index"] == 0
        assert markers[0]["cached_ack"] is True
        assert markers[0]["text"] == ACK_TEXT
        assert [item["index"] for item in markers] == [0, 1, 2, 3]
        assert event_types(transport).count("audio.start") == 1
        ack_marker_position = next(
            index for index, item in enumerate(transport.sequence)
            if item[0] == "json" and item[1].get("cached_ack") is True
            and item[1]["type"] == "audio.chunk"
        )
        first_delta_position = next(
            index for index, item in enumerate(transport.sequence)
            if item[0] == "json" and item[1]["type"] == "assistant.delta"
        )
        assert ack_marker_position < first_delta_position
        assert transport.sequence[ack_marker_position + 1] == ("binary", b"cached-wav")

    asyncio.run(scenario())


def test_cancel_after_cached_ack_closes_audio_and_cancels_response():
    async def scenario():
        transport = MemoryTransport()
        ack = CachedAcknowledgement(b"cached-wav", 24_000, 0.8, "/cache/ack.wav")
        session = ConversationSession(
            transport,
            BlockingLLM(),
            MockSTT("Sprawdź proszę status."),
            MockVAD(),
            MockTTS(),
            "system",
            acknowledgement=ack,
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.sleep(0)
        assert session.response_active
        assert any(item.get("cached_ack") for item in transport.json)

        await session.handle_command(ClientCommand("response.cancel"))

        assert not session.response_active
        assert [item for item in transport.json if item["type"] == "audio.end"][-1]["cancelled"] is True
        assert [item for item in transport.json if item["type"] == "response.cancelled"][-1]["reason"] == "client"

    asyncio.run(scenario())


def test_barge_in_cancels_active_response():
    async def scenario():
        transport = MemoryTransport()
        vad = SequenceVAD([0.9] * 5 + [0.8] * 8)
        session = ConversationSession(
            transport, BlockingLLM(), MockSTT("Cześć"), vad, MockTTS(), "system"
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.sleep(0)
        assert session.response_active
        await append_frames(session, 7)
        assert session.response_active
        await append_frames(session, 1)
        assert not session.response_active
        assert session.phase is SessionPhase.LISTENING
        started = [
            item for item in transport.json if item["type"] == "input_audio.speech_started"
        ]
        assert started[-1]["barge_in"] is True
        done = [item for item in transport.json if item["type"] == "assistant.done"]
        assert done[-1]["cancelled"] is True
        cancelled = [item for item in transport.json if item["type"] == "response.cancelled"]
        assert cancelled[-1]["reason"] == "barge_in"

    asyncio.run(scenario())


def test_endpointing_commits_after_silence_without_client_command():
    async def scenario():
        transport = MemoryTransport()
        vad = SequenceVAD([0.9] * 5 + [0.0] * 24)
        session = ConversationSession(
            transport, RecordingLLM(), MockSTT("Automatyczna tura"), vad, MockTTS(), "system"
        )

        await append_frames(session, 5)
        assert event_types(transport).count("input_audio.speech_started") == 1
        await append_frames(session, 23)
        assert "transcript.final" not in event_types(transport)
        await append_frames(session, 1)
        await session.wait_for_response()

        assert event_types(transport).count("input_audio.speech_stopped") == 1
        committed = [item for item in transport.json if item["type"] == "input_audio.committed"]
        assert committed[-1]["reason"] == "silence"
        transcripts = [item for item in transport.json if item["type"] == "transcript.final"]
        assert transcripts[-1]["text"] == "Automatyczna tura"

    asyncio.run(scenario())


def test_manual_commit_without_confirmed_speech_is_user_visible_and_skips_stt():
    class FailingIfCalledSTT:
        async def transcribe(self, pcm16, sample_rate=16_000):
            raise AssertionError("STT must not run for unconfirmed speech")

    async def scenario():
        transport = MemoryTransport()
        session = ConversationSession(
            transport,
            RecordingLLM(),
            FailingIfCalledSTT(),
            SequenceVAD([0.0] * 5),
            MockTTS(),
            "system",
        )
        silence = b"\x00\x00" * (FRAME_BYTES // 2)
        for _ in range(5):
            await session.append_audio(silence)
        await session.handle_command(ClientCommand("input_audio.commit"))

        rejected = [
            item for item in transport.json if item["type"] == "input_audio.rejected"
        ]
        assert rejected[-1]["reason"] == "no_speech"
        assert rejected[-1]["message"] == "Nie wykryto mowy"
        assert "transcript.final" not in event_types(transport)
        vad = [item for item in transport.json if item["type"] == "input_audio.vad"]
        assert vad
        assert vad[-1]["rms_dbfs"] <= -95
        assert vad[-1]["zero_fraction"] == 1.0

    asyncio.run(scenario())


def test_stale_playback_ack_does_not_release_current_response():
    async def scenario():
        transport = MemoryTransport()
        session = ConversationSession(
            transport, RecordingLLM(), MockSTT("Cześć"), MockVAD(), MockTTS(), "system"
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await session.wait_for_response()
        active_id = session._playback_response_id
        assert active_id is not None

        await session.handle_command(
            ClientCommand("output_audio.playback.done", response_id="stale")
        )
        assert session._playback_response_id == active_id
        assert session.phase is SessionPhase.SPEAKING

        await session.handle_command(
            ClientCommand("output_audio.playback.done", response_id=active_id)
        )
        assert session._playback_response_id is None
        assert session.phase is SessionPhase.LISTENING

    asyncio.run(scenario())


def test_manual_cancel_is_idempotent():
    async def scenario():
        transport = MemoryTransport()
        session = ConversationSession(
            transport, BlockingLLM(), MockSTT("Cześć"), MockVAD(), MockTTS(), "system"
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.sleep(0)
        await session.handle_command(ClientCommand("response.cancel"))
        await session.handle_command(ClientCommand("response.cancel"))
        assert session.phase is SessionPhase.LISTENING
        cancelled = [item for item in transport.json if item["type"] == "response.cancelled"]
        assert len(cancelled) == 1
        assert cancelled[0]["reason"] == "client"

    asyncio.run(scenario())


def test_cancel_cannot_split_audio_marker_from_binary_frame():
    async def scenario():
        transport = SlowBinaryTransport()
        session = ConversationSession(
            transport, BlockingAudioLLM(), MockSTT("Cześć"), MockVAD(), MockTTS(), "system"
        )
        await append_frames(session, 5)
        await session.commit_audio()
        await asyncio.wait_for(transport.binary_started.wait(), timeout=1)

        cancel = asyncio.create_task(
            session.handle_command(ClientCommand("response.cancel"))
        )
        await asyncio.sleep(0)
        types_before_release = [
            item[1]["type"]
            for item in transport.sequence
            if item[0] == "json"
        ]
        assert "audio.chunk" in types_before_release
        assert "audio.end" not in types_before_release

        transport.release_binary.set()
        await asyncio.wait_for(cancel, timeout=1)
        marker_index = next(
            index
            for index, item in enumerate(transport.sequence)
            if item[0] == "json" and item[1]["type"] == "audio.chunk"
        )
        assert transport.sequence[marker_index + 1][0] == "binary"
        assert transport.sequence[marker_index + 2][1]["type"] == "audio.end"
        assert transport.sequence[marker_index + 2][1]["cancelled"] is True

    asyncio.run(scenario())
