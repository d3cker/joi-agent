import Foundation

public enum ReasoningLevel: String, CaseIterable, Codable, Identifiable, Sendable {
    case disabled
    case low
    case high
    case maximum

    public var id: Self { self }

    public var apiValue: String {
        switch self {
        case .disabled: return "none"
        case .low: return "low"
        case .high: return "high"
        case .maximum: return "max"
        }
    }

    public var displayName: String {
        switch self {
        case .disabled: return L10n.text("reasoning.disabled")
        case .low: return L10n.text("reasoning.low")
        case .high: return L10n.text("reasoning.high")
        case .maximum: return L10n.text("reasoning.maximum")
        }
    }
}

public enum ConversationPhase: String, Codable, Sendable {
    case disconnected
    case connecting
    case recording
    case transcribing
    case thinking
    case speaking
    case failed

    public var displayName: String {
        switch self {
        case .disconnected: return L10n.text("phase.disconnected")
        case .connecting: return L10n.text("phase.connecting")
        case .recording: return L10n.text("phase.recording")
        case .transcribing: return L10n.text("phase.transcribing")
        case .thinking: return L10n.text("phase.thinking")
        case .speaking: return L10n.text("phase.speaking")
        case .failed: return L10n.text("phase.failed")
        }
    }
}

public struct ChatMessage: Identifiable, Equatable, Sendable {
    public enum Role: String, Sendable {
        case user
        case assistant
        case system
    }

    public let id: UUID
    public let role: Role
    public var text: String
    public var isStreaming: Bool

    public init(id: UUID = UUID(), role: Role, text: String, isStreaming: Bool = false) {
        self.id = id
        self.role = role
        self.text = text
        self.isStreaming = isStreaming
    }
}

public struct SessionHistoryItem: Equatable, Sendable {
    public let role: String
    public let text: String

    public init(role: String, text: String) {
        self.role = role
        self.text = text
    }
}

public struct SessionSummary: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let title: String?
    public let createdAt: String
    public let updatedAt: String
    public let reasoningEffort: String
    public let messageCount: Int
    public let lastMessage: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case reasoningEffort = "reasoning_effort"
        case messageCount = "message_count"
        case lastMessage = "last_message"
    }

    public init(
        id: String,
        title: String?,
        createdAt: String,
        updatedAt: String,
        reasoningEffort: String,
        messageCount: Int,
        lastMessage: String?
    ) {
        self.id = id
        self.title = title
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.reasoningEffort = reasoningEffort
        self.messageCount = messageCount
        self.lastMessage = lastMessage
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt) ?? createdAt
        reasoningEffort = try container.decodeIfPresent(
            String.self, forKey: .reasoningEffort
        ) ?? "none"
        messageCount = try container.decodeIfPresent(Int.self, forKey: .messageCount) ?? 0
        lastMessage = try container.decodeIfPresent(String.self, forKey: .lastMessage)
    }
}

public struct ToolActivityRecord: Codable, Equatable, Identifiable, Sendable {
    public var id: String { callID }
    public let callID: String
    public let responseID: String
    public let name: String
    public let arguments: String
    public let step: Int
    public let status: String
    public let startedAt: String
    public let finishedAt: String?
    public let error: String?

    enum CodingKeys: String, CodingKey {
        case name, arguments, step, status, error
        case callID = "call_id"
        case responseID = "response_id"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }

    public init(
        callID: String,
        responseID: String,
        name: String,
        arguments: String,
        step: Int,
        status: String,
        startedAt: String,
        finishedAt: String?,
        error: String?
    ) {
        self.callID = callID
        self.responseID = responseID
        self.name = name
        self.arguments = arguments
        self.step = step
        self.status = status
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.error = error
    }
}

public struct SessionDetail: Codable, Equatable, Sendable {
    public let id: String
    public let title: String?
    public let createdAt: String
    public let updatedAt: String
    public let reasoningEffort: String
    public let messages: [SessionHistoryItemDTO]
    public let toolActivity: [ToolActivityRecord]

    enum CodingKeys: String, CodingKey {
        case id, title, messages
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case reasoningEffort = "reasoning_effort"
        case toolActivity = "tool_activity"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        createdAt = try container.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt) ?? createdAt
        reasoningEffort = try container.decodeIfPresent(
            String.self, forKey: .reasoningEffort
        ) ?? "none"
        messages = try container.decodeIfPresent(
            [SessionHistoryItemDTO].self, forKey: .messages
        ) ?? []
        toolActivity = try container.decodeIfPresent(
            [ToolActivityRecord].self, forKey: .toolActivity
        ) ?? []
    }
}

public struct SessionHistoryItemDTO: Codable, Equatable, Sendable {
    public let role: String
    public let text: String

    public init(role: String, text: String) {
        self.role = role
        self.text = text
    }

    public var historyItem: SessionHistoryItem {
        SessionHistoryItem(role: role, text: text)
    }
}

public enum SessionEndpointError: Error, Equatable {
    case invalidWebSocketURL
}

public enum SessionEndpoint {
    public static func baseURL(from webSocketEndpoint: String) throws -> URL {
        guard var parts = URLComponents(string: webSocketEndpoint),
              let scheme = parts.scheme?.lowercased(),
              scheme == "ws" || scheme == "wss",
              parts.host != nil else {
            throw SessionEndpointError.invalidWebSocketURL
        }
        parts.scheme = scheme == "wss" ? "https" : "http"
        let path = parts.path
        if path == "/ws" || path.hasSuffix("/ws") {
            parts.path = String(path.dropLast(3))
        }
        parts.query = nil
        parts.fragment = nil
        guard let url = parts.url else { throw SessionEndpointError.invalidWebSocketURL }
        return url
    }
}

public enum ClientCommand: Equatable, Sendable {
    case configure(reasoningEffort: String, conversationLanguage: String)
    case commitAudio
    case cancelResponse
    case outputAudioPlaybackDone(responseID: String)

    public func encoded() throws -> Data {
        var object: [String: Any]
        switch self {
        case .configure(let effort, let language):
            object = [
                "type": "session.configure",
                "reasoning_effort": effort,
                "conversation_language": language
            ]
        case .commitAudio:
            object = ["type": "input_audio.commit"]
        case .cancelResponse:
            object = ["type": "response.cancel"]
        case .outputAudioPlaybackDone(let responseID):
            object = ["type": "output_audio.playback.done", "response_id": responseID]
        }
        return try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }
}

public struct AudioChunkDescriptor: Equatable, Sendable {
    public let responseID: String
    public let index: Int
    public let byteLength: Int
    public let mimeType: String?
    public let sampleRate: Double?
    public let text: String?

    public init(
        responseID: String,
        index: Int,
        byteLength: Int,
        mimeType: String? = nil,
        sampleRate: Double? = nil,
        text: String? = nil
    ) {
        self.responseID = responseID
        self.index = index
        self.byteLength = byteLength
        self.mimeType = mimeType
        self.sampleRate = sampleRate
        self.text = text
    }
}

public struct AudioSegment: Equatable, Sendable {
    public let descriptor: AudioChunkDescriptor
    public let data: Data

    public init(descriptor: AudioChunkDescriptor, data: Data) {
        self.descriptor = descriptor
        self.data = data
    }
}

public enum AudioBinaryAssemblyError: Error, Equatable {
    case descriptorAlreadyPending
    case binaryWithoutDescriptor
    case byteLengthMismatch(expected: Int, actual: Int)
    case indexOutOfSequence(responseID: String, expected: Int, actual: Int)
}

/// Associates each binary WebSocket frame with its preceding JSON descriptor.
public struct AudioBinaryAssembler: Sendable {
    public private(set) var pendingDescriptor: AudioChunkDescriptor?
    private var nextIndexByResponse: [String: Int] = [:]

    public init() {}

    public mutating func accept(descriptor: AudioChunkDescriptor) throws {
        guard pendingDescriptor == nil else {
            throw AudioBinaryAssemblyError.descriptorAlreadyPending
        }
        let expected = nextIndexByResponse[descriptor.responseID] ?? 0
        guard descriptor.index == expected else {
            throw AudioBinaryAssemblyError.indexOutOfSequence(
                responseID: descriptor.responseID,
                expected: expected,
                actual: descriptor.index
            )
        }
        pendingDescriptor = descriptor
    }

    public mutating func accept(binary: Data) throws -> AudioSegment {
        guard let descriptor = pendingDescriptor else {
            throw AudioBinaryAssemblyError.binaryWithoutDescriptor
        }
        pendingDescriptor = nil
        guard descriptor.byteLength == binary.count else {
            throw AudioBinaryAssemblyError.byteLengthMismatch(
                expected: descriptor.byteLength,
                actual: binary.count
            )
        }
        nextIndexByResponse[descriptor.responseID] = descriptor.index + 1
        return AudioSegment(descriptor: descriptor, data: binary)
    }

    public mutating func reset() {
        pendingDescriptor = nil
        nextIndexByResponse.removeAll(keepingCapacity: true)
    }

    public mutating func reset(responseID: String) {
        if pendingDescriptor?.responseID == responseID {
            pendingDescriptor = nil
        }
        nextIndexByResponse.removeValue(forKey: responseID)
    }
}

/// Small value-type queue so ordering and response-scoped cancellation are testable.
public struct AudioPlaybackQueue: Sendable {
    private var segments: [AudioSegment] = []

    public init() {}
    public var isEmpty: Bool { segments.isEmpty }
    public var count: Int { segments.count }

    public mutating func enqueue(_ segment: AudioSegment) {
        segments.append(segment)
    }

    public mutating func dequeue() -> AudioSegment? {
        guard !segments.isEmpty else { return nil }
        return segments.removeFirst()
    }

    public func contains(responseID: String) -> Bool {
        segments.contains { $0.descriptor.responseID == responseID }
    }

    public mutating func remove(responseID: String) {
        segments.removeAll { $0.descriptor.responseID == responseID }
    }

    public mutating func removeAll() {
        segments.removeAll(keepingCapacity: true)
    }
}

public struct BackendDiagnosticsPatch: Equatable, Sendable {
    public var vadProbability: Double?
    public var rmsDBFS: Double?
    public var peak: Double?
    public var zeroFraction: Double?
    public var vadState: String?
    public var speechMS: Int?
    public var silenceMS: Int?
    public var bufferedMS: Int?
    public var processedFrames: Int?
    public var receivedMessages: Int?
    public var receivedBytes: Int?
    public var receivedBytesPerSecond: Double?
    public var lastMessageBytes: Int?
    public var commitReason: String?
    public var commitAudioMS: Int?
    public var transcript: String?
    public var transcriptAccepted: Bool?
    public var confidence: Double?
    public var noSpeechProbability: Double?
    public var averageLogProbability: Double?
    public var compressionRatio: Double?
    public var sttMS: Int?
    public var endpointToSTTMS: Int?
    public var llmTTFTMS: Int?
    public var endpointToFirstTokenMS: Int?
    public var ttsMS: Int?
    public var endpointToFirstAudioMS: Int?
    public var firstTokenToFirstAudioMS: Int?
    public var tokensPerSecond: Double?
    public var tokensEstimated: Bool?
    public var estimatedTokens: Int?
    public var inputTokens: Int?
    public var inputTokensEstimated: Bool?
    public var outputTokens: Int?
    public var contextSize: Int?
    public var contextUsedTokens: Int?
    public var contextRemainingTokens: Int?
    public var promptProcessingTokensPerSecond: Double?
    public var promptProcessingEstimated: Bool?
    public var systemPromptTokens: Int?
    public var skillsTokens: Int?
    public var toolsTokens: Int?
    public var sessionTokens: Int?
    public var contextBreakdownEstimated: Bool?
    public var lastRejection: String?

    public init() {}
}

public struct BackendDiagnostics: Equatable, Sendable {
    public var vadProbability: Double = 0
    public var rmsDBFS: Double = -96
    public var peak: Double = 0
    public var zeroFraction: Double = 1
    public var vadState: String = "silence"
    public var speechMS: Int = 0
    public var silenceMS: Int = 0
    public var bufferedMS: Int = 0
    public var processedFrames: Int = 0
    public var receivedMessages: Int = 0
    public var receivedBytes: Int = 0
    public var receivedBytesPerSecond: Double = 0
    public var lastMessageBytes: Int = 0
    public var commitReason: String?
    public var commitAudioMS: Int?
    public var transcript: String = ""
    public var transcriptAccepted: Bool?
    public var confidence: Double?
    public var noSpeechProbability: Double?
    public var averageLogProbability: Double?
    public var compressionRatio: Double?
    public var sttMS: Int?
    public var endpointToSTTMS: Int?
    public var llmTTFTMS: Int?
    public var endpointToFirstTokenMS: Int?
    public var ttsMS: Int?
    public var endpointToFirstAudioMS: Int?
    public var firstTokenToFirstAudioMS: Int?
    public var tokensPerSecond: Double?
    public var tokensEstimated = true
    public var estimatedTokens: Int?
    public var inputTokens: Int = 0
    public var inputTokensEstimated = true
    public var outputTokens: Int = 0
    public var contextSize: Int = 128_000
    public var contextUsedTokens: Int = 0
    public var contextRemainingTokens: Int = 128_000
    public var promptProcessingTokensPerSecond: Double?
    public var promptProcessingEstimated = true
    public var systemPromptTokens: Int = 0
    public var skillsTokens: Int = 0
    public var toolsTokens: Int = 0
    public var sessionTokens: Int = 0
    public var contextBreakdownEstimated = true
    public var lastRejection: String?

    public init() {}

    public mutating func merge(_ patch: BackendDiagnosticsPatch) {
        if let value = patch.vadProbability { vadProbability = value }
        if let value = patch.rmsDBFS { rmsDBFS = value }
        if let value = patch.peak { peak = value }
        if let value = patch.zeroFraction { zeroFraction = value }
        if let value = patch.vadState { vadState = value }
        if let value = patch.speechMS { speechMS = value }
        if let value = patch.silenceMS { silenceMS = value }
        if let value = patch.bufferedMS { bufferedMS = value }
        if let value = patch.processedFrames { processedFrames = value }
        if let value = patch.receivedMessages { receivedMessages = value }
        if let value = patch.receivedBytes { receivedBytes = value }
        if let value = patch.receivedBytesPerSecond { receivedBytesPerSecond = value }
        if let value = patch.lastMessageBytes { lastMessageBytes = value }
        if let value = patch.commitReason { commitReason = value }
        if let value = patch.commitAudioMS { commitAudioMS = value }
        if let value = patch.transcript { transcript = value }
        if let value = patch.transcriptAccepted { transcriptAccepted = value }
        if let value = patch.confidence { confidence = value }
        if let value = patch.noSpeechProbability { noSpeechProbability = value }
        if let value = patch.averageLogProbability { averageLogProbability = value }
        if let value = patch.compressionRatio { compressionRatio = value }
        if let value = patch.sttMS { sttMS = value }
        if let value = patch.endpointToSTTMS { endpointToSTTMS = value }
        if let value = patch.llmTTFTMS { llmTTFTMS = value }
        if let value = patch.endpointToFirstTokenMS { endpointToFirstTokenMS = value }
        if let value = patch.ttsMS { ttsMS = value }
        if let value = patch.endpointToFirstAudioMS { endpointToFirstAudioMS = value }
        if let value = patch.firstTokenToFirstAudioMS { firstTokenToFirstAudioMS = value }
        if let value = patch.tokensPerSecond { tokensPerSecond = value }
        if let value = patch.tokensEstimated { tokensEstimated = value }
        if let value = patch.estimatedTokens { estimatedTokens = value }
        if let value = patch.inputTokens { inputTokens = value }
        if let value = patch.inputTokensEstimated { inputTokensEstimated = value }
        if let value = patch.outputTokens { outputTokens = value }
        if let value = patch.contextSize { contextSize = value }
        if let value = patch.contextUsedTokens { contextUsedTokens = value }
        if let value = patch.contextRemainingTokens { contextRemainingTokens = value }
        if let value = patch.promptProcessingTokensPerSecond {
            promptProcessingTokensPerSecond = value
        }
        if let value = patch.promptProcessingEstimated { promptProcessingEstimated = value }
        if let value = patch.systemPromptTokens { systemPromptTokens = value }
        if let value = patch.skillsTokens { skillsTokens = value }
        if let value = patch.toolsTokens { toolsTokens = value }
        if let value = patch.sessionTokens { sessionTokens = value }
        if let value = patch.contextBreakdownEstimated {
            contextBreakdownEstimated = value
        }
        if let value = patch.lastRejection { lastRejection = value }
    }
}

public enum ServerEvent: Equatable, Sendable {
    case ready
    case sessionReady(sessionID: String?, resumed: Bool)
    case sessionHistory([SessionHistoryItem])
    case state(String)
    case transcriptPartial(String)
    case transcriptFinal(String)
    case reasoningDelta(String)
    case assistantDelta(String)
    case assistantDone(String?)
    case audioStart(responseID: String, sampleRate: Double?, channels: Int?, encoding: String?)
    case audioChunkDescriptor(AudioChunkDescriptor)
    case audioSegment(AudioSegment)
    case audioEnd(responseID: String, cancelled: Bool)
    case responseCancelled(responseID: String?)
    case speechStarted(bargeIn: Bool, probability: Double?)
    case speechStopped(silenceMS: Int?, audioMS: Int?)
    case inputCommitted(reason: String?, audioMS: Int?)
    case inputRejected(reason: String, message: String)
    case diagnostics(BackendDiagnosticsPatch)
    case toolStarted(name: String, callID: String?, arguments: String)
    case toolCompleted(name: String, callID: String?)
    case toolFailed(name: String, callID: String?, message: String?)
    case error(String)
    case ignored(String)
}
