import AppKit
import Combine
import Foundation
import VoiceAgentCore

@MainActor
final class ConversationStore: ObservableObject {
    @Published private(set) var state = ConversationState()
    @Published var reasoningEffort: String = "low" {
        didSet {
            persistClientSettings()
            sendSessionConfigurationIfRunning()
        }
    }
    @Published private(set) var availableReasoningEfforts = ["none", "low", "high", "max"]
    @Published private(set) var availableInterfaceLanguages: [InterfaceLanguageDescriptor] = []
    @Published var endpoint: String {
        didSet { persistClientSettings() }
    }
    @Published var clientAccessKey = "" {
        didSet { try? ConfigKeychain.saveClientAccessKey(clientAccessKey) }
    }
    @Published var interfaceLanguage: String {
        didSet {
            guard availableInterfaceLanguages.contains(where: { $0.id == interfaceLanguage }) else {
                interfaceLanguage = oldValue
                return
            }
            L10n.configure(language: interfaceLanguage)
            persistClientSettings()
            sendSessionConfigurationIfRunning()
        }
    }
    @Published var contextPanelVisible: Bool {
        didSet { persistClientSettings() }
    }
    @Published private(set) var isRunning = false
    @Published private(set) var sessions: [SessionSummary] = []
    @Published private(set) var toolActivity: [ToolActivityRecord] = []
    @Published private(set) var sessionOperationInProgress = false
    @Published private(set) var sessionError: String?
    @Published var useVoiceProcessing = true {
        didSet {
            audio.setVoiceProcessingRequested(useVoiceProcessing)
            persistClientSettings()
        }
    }

    let audio = AudioController()
    let socket = WebSocketClient()
    private let clientSettingsStore: ClientSettingsStore
    private var clientSettings: ClientSettingsDocument
    private var sessionID: String
    private var activeResponseID: String?
    private var serverAudioEnded: Set<String> = []
    private var locallyDrained: Set<String> = []
    private var completedPlayback: Set<String> = []
    private var cancelledResponses: Set<String> = []
    private var didRunCommandLineDiagnostic = false

    var currentSessionID: String { sessionID }
    var currentSession: SessionSummary? { sessions.first { $0.id == sessionID } }
    var currentSessionTitle: String {
        let title = currentSession?.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        return (title?.isEmpty == false ? title : nil) ?? text("session.new")
    }

    init(
        defaults: UserDefaults = .standard,
        clientSettingsStore: ClientSettingsStore = ClientSettingsStore()
    ) {
        self.clientSettingsStore = clientSettingsStore
        let localizationRoot = Bundle.main.resourceURL?
            .appendingPathComponent("Localization", isDirectory: true)
        let manifest = localizationRoot.flatMap { try? LocalizationManifest.load(resourceRoot: $0) }
        let languageChoices = manifest?.languages ?? [
            InterfaceLanguageDescriptor(id: "en", name: "English")
        ]
        let loaded = (try? clientSettingsStore.loadOrMigrate(defaults: defaults))
            ?? ClientSettingsDocument(
                language: "en",
                webSocketEndpoint: "ws://127.0.0.1:8765/ws",
                reasoningLevel: "low",
                sessionID: UUID().uuidString.lowercased(),
                useVoiceProcessing: true,
                contextPanelVisible: false
            )
        var normalized = loaded
        if !languageChoices.contains(where: { $0.id == normalized.language }) {
            normalized.language = manifest?.defaultLanguage ?? languageChoices[0].id
        }
        clientSettings = normalized
        sessionID = loaded.sessionID
        endpoint = loaded.webSocketEndpoint
        clientAccessKey = ConfigKeychain.loadClientAccessKey()
        availableInterfaceLanguages = languageChoices
        interfaceLanguage = normalized.language
        contextPanelVisible = loaded.contextPanelVisible
        reasoningEffort = Self.normalizedReasoningEffort(loaded.reasoningLevel)
        useVoiceProcessing = loaded.useVoiceProcessing
        L10n.configure(language: normalized.language)
        socket.onEvent = { [weak self] in self?.receive($0) }
        audio.onInputAudio = { [weak self] data in
            Task { @MainActor in self?.socket.send(audio: data) }
        }
        audio.onPlaybackDrained = { [weak self] responseID in
            self?.playbackDrained(responseID: responseID)
        }
    }

    var statusDetail: String? {
        if let error = state.lastError { return error }
        if let notice = state.notice { return notice }
        if let tool = state.activeToolName { return text("status.active_tool", tool) }
        switch state.phase {
        case .recording: return text("status.recording_detail")
        case .transcribing: return text("status.transcribing_detail")
        case .thinking: return text("status.thinking_detail")
        case .speaking: return text("status.speaking_detail")
        default: return nil
        }
    }

    var diagnosticStage: String {
        if audio.diagnostics.diagnosticRecording { return text("status.local_microphone_test") }
        if audio.isPlaying { return text("status.playback") }
        switch state.phase {
        case .connecting: return text("status.websocket_connecting")
        case .transcribing: return text("status.stt")
        case .thinking: return text("status.llm")
        case .speaking: return text("status.audio_wait")
        case .failed: return text("status.error")
        case .disconnected: return text("status.disconnected")
        case .recording:
            switch state.diagnostics.vadState {
            case "speech", "candidate": return text("status.listening_speech")
            case "waiting_for_silence": return text("status.waiting_silence")
            default: return text("status.listening_silence")
            }
        }
    }

    var websocketStatusText: String {
        switch socket.status {
        case .disconnected: return text("status.websocket_disconnected")
        case .connecting: return text("status.websocket_connecting")
        case .connected: return text("status.websocket_connected")
        case .failed(let message): return text("status.error_detail", message)
        }
    }

    func text(_ key: String, _ arguments: CVarArg...) -> String {
        let template = L10n.text(key)
        guard !arguments.isEmpty else { return template }
        return String(
            format: template,
            locale: Locale(identifier: interfaceLanguage),
            arguments: arguments
        )
    }

    func reasoningDisplayName(_ effort: String) -> String {
        switch effort {
        case "none": return text("reasoning.disabled")
        case "low": return text("reasoning.low")
        case "high": return text("reasoning.high")
        case "max": return text("reasoning.maximum")
        default: return effort
        }
    }

    func applyBackendConfiguration(_ snapshot: BackendConfigSnapshot) {
        guard let profile = snapshot.activeModelProfile else { return }
        let levels = profile["reasoning_levels"]?.stringArrayValue ?? []
        if !levels.isEmpty { availableReasoningEfforts = levels }
        if !availableReasoningEfforts.contains(reasoningEffort) {
            reasoningEffort = profile["default_reasoning"]?.stringValue
                ?? availableReasoningEfforts[0]
        }
        if let contextSize = profile["context_size"]?.integerValue {
            state.diagnostics.contextSize = contextSize
            state.diagnostics.contextRemainingTokens = max(
                0, contextSize - state.diagnostics.contextUsedTokens
            )
        }
    }

    func startDiagnosticSample() { audio.startDiagnosticSample(seconds: 5) }
    func finishDiagnosticSample() { audio.finishDiagnosticSample() }
    func playDiagnosticSample() { audio.playDiagnosticSample() }

    func runCommandLineDiagnosticIfRequested() async {
        guard !didRunCommandLineDiagnostic else { return }
        let arguments = ProcessInfo.processInfo.arguments
        guard let flag = arguments.firstIndex(of: "--local-mic-smoke") else { return }
        didRunCommandLineDiagnostic = true
        let basePath = arguments.indices.contains(flag + 1)
            ? arguments[flag + 1]
            : "/private/tmp/voiceagent-mic-smoke"
        let reportURL = URL(fileURLWithPath: basePath + ".json")
        let wavURL = URL(fileURLWithPath: basePath + ".wav")
        let report = await audio.runMicrophoneSmoke(seconds: 5, wavURL: wavURL)
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            try encoder.encode(report).write(to: reportURL, options: .atomic)
        } catch {
            FileHandle.standardError.write(
                Data("\(text("error.audio.save_sample", String(describing: error)))\n".utf8)
            )
        }
        NSApplication.shared.terminate(nil)
    }

    func copyDiagnostics() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(diagnosticsText, forType: .string)
    }

    func openInputSettings() {
        let soundInput = URL(
            string: "x-apple.systempreferences:com.apple.Sound-Settings.extension?input"
        )!
        if !NSWorkspace.shared.open(soundInput),
           let generalSound = URL(string: "x-apple.systempreferences:com.apple.preference.sound") {
            NSWorkspace.shared.open(generalSound)
        }
    }

    func openMicrophonePrivacySettings() {
        guard let privacy = URL(
            string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        ) else { return }
        NSWorkspace.shared.open(privacy)
    }

    var diagnosticsText: String {
        let client = audio.diagnostics
        let server = state.diagnostics
        return """
        Local Voice Agent diagnostics 0.1.20
        conversation_language=\(interfaceLanguage)
        stage=\(diagnosticStage)
        websocket=\(websocketStatusText)
        input_device=\(client.activeDeviceName) uid=\(client.activeDeviceUID) system_default=\(client.activeDeviceIsDefault) available=\(client.availableInputDevices)
        client_source=\(client.sourceSampleRate)Hz channels=\(client.sourceChannels) format=\(client.sourceFormat) interleaved=\(client.sourceInterleaved)
        voice_processing=\(client.voiceProcessingEnabled) muted=\(client.voiceProcessingMuted)
        client_rms_dbfs=\(String(format: "%.1f", client.rmsDBFS)) peak=\(String(format: "%.4f", client.peak)) zero_fraction=\(String(format: "%.3f", client.zeroFraction))
        input_callbacks=\(client.inputCallbacks) rate=\(String(format: "%.1f", client.inputCallbacksPerSecond))/s converted_frames=\(client.convertedFrames) conversion_failures=\(client.conversionFailures)
        sent_frames=\(client.sentFrames) rate=\(String(format: "%.1f", client.sentFramesPerSecond))/s sent_bytes=\(client.sentBytes) last_frame_bytes=\(client.lastFrameBytes) discontinuities=\(client.discontinuities)
        selected_channel=\(client.selectedChannel.map(String.init) ?? "brak") channel_levels=\(client.channelLevels.map { "ch\($0.index):\(String(format: "%.1f", $0.rmsDBFS))dBFS/peak\(String(format: "%.4f", $0.peak))/zero\(String(format: "%.1f", $0.zeroFraction * 100))%" }.joined(separator: ","))
        server_vad=\(String(format: "%.3f", server.vadProbability)) state=\(server.vadState) speech_ms=\(server.speechMS) silence_ms=\(server.silenceMS) buffered_ms=\(server.bufferedMS)
        server_frames=\(server.processedFrames) messages=\(server.receivedMessages) bytes=\(server.receivedBytes) bytes_per_second=\(server.receivedBytesPerSecond)
        commit=\(server.commitReason ?? "brak") audio_ms=\(server.commitAudioMS.map(String.init) ?? "brak") rejection=\(server.lastRejection ?? "brak")
        transcript=\(server.transcript) accepted=\(server.transcriptAccepted.map(String.init) ?? "brak") confidence=\(server.confidence.map { String(format: "%.3f", $0) } ?? "brak") no_speech=\(server.noSpeechProbability.map { String(format: "%.3f", $0) } ?? "brak") avg_logprob=\(server.averageLogProbability.map { String(format: "%.3f", $0) } ?? "brak") compression=\(server.compressionRatio.map { String(format: "%.3f", $0) } ?? "brak")
        stt_ms=\(server.sttMS.map(String.init) ?? "brak") ttft_ms=\(server.llmTTFTMS.map(String.init) ?? "brak") first_audio_ms=\(server.endpointToFirstAudioMS.map(String.init) ?? "brak") tokens_per_second=\(server.tokensPerSecond.map { String(format: "%.2f", $0) } ?? "brak") tokens_estimated=\(server.tokensEstimated)
        playback_queue=\(audio.playbackQueueDepth) current_segment=\(audio.currentSegmentIndex.map(String.init) ?? "brak") played_segments=\(audio.playedSegments)
        capture_warning=\(client.captureWarning ?? "brak")
        last_error=\(state.lastError ?? "brak")
        diagnostic_file=\(client.lastDiagnosticFile ?? "brak")
        """
    }

    func toggle() {
        isRunning ? stop() : start()
    }

    func loadSessionManagement() async {
        // Session REST metadata is optional. It must never interrupt the voice
        // WebSocket path with a modal error during launch or reconnection.
        await refreshSessions(loadCurrentDetail: true, reportErrors: false)
    }

    func retrySessionManagement() async {
        sessionError = nil
        await refreshSessions(loadCurrentDetail: true, reportErrors: true)
    }

    func createNewSession() async {
        guard !sessionOperationInProgress else { return }
        sessionOperationInProgress = true
        sessionError = nil
        let resumeCapture = isRunning
        if resumeCapture { stop() }
        do {
            let identifier = try await sessionAPI.create()
            selectLocally(identifier)
            try await loadDetail(identifier)
            sessions = try await sessionAPI.list()
            if resumeCapture { start() }
        } catch {
            sessionError = error.localizedDescription
            if resumeCapture { start() }
        }
        sessionOperationInProgress = false
    }

    func switchSession(to identifier: String) async {
        guard identifier != sessionID, !sessionOperationInProgress else { return }
        sessionOperationInProgress = true
        sessionError = nil
        let resumeCapture = isRunning
        if resumeCapture { stop() }
        selectLocally(identifier)
        do {
            try await loadDetail(identifier)
            if resumeCapture { start() }
        } catch {
            sessionError = error.localizedDescription
        }
        sessionOperationInProgress = false
    }

    func renameCurrentSession(to proposedTitle: String) async {
        let title = proposedTitle.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !title.isEmpty, title.count <= 120, !sessionOperationInProgress else { return }
        sessionOperationInProgress = true
        sessionError = nil
        do {
            try await sessionAPI.rename(id: sessionID, title: title)
            sessions = try await sessionAPI.list()
        } catch {
            sessionError = error.localizedDescription
        }
        sessionOperationInProgress = false
    }

    func deleteCurrentSession() async {
        await deleteSession(id: sessionID)
    }

    func deleteSession(id deleting: String) async {
        guard !sessionOperationInProgress else { return }
        sessionOperationInProgress = true
        sessionError = nil
        let deletingCurrentSession = deleting == sessionID
        let resumeCapture = deletingCurrentSession && isRunning
        if resumeCapture {
            stop()
            try? await Task.sleep(nanoseconds: 300_000_000)
        }
        do {
            try await sessionAPI.delete(id: deleting)
            sessions = try await sessionAPI.list()
            if deletingCurrentSession {
                if let next = sessions.first {
                    selectLocally(next.id)
                    try await loadDetail(next.id)
                } else {
                    let identifier = try await sessionAPI.create()
                    selectLocally(identifier)
                    state = ConversationState()
                    toolActivity = []
                    sessions = try await sessionAPI.list()
                }
                if resumeCapture { start() }
            }
        } catch {
            sessionError = error.localizedDescription
            if resumeCapture { start() }
        }
        sessionOperationInProgress = false
    }

    func clearSessionError() { sessionError = nil }

    func start() {
        guard !isRunning else { return }
        guard TransportSecurityPolicy.allowsWebSocketEndpoint(endpoint),
              let url = URL(string: endpoint) else {
            receive(.error(text("error.websocket.valid_url")))
            return
        }
        guard !clientAccessKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            receive(.error(text("error.websocket.missing_client_key")))
            return
        }
        state.beginConnecting()
        isRunning = true
        guard let sessionURL = Self.url(
            url,
            withSessionID: sessionID,
            conversationLanguage: interfaceLanguage
        ) else {
            receive(.error(text("error.websocket.session_url")))
            isRunning = false
            return
        }
        socket.connect(to: sessionURL, clientAccessKey: clientAccessKey)
        socket.send(.configure(
            reasoningEffort: reasoningEffort,
            conversationLanguage: interfaceLanguage
        ))

        Task {
            do {
                try await audio.startCapture()
            } catch {
                receive(.error(error.localizedDescription))
                stop(keepError: true)
            }
        }
    }

    func stop() { stop(keepError: false) }

    func commitUtterance() {
        guard isRunning else { return }
        socket.send(.commitAudio)
    }

    private func stop(keepError: Bool) {
        audio.stopCapture()
        audio.stopPlayback()
        activeResponseID = nil
        serverAudioEnded.removeAll()
        locallyDrained.removeAll()
        completedPlayback.removeAll()
        cancelledResponses.removeAll()
        socket.disconnect()
        isRunning = false
        if keepError {
            state.phase = .failed
        } else {
            state.disconnect()
        }
    }

    private func receive(_ event: ServerEvent) {
        if case .sessionReady(let serverSessionID, _) = event,
           let serverSessionID, !serverSessionID.isEmpty {
            sessionID = serverSessionID
            persistClientSettings()
            Task { await refreshSessions(loadCurrentDetail: false) }
        }
        switch event {
        case .audioStart(let responseID, _, _, _):
            activeResponseID = responseID
            serverAudioEnded.remove(responseID)
            locallyDrained.remove(responseID)
            completedPlayback.remove(responseID)
            cancelledResponses.remove(responseID)
        case .audioSegment(let segment):
            activeResponseID = segment.descriptor.responseID
            audio.enqueue(segment)
        case .audioEnd(let responseID, let cancelled):
            if cancelled {
                cancelPlayback(responseID: responseID)
                return
            }
            serverAudioEnded.insert(responseID)
            if audio.hasPendingPlayback(responseID: responseID) { return }
            finishPlayback(responseID: responseID)
            return
        case .transcriptFinal:
            Task { await refreshSessions(loadCurrentDetail: false) }
        case .responseCancelled(let responseID):
            cancelPlayback(responseID: responseID ?? activeResponseID)
            return
        case .assistantDone:
            break
        case .toolStarted(let name, let callID, let arguments):
            let identifier = callID ?? UUID().uuidString
            toolActivity.insert(ToolActivityRecord(
                callID: identifier,
                responseID: activeResponseID ?? "active",
                name: name,
                arguments: arguments,
                step: 0,
                status: "running",
                startedAt: ISO8601DateFormatter().string(from: Date()),
                finishedAt: nil,
                error: nil
            ), at: 0)
        case .toolCompleted(let name, let callID):
            finishToolActivity(callID: callID, name: name, status: "completed", error: nil)
        case .toolFailed(let name, let callID, let message):
            finishToolActivity(callID: callID, name: name, status: "failed", error: message)
        case .state(let serverState):
            if ["ready", "listening", "recording"].contains(serverState.lowercased()),
               activeResponseID != nil {
                // The backend has finished producing bytes; speaking remains a
                // local playback state until the queue is actually drained.
                return
            }
        default:
            break
        }
        state.reduce(event)
    }

    private var sessionAPI: SessionAPIClient {
        SessionAPIClient(
            webSocketEndpoint: endpoint,
            clientAccessKey: clientAccessKey
        )
    }

    private func refreshSessions(loadCurrentDetail: Bool, reportErrors: Bool = false) async {
        do {
            sessions = try await sessionAPI.list()
            if loadCurrentDetail, sessions.contains(where: { $0.id == sessionID }) {
                try await loadDetail(sessionID)
            }
            sessionError = nil
        } catch {
            if reportErrors { sessionError = error.localizedDescription }
        }
    }

    private func loadDetail(_ identifier: String) async throws {
        let detail = try await sessionAPI.detail(id: identifier)
        guard identifier == sessionID else { return }
        state.reduce(.sessionHistory(detail.messages.map(\.historyItem)))
        toolActivity = detail.toolActivity
    }

    private func finishToolActivity(
        callID: String?, name: String, status: String, error: String?
    ) {
        let index = toolActivity.firstIndex { record in
            if let callID { return record.callID == callID }
            return record.name == name && record.status == "running"
        }
        guard let index else { return }
        let current = toolActivity[index]
        toolActivity[index] = ToolActivityRecord(
            callID: current.callID,
            responseID: current.responseID,
            name: current.name,
            arguments: current.arguments,
            step: current.step,
            status: status,
            startedAt: current.startedAt,
            finishedAt: ISO8601DateFormatter().string(from: Date()),
            error: error
        )
    }

    private func selectLocally(_ identifier: String) {
        audio.stopPlayback()
        activeResponseID = nil
        serverAudioEnded.removeAll()
        locallyDrained.removeAll()
        completedPlayback.removeAll()
        cancelledResponses.removeAll()
        sessionID = identifier
        persistClientSettings()
        state = ConversationState()
        toolActivity = []
    }

    private func persistClientSettings() {
        clientSettings.language = interfaceLanguage
        clientSettings.webSocketEndpoint = endpoint
        clientSettings.reasoningLevel = reasoningEffort
        clientSettings.sessionID = sessionID
        clientSettings.useVoiceProcessing = useVoiceProcessing
        clientSettings.contextPanelVisible = contextPanelVisible
        if let saved = try? clientSettingsStore.save(clientSettings) {
            clientSettings = saved
        }
    }

    private static func url(
        _ baseURL: URL,
        withSessionID sessionID: String,
        conversationLanguage: String
    ) -> URL? {
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            return nil
        }
        var items = components.queryItems ?? []
        items.removeAll {
            $0.name == "session_id" || $0.name == "conversation_language"
        }
        items.append(URLQueryItem(name: "session_id", value: sessionID))
        items.append(URLQueryItem(
            name: "conversation_language",
            value: conversationLanguage
        ))
        components.queryItems = items
        return components.url
    }

    private static func normalizedReasoningEffort(_ value: String) -> String {
        ReasoningLevel(rawValue: value)?.apiValue ?? value
    }

    private func sendSessionConfigurationIfRunning() {
        guard isRunning else { return }
        socket.send(.configure(
            reasoningEffort: reasoningEffort,
            conversationLanguage: interfaceLanguage
        ))
    }

    private func playbackDrained(responseID: String) {
        locallyDrained.insert(responseID)
        guard serverAudioEnded.contains(responseID) else { return }
        finishPlayback(responseID: responseID)
    }

    private func finishPlayback(responseID: String) {
        guard !completedPlayback.contains(responseID) else { return }
        guard serverAudioEnded.contains(responseID),
              locallyDrained.contains(responseID) || !audio.hasPendingPlayback(responseID: responseID) else {
            return
        }
        completedPlayback.insert(responseID)
        serverAudioEnded.remove(responseID)
        locallyDrained.remove(responseID)
        if activeResponseID == responseID { activeResponseID = nil }
        state.reduce(.audioEnd(responseID: responseID, cancelled: false))
        socket.send(.outputAudioPlaybackDone(responseID: responseID))
    }

    private func cancelPlayback(responseID: String?) {
        guard let responseID else {
            audio.stopPlayback()
            activeResponseID = nil
            state.reduce(.responseCancelled(responseID: nil))
            return
        }
        audio.stopPlayback(responseID: responseID)
        serverAudioEnded.remove(responseID)
        locallyDrained.remove(responseID)
        if activeResponseID == responseID { activeResponseID = nil }
        guard cancelledResponses.insert(responseID).inserted else { return }
        state.reduce(.responseCancelled(responseID: responseID))
    }
}
