import XCTest
@testable import VoiceAgentCore

final class ConversationReducerTests: XCTestCase {
    override func setUpWithError() throws {
        // XCTest is not the app bundle: initialize its catalog explicitly,
        // independently of which localization tests ran before this suite.
        let root = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Resources/Localization", isDirectory: true)
        XCTAssertTrue(L10n.configure(language: "en", resourceRoot: root))
    }

    func testMarkdownDisplayRendererRemovesControlsAndPreservesContent() {
        let rendered = MarkdownDisplayRenderer.render(
            "**Ważne** i *kursywa* z [odnośnikiem](https://example.com)."
        )
        XCTAssertEqual(
            String(rendered.characters),
            "Ważne i kursywa z odnośnikiem."
        )
        XCTAssertFalse(String(rendered.characters).contains("**"))
        XCTAssertTrue(rendered.runs.contains { run in
            run.inlinePresentationIntent?.contains(.stronglyEmphasized) == true
        })
        XCTAssertTrue(rendered.runs.contains { run in
            run.inlinePresentationIntent?.contains(.emphasized) == true
        })
    }

    func testAssistantDeltaDoesNotReplaceSpeakingPhaseDuringStreamingAudio() {
        var state = ConversationState()
        state.reduce(.audioStart(
            responseID: "response-1", sampleRate: 24_000,
            channels: 1, encoding: "wav"
        ))
        state.reduce(.assistantDelta("Kolejne zdanie."))
        XCTAssertEqual(state.phase, .speaking)
    }

    func testConversationLifecycle() {
        var state = ConversationState()
        state.beginConnecting()
        XCTAssertEqual(state.phase, .connecting)

        state.reduce(.ready)
        XCTAssertEqual(state.phase, .recording)
        state.reduce(.transcriptPartial("Cześć"))
        XCTAssertEqual(state.partialTranscript, "Cześć")
        state.reduce(.transcriptFinal("Cześć agencie"))
        XCTAssertEqual(state.phase, .thinking)
        XCTAssertEqual(state.messages.last?.role, .user)

        state.reduce(.assistantDelta("Witaj"))
        state.reduce(.assistantDelta("!"))
        XCTAssertEqual(state.messages.last?.text, "Witaj!")
        XCTAssertTrue(state.messages.last?.isStreaming == true)
        state.reduce(.assistantDone(nil))
        XCTAssertFalse(state.messages.last?.isStreaming == true)
        state.reduce(.audioStart(responseID: "r-1", sampleRate: 24_000, channels: 1, encoding: "wav"))
        XCTAssertEqual(state.phase, .speaking)
        state.reduce(.audioEnd(responseID: "r-1", cancelled: false))
        XCTAssertEqual(state.phase, .recording)
    }

    func testReasoningIsNeverAddedToConversation() {
        var state = ConversationState()
        state.reduce(.reasoningDelta("wewnętrzny tok rozumowania"))
        XCTAssertTrue(state.messages.isEmpty)
    }

    func testSessionHistoryReplacesTransientMessagesOnResume() {
        var state = ConversationState()
        state.reduce(.assistantDelta("stale partial"))
        state.reduce(.sessionHistory([
            SessionHistoryItem(role: "user", text: "Poprzednie pytanie"),
            SessionHistoryItem(role: "tool", text: "internal result"),
            SessionHistoryItem(role: "assistant", text: "Poprzednia odpowiedź")
        ]))
        XCTAssertEqual(state.messages.map(\.text), [
            "Poprzednie pytanie", "Poprzednia odpowiedź"
        ])
        XCTAssertTrue(state.messages.allSatisfy { !$0.isStreaming })
    }

    func testBargeInCancelsOnlyAnActiveResponse() {
        var state = ConversationState()
        state.reduce(.ready)
        XCTAssertFalse(state.bargeIn())
        XCTAssertEqual(state.cancellationCount, 0)

        state.reduce(.audioStart(responseID: "r-1", sampleRate: nil, channels: nil, encoding: nil))
        XCTAssertTrue(state.bargeIn())
        XCTAssertEqual(state.phase, .recording)
        XCTAssertEqual(state.cancellationCount, 1)
        XCTAssertFalse(state.bargeIn())
        XCTAssertEqual(state.cancellationCount, 1)
    }

    func testServerCancellationFinalizesStreamingMessage() {
        var state = ConversationState()
        state.reduce(.assistantDelta("Niedokończona odpowiedź"))
        state.reduce(.responseCancelled(responseID: "r-1"))
        XCTAssertEqual(state.phase, .recording)
        XCTAssertFalse(state.messages.last?.isStreaming == true)
        XCTAssertEqual(state.cancellationCount, 1)
    }

    func testToolLifecycleIsVisibleWithoutAddingToolOutputToChat() {
        var state = ConversationState()
        state.reduce(.toolStarted(name: "execute", callID: "call-1", arguments: #"{"command":"pwd"}"#))
        XCTAssertEqual(state.phase, .thinking)
        XCTAssertEqual(state.activeToolName, "execute")
        XCTAssertTrue(state.messages.isEmpty)

        state.reduce(.toolCompleted(name: "execute", callID: "call-1"))
        XCTAssertNil(state.activeToolName)
        XCTAssertEqual(state.lastToolStatus, "Completed: execute")

        state.reduce(.toolFailed(name: "grep", callID: "call-2", message: "unreachable"))
        XCTAssertEqual(state.lastToolStatus, "Failed: grep — unreachable")
        XCTAssertTrue(state.messages.isEmpty)
    }
}
