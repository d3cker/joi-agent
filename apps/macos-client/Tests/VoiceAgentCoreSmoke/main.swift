import Foundation
import VoiceAgentCore

private func require(_ condition: @autoclosure () -> Bool, _ message: String) {
    guard condition() else {
        FileHandle.standardError.write(Data("FAIL: \(message)\n".utf8))
        exit(1)
    }
}

let sessionRESTBase = try SessionEndpoint.baseURL(
    from: "ws://192.168.30.215:8765/ws"
)
require(
    sessionRESTBase.absoluteString == "http://192.168.30.215:8765",
    "session REST endpoint derivation failed"
)

let legacySessionJSON = #"{"id":"legacy","title":"Old","created_at":"2026-09-05T10:00:00.000+00:00","updated_at":"2026-09-05T10:01:00.000+00:00","reasoning_effort":"low","messages":[]}"#
let legacySession = try JSONDecoder().decode(
    SessionDetail.self, from: Data(legacySessionJSON.utf8)
)
require(legacySession.toolActivity.isEmpty, "legacy session compatibility")

// Client protocol and explicit reasoning level.
let configData = try ClientCommand.configure(
    reasoningEffort: ReasoningLevel.maximum.apiValue,
    conversationLanguage: "en"
).encoded()
let config = try JSONSerialization.jsonObject(with: configData) as! [String: String]
require(config["type"] == "session.configure", "configure command type")
require(config["reasoning_effort"] == "max", "explicit reasoning effort")
require(config["conversation_language"] == "en", "explicit conversation language")

let commitData = try ClientCommand.commitAudio.encoded()
let commit = try JSONSerialization.jsonObject(with: commitData) as! [String: String]
require(commit == ["type": "input_audio.commit"], "audio commit command")

let cancelData = try ClientCommand.cancelResponse.encoded()
let cancel = try JSONSerialization.jsonObject(with: cancelData) as! [String: String]
require(cancel == ["type": "response.cancel"], "response cancel command")

let playbackDoneData = try ClientCommand.outputAudioPlaybackDone(responseID: "r-1").encoded()
let playbackDone = try JSONSerialization.jsonObject(with: playbackDoneData) as! [String: String]
require(
    playbackDone == ["type": "output_audio.playback.done", "response_id": "r-1"],
    "playback done command"
)

let marker = try ProtocolCodec.decode(
    text: #"{"type":"audio.chunk","response_id":"r-1","index":0,"byte_length":3,"encoding":"binary-next-frame"}"#
)
let descriptor = AudioChunkDescriptor(responseID: "r-1", index: 0, byteLength: 3)
require(marker == .audioChunkDescriptor(descriptor), "binary WAV marker")
var assembler = AudioBinaryAssembler()
try assembler.accept(descriptor: descriptor)
let assembled = try assembler.accept(binary: Data([1, 2, 3]))
require(
    assembled == AudioSegment(
        descriptor: descriptor,
        data: Data([1, 2, 3])
    ),
    "binary WAV assembly"
)
do {
    try assembler.accept(descriptor: descriptor)
    require(false, "duplicate segment index must fail")
} catch AudioBinaryAssemblyError.indexOutOfSequence(let responseID, let expected, let actual) {
    require(responseID == "r-1" && expected == 1 && actual == 0, "duplicate index diagnostics")
}
let missingDescriptor = AudioChunkDescriptor(responseID: "r-1", index: 2, byteLength: 1)
do {
    try assembler.accept(descriptor: missingDescriptor)
    require(false, "missing segment index must fail")
} catch AudioBinaryAssemblyError.indexOutOfSequence(let responseID, let expected, let actual) {
    require(responseID == "r-1" && expected == 1 && actual == 2, "missing index diagnostics")
}

let reasoning = try ProtocolCodec.decode(
    text: #"{"type":"assistant.reasoning.delta","delta":"ukryte"}"#
)
let content = try ProtocolCodec.decode(
    text: #"{"type":"assistant.delta","text":"Witaj"}"#
)
require(reasoning == .reasoningDelta("ukryte"), "reasoning stream parsing")
require(content == .assistantDelta("Witaj"), "content stream parsing")

let renderedMarkdown = MarkdownDisplayRenderer.render(
    "**Ważne** i *kursywa* z [odnośnikiem](https://example.com)."
)
require(
    String(renderedMarkdown.characters) == "Ważne i kursywa z odnośnikiem.",
    "assistant Markdown visible rendering"
)
require(
    renderedMarkdown.runs.contains {
        $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true
    },
    "assistant Markdown strong emphasis"
)

let blockMarkdown = MarkdownDocumentParser.parse("""
| Model | Wynik |
|---|---|
| Higgs | działa |

Pierwsza linia
Druga linia
""")
require(blockMarkdown.blocks.count == 2, "Markdown block count")
guard case .table(let headers, let rows) = blockMarkdown.blocks[0] else {
    require(false, "Markdown table block")
    exit(1)
}
require(headers == ["Model", "Wynik"], "Markdown table headers")
require(rows == [["Higgs", "działa"]], "Markdown table rows")
let tableWidths = MarkdownTableLayout.columnWidths(
    headers: ["Slug", "Opis", "Wersja"],
    rows: [[
        "web-research",
        "Badaj aktualne fakty przez prywatny SearXNG i weryfikuj źródła.",
        "1",
    ]]
)
require(tableWidths.count == 3, "Markdown table layout columns")
require(tableWidths[1] > tableWidths[0], "Markdown prose column width")
require(tableWidths[2] == 120, "Markdown compact column minimum width")
guard case .paragraph(let visibleLines) = blockMarkdown.blocks[1] else {
    require(false, "Markdown paragraph block")
    exit(1)
}
require(
    visibleLines == ["Pierwsza linia", "Druga linia"],
    "Markdown explicit line breaks"
)

let vadEvent = try ProtocolCodec.decode(
    text: #"{"type":"input_audio.vad","probability":0.81,"rms_dbfs":-24.0,"peak":0.31,"zero_fraction":0.02,"state":"speech","speech_ms":224,"silence_ms":0,"buffered_ms":512,"processed_frames":8,"received_messages":10,"received_bytes":10240,"received_bytes_per_second":32000}"#
)
guard case .diagnostics(let vadPatch) = vadEvent else {
    require(false, "VAD diagnostics decoding")
    exit(1)
}
require(vadPatch.vadProbability == 0.81, "VAD probability")
require(vadPatch.rmsDBFS == -24, "backend RMS")
require(vadPatch.vadState == "speech", "VAD state")

var backendDiagnostics = BackendDiagnostics()
backendDiagnostics.merge(vadPatch)
require(backendDiagnostics.receivedBytes == 10_240, "backend byte counter")
require(backendDiagnostics.speechMS == 224, "backend speech timer")

let diagnosticSamples: [Int16] = [0, 16_384, -16_384, 0]
let diagnosticPCM = diagnosticSamples.withUnsafeBytes { Data($0) }
let diagnosticMetrics = PCM16Diagnostics.analyze(diagnosticPCM)
require(abs(diagnosticMetrics.peak - 0.5) < 0.001, "client PCM peak")
require(abs(diagnosticMetrics.zeroFraction - 0.5) < 0.001, "client PCM zero fraction")
let diagnosticWAV = PCM16WAVEncoder.encodeMono16K(diagnosticPCM)
require(String(data: diagnosticWAV.prefix(4), encoding: .ascii) == "RIFF", "diagnostic WAV header")
require(diagnosticWAV.suffix(diagnosticPCM.count) == diagnosticPCM, "diagnostic WAV exact PCM")

// Six-channel planar input: channel 0 is digital silence and speech lives on 4.
let sourceCount = 4_800
var sixChannels = Array(
    repeating: Array(repeating: Float.zero, count: sourceCount),
    count: 6
)
for index in 0..<sourceCount {
    sixChannels[4][index] = sin(Float(index) * 0.09) * 0.35
}
var downmixer = MultiChannelDownmixer()
let selected = downmixer.process(sixChannels)
require(selected.selectedChannel == 4, "select energetic channel instead of channel zero")
require(selected.channelLevels[0].zeroFraction == 1, "channel zero telemetry")
let mono16K = MonoPCM16Resampler.linear(selected.mono, from: 48_000)
require(mono16K.count == 3_200, "48 kHz to 16 kHz mono PCM16 size")
require(PCM16Diagnostics.analyze(mono16K).peak > 0.3, "nonzero mono output")

// Exact reported hardware format and real tap chunk size, kept stateful across
// 235 callbacks (the final callback is shorter) for a five-second WAV.
var streamingDownmixer = MultiChannelDownmixer()
var streamingResampler = StreamingMonoPCM16Resampler(
    sourceRate: 48_000,
    targetRate: 16_000
 )!
var streamingCounters = AudioCaptureCounters()
var fiveSecondPCM = Data()
let fiveSecondSourceFrames = 48_000 * 5
var sourceOffset = 0
while sourceOffset < fiveSecondSourceFrames {
    let frameCount = min(1_024, fiveSecondSourceFrames - sourceOffset)
    var callbackChannels = Array(
        repeating: Array(repeating: Float.zero, count: frameCount),
        count: 6
    )
    for frame in 0..<frameCount {
        let phase = Float(sourceOffset + frame) * 2 * .pi * 440 / 48_000
        callbackChannels[4][frame] = sin(phase) * 0.25
    }
    streamingCounters.recordInputCallback()
    let callbackDownmix = streamingDownmixer.process(callbackChannels)
    require(callbackDownmix.selectedChannel == 4, "48k/6ch callback channel selection")
    let callbackPCM = streamingResampler.process(callbackDownmix.mono)
    require(!callbackPCM.isEmpty, "each real-size callback produces PCM")
    streamingCounters.recordConvertedFrame()
    streamingCounters.recordSentFrame(bytes: callbackPCM.count)
    fiveSecondPCM.append(callbackPCM)
    sourceOffset += frameCount
}
require(streamingCounters.inputCallbacks == 235, "five-second callback count")
require(streamingCounters.convertedFrames == 235, "five-second conversion count")
require(streamingCounters.sentFrames == 235, "five-second sent frame count")
require(fiveSecondPCM.count == 16_000 * 5 * 2, "stateful 48k-to-16k duration")
require(streamingCounters.sentBytes == fiveSecondPCM.count, "stateful sent byte count")
let fiveSecondWAV = PCM16WAVEncoder.encodeMono16K(fiveSecondPCM)
require(fiveSecondWAV.count == 44 + 16_000 * 5 * 2, "five-second WAV duration")
require(PCM16Diagnostics.analyze(fiveSecondPCM).peak > 0.2, "five-second WAV signal")

let allZero = downmixer.process(
    Array(repeating: Array(repeating: Float.zero, count: 1_024), count: 6)
)
require(allZero.allChannelsDigitalZero, "all-zero multichannel detection")

let positive = (0..<1_024).map { sin(Float($0) * 0.1) * 0.5 }
let negative = positive.map(-)
var phaseSafeDownmixer = MultiChannelDownmixer()
let phaseSafe = phaseSafeDownmixer.process([positive, negative])
require(phaseSafe.mono.map { abs($0) }.max()! > 0.49, "opposite phase must not cancel")

var counters = AudioCaptureCounters()
counters.recordInputCallback()
counters.recordInputCallback()
counters.recordConvertedFrame()
counters.recordSentFrame(bytes: 640)
require(counters.inputCallbacks == 2, "input callback counter")
require(counters.convertedFrames == 1, "converted frame counter")
require(counters.sentFrames == 1 && counters.sentBytes == 640, "network counters")

require(
    AudioCapturePolicy.plan(
        permission: .allowed,
        systemDefaultUID: "built-in",
        requestedUID: nil
    ) == .systemDefault(deviceUID: "built-in"),
    "system default requires no explicit AudioUnit routing"
)
require(
    AudioCapturePolicy.plan(
        permission: .allowed,
        systemDefaultUID: "built-in",
        requestedUID: "aggregate"
    ) == .systemDefaultFallback(deviceUID: "built-in", requestedUID: "aggregate"),
    "unsupported explicit selection falls back safely"
)
require(
    AudioCapturePolicy.plan(
        permission: .denied,
        systemDefaultUID: "built-in",
        requestedUID: nil
    ) == .blockedPermission,
    "permission denial is distinct"
)
require(
    AudioCapturePolicy.plan(
        permission: .allowed,
        systemDefaultUID: nil,
        requestedUID: nil
    ) == .blockedNoSystemInput,
    "missing system input is distinct"
)

// Conversation lifecycle and strict reasoning/content separation.
var state = ConversationState()
state.beginConnecting()
state.reduce(.ready)
state.reduce(.transcriptFinal("Cześć"))
state.reduce(reasoning)
state.reduce(content)
require(state.messages.count == 2, "reasoning must stay hidden")
require(state.messages[0].role == .user, "user transcript state")
require(state.messages[1].text == "Witaj", "assistant content state")
require(state.phase == .thinking, "thinking state")

let readyWithSession = try ProtocolCodec.decode(
    text: #"{"type":"session.ready","session_id":"session-1","resumed":true}"#
)
require(
    readyWithSession == .sessionReady(sessionID: "session-1", resumed: true),
    "session identity must decode"
)
state.reduce(.sessionHistory([
    SessionHistoryItem(role: "user", text: "Previous question"),
    SessionHistoryItem(role: "tool", text: "hidden tool result"),
    SessionHistoryItem(role: "assistant", text: "Previous answer")
]))
require(state.messages.count == 2, "public session history must replace transient state")
require(state.messages[0].text == "Previous question", "history user message")
require(state.messages[1].text == "Previous answer", "history assistant message")

let toolStarted = try ProtocolCodec.decode(
    text: #"{"type":"tool.call.started","tool_call_id":"call-1","name":"web_search","arguments":"{\"query\":\"bitcoin price\"}"}"#
)
require(
    toolStarted == .toolStarted(
        name: "web_search",
        callID: "call-1",
        arguments: #"{"query":"bitcoin price"}"#
    ),
    "tool lifecycle event must decode"
)
state.reduce(toolStarted)
require(state.activeToolName == "web_search", "active tool must be visible")
require(state.messages.count == 2, "tool events must not enter chat history")
state.reduce(.toolCompleted(name: "web_search", callID: "call-1"))
require(state.activeToolName == nil, "completed tool must clear active status")

// XDG-style client settings survive a round trip independently of UserDefaults.
let settingsRoot = FileManager.default.temporaryDirectory
    .appendingPathComponent("voice-agent-settings-\(UUID().uuidString)", isDirectory: true)
let settingsSuite = "VoiceAgentCoreSmoke.\(UUID().uuidString)"
guard let isolatedDefaults = UserDefaults(suiteName: settingsSuite) else {
    fatalError("cannot create isolated defaults")
}
defer {
    isolatedDefaults.removePersistentDomain(forName: settingsSuite)
    try? FileManager.default.removeItem(at: settingsRoot)
}
let settingsStore = ClientSettingsStore(rootURL: settingsRoot)
var clientSettings = try settingsStore.loadOrMigrate(defaults: isolatedDefaults)
require(clientSettings.language == "en", "clean client settings must default to English")
require(settingsStore.settingsURL.path.hasSuffix("/client/settings.json"), "JOI settings path")
clientSettings.contextPanelVisible = true
clientSettings = try settingsStore.save(clientSettings)
require(clientSettings.revision == 2, "client settings revision")
let reloadedClientSettings = try settingsStore.loadOrMigrate(defaults: isolatedDefaults)
require(reloadedClientSettings == clientSettings, "client settings round trip")

let localizationRoot = URL(fileURLWithPath: "Resources/Localization", isDirectory: true)
let localizationManifest = try LocalizationManifest.load(resourceRoot: localizationRoot)
require(localizationManifest.languages.map(\.id) == ["en", "pl"], "localization manifest")
let englishCatalog = try LocalizationCatalog.load(language: "en", resourceRoot: localizationRoot)
let polishCatalog = try LocalizationCatalog.load(language: "pl", resourceRoot: localizationRoot)
require(Set(englishCatalog.values.keys) == Set(polishCatalog.values.keys), "localization key parity")
require(L10n.configure(language: "en", resourceRoot: localizationRoot), "English localization load")
require(ConversationPhase.recording.displayName == "Listening", "English runtime localization")
require(L10n.configure(language: "pl", resourceRoot: localizationRoot), "Polish localization load")
require(ConversationPhase.recording.displayName == "Słucham", "Polish runtime localization")
for phase: ConversationPhase in [.disconnected, .connecting, .recording, .transcribing, .thinking, .speaking, .failed] {
    for language in ["pl", "en", "pl"] {
        require(L10n.configure(language: language, resourceRoot: localizationRoot), "phase language switch")
        let catalog = language == "pl" ? polishCatalog : englishCatalog
        require(phase.displayName == catalog.text("phase.\(phase.rawValue)"), "phase label must update without a phase change")
    }
}
require(L10n.configure(language: "en", resourceRoot: localizationRoot), "restore English catalog")
require(ConversationPhase.disconnected.displayName == "Disconnected", "disconnected English label")

let contextEvent = try ProtocolCodec.decode(
    text: #"{"type":"context.metrics","input_tokens":1200,"output_tokens":80,"context_size":128000,"context_used_tokens":1200,"context_remaining_tokens":126800,"categories":{"system_prompt":120,"skills":180,"tools":300,"session":600}}"#
)
guard case .diagnostics(let contextPatch) = contextEvent else {
    fatalError("context metrics did not decode")
}
require(contextPatch.inputTokens == 1_200, "input token telemetry")
require(contextPatch.systemPromptTokens == 120, "context system prompt category")
require(contextPatch.sessionTokens == 600, "context session category")

let backendConfigData = Data(#"{"schema_version":1,"config_root":"/tmp/joi","data_root":"/tmp/joi-data","settings":{"schema_version":1,"revision":4,"stt":{"mode":"real"}},"models":{"schema_version":1,"revision":7,"active_profile":"default","profiles":[{"id":"default","model":"deepseek-v4","context_size":128000,"reasoning_levels":["none","low","high","max"]}]},"secrets":{"llm_api_key":{"configured":false}}}"#.utf8)
let backendConfig = try JSONDecoder().decode(BackendConfigSnapshot.self, from: backendConfigData)
require(backendConfig.settingsRevision == 4, "backend settings revision")
require(backendConfig.modelsRevision == 7, "backend models revision")
require(backendConfig.activeModelProfile?["model"]?.stringValue == "deepseek-v4", "active model profile")
require(backendConfig.settingsChanges["revision"] == nil, "patch excludes revision")

// Barge-in can cancel an active response once and returns to recording.
state.reduce(.audioStart(responseID: "r-1", sampleRate: 24_000, channels: 1, encoding: "wav"))
require(state.phase == .speaking, "speaking state")
state.reduce(.assistantDelta(" dalszy tekst"))
require(state.phase == .speaking, "streamed text must not replace speaking state")
require(state.bargeIn(), "barge-in should cancel speaking")
require(state.phase == .recording, "barge-in should resume recording")
require(state.cancellationCount == 1, "cancellation count")
require(!state.bargeIn(), "inactive response must not be cancelled again")

print("VoiceAgentCore smoke fallback: PASS")
