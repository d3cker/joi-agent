import Foundation
import XCTest
@testable import VoiceAgentCore

final class ProtocolCodecTests: XCTestCase {
    func testReasoningLevelsMapToExplicitAPIValues() {
        XCTAssertEqual(ReasoningLevel.disabled.apiValue, "none")
        XCTAssertEqual(ReasoningLevel.low.apiValue, "low")
        XCTAssertEqual(ReasoningLevel.high.apiValue, "high")
        XCTAssertEqual(ReasoningLevel.maximum.apiValue, "max")
    }

    func testConfigureCommandAlwaysContainsReasoningEffort() throws {
        let data = try ClientCommand.configure(
            reasoningEffort: "max",
            conversationLanguage: "en"
        ).encoded()
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])
        XCTAssertEqual(json["type"], "session.configure")
        XCTAssertEqual(json["reasoning_effort"], "max")
        XCTAssertEqual(json["conversation_language"], "en")
    }

    func testControlCommandsMatchContract() throws {
        let commit = try XCTUnwrap(JSONSerialization.jsonObject(
            with: ClientCommand.commitAudio.encoded()
        ) as? [String: String])
        let cancel = try XCTUnwrap(JSONSerialization.jsonObject(
            with: ClientCommand.cancelResponse.encoded()
        ) as? [String: String])
        let playbackDone = try XCTUnwrap(JSONSerialization.jsonObject(
            with: ClientCommand.outputAudioPlaybackDone(responseID: "r-7").encoded()
        ) as? [String: String])
        XCTAssertEqual(commit, ["type": "input_audio.commit"])
        XCTAssertEqual(cancel, ["type": "response.cancel"])
        XCTAssertEqual(playbackDone, ["type": "output_audio.playback.done", "response_id": "r-7"])
    }

    func testDecodesBackendEventsAndSeparatesReasoningFromContent() throws {
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"session.ready","session_id":"session-1","resumed":true}"#),
            .sessionReady(sessionID: "session-1", resumed: true)
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"session.history","messages":[{"role":"user","text":"Cześć"},{"role":"assistant","text":"Witaj"}]}"#),
            .sessionHistory([
                SessionHistoryItem(role: "user", text: "Cześć"),
                SessionHistoryItem(role: "assistant", text: "Witaj")
            ])
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"assistant.reasoning.delta","delta":"ukryte"}"#),
            .reasoningDelta("ukryte")
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"assistant.delta","text":"Dzień dobry"}"#),
            .assistantDelta("Dzień dobry")
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"audio.start","response_id":"r-1","sample_rate":24000,"channels":1,"encoding":"wav"}"#),
            .audioStart(responseID: "r-1", sampleRate: 24_000, channels: 1, encoding: "wav")
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"tool.call.started","tool_call_id":"call-1","name":"web_search","arguments":"{\"query\":\"bitcoin price\"}"}"#),
            .toolStarted(
                name: "web_search",
                callID: "call-1",
                arguments: #"{"query":"bitcoin price"}"#
            )
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"tool.call.completed","tool_call_id":"call-1","name":"web_search"}"#),
            .toolCompleted(name: "web_search", callID: "call-1")
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"tool.call.failed","tool_call_id":"call-2","name":"read","content":"not found"}"#),
            .toolFailed(name: "read", callID: "call-2", message: "not found")
        )
    }

    func testToolStartedAcceptsObjectArgumentsAndDefaultsMissingArguments() throws {
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"tool.call.started","name":"read","arguments":{"path":"README.md"}}"#),
            .toolStarted(name: "read", callID: nil, arguments: #"{"path":"README.md"}"#)
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"tool.call.started","name":"reload_skills"}"#),
            .toolStarted(name: "reload_skills", callID: nil, arguments: "{}")
        )
    }

    func testDecodesAudioAndBinaryFrameMarker() throws {
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"audio.chunk","response_id":"r-1","index":2,"audio":"AQID"}"#),
            .audioSegment(AudioSegment(
                descriptor: AudioChunkDescriptor(responseID: "r-1", index: 2, byteLength: 3),
                data: Data([1, 2, 3])
            ))
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"response.canceled","response_id":"r-1"}"#),
            .responseCancelled(responseID: "r-1")
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"audio.chunk","response_id":"r-1","index":3,"byte_length":42,"encoding":"binary-next-frame"}"#),
            .audioChunkDescriptor(AudioChunkDescriptor(responseID: "r-1", index: 3, byteLength: 42))
        )
    }

    func testBinaryAssemblerVerifiesDescriptorAndByteLength() throws {
        let descriptor = AudioChunkDescriptor(responseID: "r-2", index: 0, byteLength: 3)
        var assembler = AudioBinaryAssembler()
        try assembler.accept(descriptor: descriptor)
        XCTAssertEqual(
            try assembler.accept(binary: Data([1, 2, 3])),
            AudioSegment(descriptor: descriptor, data: Data([1, 2, 3]))
        )
        XCTAssertThrowsError(try assembler.accept(binary: Data())) { error in
            XCTAssertEqual(error as? AudioBinaryAssemblyError, .binaryWithoutDescriptor)
        }

        let nextDescriptor = AudioChunkDescriptor(responseID: "r-2", index: 1, byteLength: 3)
        try assembler.accept(descriptor: nextDescriptor)
        XCTAssertThrowsError(try assembler.accept(binary: Data([1]))) { error in
            XCTAssertEqual(
                error as? AudioBinaryAssemblyError,
                .byteLengthMismatch(expected: 3, actual: 1)
            )
        }
    }

    func testBinaryAssemblerRejectsMissingAndDuplicateIndicesPerResponse() throws {
        var assembler = AudioBinaryAssembler()
        let first = AudioChunkDescriptor(responseID: "r-3", index: 0, byteLength: 1)
        try assembler.accept(descriptor: first)
        _ = try assembler.accept(binary: Data([1]))

        XCTAssertThrowsError(try assembler.accept(descriptor: first)) { error in
            XCTAssertEqual(
                error as? AudioBinaryAssemblyError,
                .indexOutOfSequence(responseID: "r-3", expected: 1, actual: 0)
            )
        }
        let missing = AudioChunkDescriptor(responseID: "r-3", index: 2, byteLength: 1)
        XCTAssertThrowsError(try assembler.accept(descriptor: missing)) { error in
            XCTAssertEqual(
                error as? AudioBinaryAssemblyError,
                .indexOutOfSequence(responseID: "r-3", expected: 1, actual: 2)
            )
        }

        // Sequence tracking is independent for concurrent/new response IDs.
        let other = AudioChunkDescriptor(responseID: "r-4", index: 0, byteLength: 1)
        try assembler.accept(descriptor: other)
        _ = try assembler.accept(binary: Data([2]))
        assembler.reset(responseID: "r-3")
        try assembler.accept(descriptor: first)
    }

    func testPlaybackQueuePreservesOrderAndCancelsOnlyMatchingResponse() {
        func segment(_ responseID: String, _ index: Int) -> AudioSegment {
            AudioSegment(
                descriptor: AudioChunkDescriptor(responseID: responseID, index: index, byteLength: 1),
                data: Data([UInt8(index)])
            )
        }
        var queue = AudioPlaybackQueue()
        queue.enqueue(segment("r-1", 0))
        queue.enqueue(segment("r-2", 1))
        queue.enqueue(segment("r-1", 2))
        queue.remove(responseID: "r-1")
        XCTAssertEqual(queue.count, 1)
        XCTAssertEqual(queue.dequeue(), segment("r-2", 1))
        XCTAssertTrue(queue.isEmpty)
    }

    func testInformationalEndpointEventsDecodeWithoutBreakingSession() throws {
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"input_audio.speech_started","barge_in":true,"probability":0.91}"#),
            .speechStarted(bargeIn: true, probability: 0.91)
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"input_audio.speech_stopped","silence_ms":768,"audio_ms":1344}"#),
            .speechStopped(silenceMS: 768, audioMS: 1_344)
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"input_audio.committed","reason":"silence","audio_ms":1504}"#),
            .inputCommitted(reason: "silence", audioMS: 1_504)
        )
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"input_audio.rejected","reason":"no_speech","message":"Nie wykryto mowy"}"#),
            .inputRejected(reason: "no_speech", message: "Nie wykryto mowy")
        )
    }

    func testDecodesDiagnosticTelemetry() throws {
        let decoded = try ProtocolCodec.decode(text: #"{"type":"input_audio.vad","probability":0.83,"rms_dbfs":-21.5,"peak":0.42,"zero_fraction":0.01,"state":"speech","speech_ms":320,"silence_ms":0,"buffered_ms":640,"processed_frames":20,"received_messages":24,"received_bytes":24576,"received_bytes_per_second":31800}"#)
        guard case .diagnostics(let patch) = decoded else {
            return XCTFail("expected diagnostic patch")
        }
        XCTAssertEqual(patch.vadProbability, 0.83)
        XCTAssertEqual(patch.rmsDBFS, -21.5)
        XCTAssertEqual(patch.vadState, "speech")
        XCTAssertEqual(patch.speechMS, 320)
        XCTAssertEqual(patch.receivedBytes, 24_576)
    }

    func testDecodesContextCompositionAndPromptMetrics() throws {
        let decoded = try ProtocolCodec.decode(text: #"{"type":"context.metrics","input_tokens":1200,"input_tokens_estimated":false,"output_tokens":80,"context_size":128000,"context_used_tokens":1200,"context_remaining_tokens":126800,"prompt_processing_tokens_per_second":750.5,"prompt_processing_estimated":true,"context_breakdown_estimated":true,"categories":{"system_prompt":120,"skills":180,"tools":300,"session":600}}"#)
        guard case .diagnostics(let patch) = decoded else {
            return XCTFail("expected context diagnostic patch")
        }
        XCTAssertEqual(patch.inputTokens, 1_200)
        XCTAssertEqual(patch.outputTokens, 80)
        XCTAssertEqual(patch.contextSize, 128_000)
        XCTAssertEqual(patch.contextRemainingTokens, 126_800)
        XCTAssertEqual(patch.promptProcessingTokensPerSecond, 750.5)
        XCTAssertEqual(patch.systemPromptTokens, 120)
        XCTAssertEqual(patch.skillsTokens, 180)
        XCTAssertEqual(patch.toolsTokens, 300)
        XCTAssertEqual(patch.sessionTokens, 600)
    }

    func testPCM16DiagnosticsAndWAVUseExactSamples() {
        let samples: [Int16] = [0, 16_384, -16_384, 0]
        let data = samples.withUnsafeBytes { Data($0) }
        let metrics = PCM16Diagnostics.analyze(data)
        XCTAssertEqual(metrics.sampleCount, 4)
        XCTAssertEqual(metrics.peak, 0.5, accuracy: 0.001)
        XCTAssertEqual(metrics.zeroFraction, 0.5, accuracy: 0.001)
        XCTAssertEqual(metrics.waveform.count, 4)

        let wav = PCM16WAVEncoder.encodeMono16K(data)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(String(data: wav.dropFirst(8).prefix(4), encoding: .ascii), "WAVE")
        XCTAssertEqual(wav.count, 44 + data.count)
        XCTAssertEqual(wav.suffix(data.count), data)
    }

    func testUnknownMessagesDoNotBreakSession() throws {
        XCTAssertEqual(
            try ProtocolCodec.decode(text: #"{"type":"future.event","value":42}"#),
            .ignored("future.event")
        )
    }
}
