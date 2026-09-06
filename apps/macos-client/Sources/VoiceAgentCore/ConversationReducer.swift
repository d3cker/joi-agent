import Foundation

public struct ConversationState: Equatable, Sendable {
    public var phase: ConversationPhase = .disconnected
    public var messages: [ChatMessage] = []
    public var partialTranscript = ""
    public var lastError: String?
    public var notice: String?
    public var cancellationCount = 0
    public var activeToolName: String?
    public var lastToolStatus: String?
    public var diagnostics = BackendDiagnostics()

    public init() {}

    public mutating func reduce(_ event: ServerEvent) {
        switch event {
        case .ready, .sessionReady:
            phase = .recording
            lastError = nil
            notice = nil
        case .sessionHistory(let history):
            messages = history.compactMap { item in
                guard let role = ChatMessage.Role(rawValue: item.role),
                      role == .user || role == .assistant else { return nil }
                return ChatMessage(role: role, text: item.text, isStreaming: false)
            }
            partialTranscript = ""
        case .state(let state):
            phase = Self.phase(from: state) ?? phase
        case .transcriptPartial(let text):
            partialTranscript = text
        case .transcriptFinal(let text):
            partialTranscript = ""
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
            messages.append(ChatMessage(role: .user, text: text))
            diagnostics.transcript = text
            diagnostics.transcriptAccepted = true
            phase = .thinking
        case .reasoningDelta:
            // Intentionally discarded: only assistant content is displayed/spoken.
            break
        case .assistantDelta(let delta):
            appendAssistant(delta, streaming: true)
            if phase != .speaking { phase = .thinking }
        case .assistantDone(let finalText):
            if let finalText, !finalText.isEmpty,
               messages.last?.role != .assistant {
                messages.append(ChatMessage(role: .assistant, text: finalText))
            }
            if messages.last?.role == .assistant { messages[messages.count - 1].isStreaming = false }
        case .audioStart:
            phase = .speaking
        case .audioChunkDescriptor, .audioSegment:
            break
        case .audioEnd:
            phase = .recording
        case .responseCancelled:
            cancellationCount += 1
            if messages.last?.role == .assistant { messages[messages.count - 1].isStreaming = false }
            phase = .recording
        case .speechStarted(_, let probability):
            diagnostics.vadState = "speech"
            if let probability { diagnostics.vadProbability = probability }
            notice = nil
        case .speechStopped(let silenceMS, _):
            diagnostics.vadState = "waiting_for_silence"
            if let silenceMS { diagnostics.silenceMS = silenceMS }
        case .inputCommitted(let reason, let audioMS):
            diagnostics.commitReason = reason
            diagnostics.commitAudioMS = audioMS
            diagnostics.lastRejection = nil
            phase = .transcribing
        case .inputRejected(let reason, let message):
            diagnostics.lastRejection = reason
            diagnostics.transcriptAccepted = false
            notice = message
            phase = .recording
        case .diagnostics(let patch):
            diagnostics.merge(patch)
        case .toolStarted(let name, _, _):
            activeToolName = name
            lastToolStatus = nil
            phase = .thinking
        case .toolCompleted(let name, _):
            activeToolName = nil
            lastToolStatus = L10n.text("tool.completed", name)
        case .toolFailed(let name, _, let message):
            activeToolName = nil
            lastToolStatus = message.map {
                L10n.text("tool.failed_detail", name, $0)
            } ?? L10n.text("tool.failed", name)
        case .error(let message):
            lastError = message
            phase = .failed
        case .ignored:
            break
        }
    }

    public mutating func beginConnecting() {
        phase = .connecting
        lastError = nil
        notice = nil
    }

    public mutating func disconnect() {
        phase = .disconnected
        partialTranscript = ""
        activeToolName = nil
    }

    public mutating func bargeIn() -> Bool {
        guard phase == .speaking || phase == .thinking else { return false }
        cancellationCount += 1
        if messages.last?.role == .assistant { messages[messages.count - 1].isStreaming = false }
        phase = .recording
        return true
    }

    private mutating func appendAssistant(_ delta: String, streaming: Bool) {
        guard !delta.isEmpty else { return }
        if messages.last?.role == .assistant, messages.last?.isStreaming == true {
            messages[messages.count - 1].text += delta
        } else {
            messages.append(ChatMessage(role: .assistant, text: delta, isStreaming: streaming))
        }
    }

    private static func phase(from raw: String) -> ConversationPhase? {
        switch raw.lowercased() {
        case "ready", "listening", "recording": return .recording
        case "transcribing": return .transcribing
        case "thinking", "processing": return .thinking
        case "speaking", "playing": return .speaking
        case "disconnected", "closed": return .disconnected
        case "error", "failed": return .failed
        default: return nil
        }
    }
}
