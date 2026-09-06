import SwiftUI
import VoiceAgentCore

struct ContentView: View {
    @ObservedObject var store: ConversationStore
    @Environment(\.openWindow) private var openWindow
    @State private var showingRename = false
    @State private var sessionPendingDeletion: SessionSummary?
    @State private var renameTitle = ""

    var body: some View {
        HStack(spacing: 0) {
            SessionSidebar(
                store: store,
                rename: {
                    renameTitle = store.currentSessionTitle == store.text("session.new")
                        ? "" : store.currentSessionTitle
                    showingRename = true
                },
                delete: { sessionPendingDeletion = $0 }
            )
            .frame(width: 258)
            Divider()
            VStack(spacing: 0) {
                header
                Divider()
                conversation
                Divider()
                controls
            }
            if store.contextPanelVisible {
                Divider()
                ContextUsagePanel(store: store)
                    .frame(width: 270)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .task { await store.loadSessionManagement() }
        .sheet(isPresented: $showingRename) {
            VStack(alignment: .leading, spacing: 16) {
                Text(store.text("session.rename_title")).font(.headline)
                TextField(store.text("session.rename_field"), text: $renameTitle)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { submitRename() }
                HStack {
                    Spacer()
                    Button(store.text("common.cancel")) { showingRename = false }
                    Button(store.text("common.save")) { submitRename() }
                        .buttonStyle(.borderedProminent)
                        .disabled(renameTitle.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
            }
            .padding(22)
            .frame(width: 390)
        }
        .confirmationDialog(
            deletionConfirmationTitle,
            isPresented: deletionConfirmationPresented
        ) {
            Button(store.text("session.delete"), role: .destructive) {
                guard let identifier = sessionPendingDeletion?.id else { return }
                sessionPendingDeletion = nil
                Task { await store.deleteSession(id: identifier) }
            }
            Button(store.text("common.cancel"), role: .cancel) { sessionPendingDeletion = nil }
        }
    }

    private var deletionConfirmationPresented: Binding<Bool> {
        Binding(
            get: { sessionPendingDeletion != nil },
            set: { if !$0 { sessionPendingDeletion = nil } }
        )
    }

    private var deletionConfirmationTitle: String {
        let title = sessionPendingDeletion?.title?.trimmingCharacters(in: .whitespacesAndNewlines)
        return store.text(
            "session.delete_confirmation",
            (title?.isEmpty == false ? title : nil) ?? store.text("session.new")
        )
    }

    private func submitRename() {
        let title = renameTitle
        showingRename = false
        Task { await store.renameCurrentSession(to: title) }
    }

    private var header: some View {
        VStack(spacing: 10) {
            HStack(spacing: 12) {
                Image(systemName: "waveform.circle.fill")
                    .font(.system(size: 29))
                    .foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 2) {
                    Text(store.currentSessionTitle)
                        .font(.headline)
                    Text(store.text("app.local_voice_agent"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer()
                LanguageSelector(
                    languages: store.availableInterfaceLanguages,
                    selection: $store.interfaceLanguage
                )
                .frame(width: 120)
                .help(store.text("language.title"))
                Button {
                    openWindow(id: "options")
                } label: {
                    Image(systemName: "gearshape")
                }
                .buttonStyle(.borderless)
                .help(store.text("options.open"))
                Button {
                    withAnimation { store.contextPanelVisible.toggle() }
                } label: {
                    Image(systemName: store.contextPanelVisible
                        ? "sidebar.right" : "sidebar.right")
                }
                .buttonStyle(.borderless)
                .help(store.text(store.contextPanelVisible ? "context.hide" : "context.show"))
                StatusPill(phase: store.state.phase)
            }
            StatsBar(store: store)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    private var conversation: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 14) {
                    if store.state.messages.isEmpty && store.state.partialTranscript.isEmpty {
                        EmptyConversationView(store: store)
                            .padding(.top, 80)
                    }
                    ForEach(store.state.messages) { message in
                        MessageBubble(store: store, message: message)
                            .id(message.id)
                    }
                    if !store.state.partialTranscript.isEmpty {
                        PartialTranscript(text: store.state.partialTranscript)
                            .id("partial")
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(22)
            }
            .onChange(of: store.state.messages) { _ in
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: store.state.partialTranscript) { _ in
                withAnimation { proxy.scrollTo("bottom", anchor: .bottom) }
            }
        }
    }

    private var controls: some View {
        VStack(spacing: 14) {
            if let detail = store.statusDetail {
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(store.state.phase == .failed ? .red : .secondary)
                    .multilineTextAlignment(.center)
            }
            DiagnosticsPanel(store: store, audio: store.audio)
            HStack(spacing: 14) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(store.text("controls.reasoning"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Picker(store.text("controls.reasoning"), selection: $store.reasoningEffort) {
                        ForEach(store.availableReasoningEfforts, id: \.self) { effort in
                            Text(store.reasoningDisplayName(effort)).tag(effort)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 145)
                }
                Spacer()
                if store.isRunning {
                    Button(action: store.commitUtterance) {
                        Label(store.text("controls.emergency_send"), systemImage: "arrow.up.circle.fill")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.large)
                    .help(store.text("controls.emergency_help"))
                    .disabled(store.state.phase == .thinking || store.state.phase == .speaking)
                }
                Button(action: store.toggle) {
                    Label(
                        store.text(store.isRunning ? "controls.stop" : "controls.start"),
                        systemImage: store.isRunning ? "stop.fill" : "mic.fill"
                    )
                    .frame(minWidth: 92)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .tint(store.isRunning ? .red : .blue)
            }
        }
        .padding(20)
        .background(.bar)
    }
}

private struct SessionSidebar: View {
    @ObservedObject var store: ConversationStore
    let rename: () -> Void
    let delete: (SessionSummary) -> Void
    @State private var hoveredSessionID: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(store.text("session.title")).font(.headline)
                Spacer()
                Button {
                    Task { await store.createNewSession() }
                } label: {
                    Image(systemName: "square.and.pencil")
                }
                .buttonStyle(.borderless)
                .help(store.text("session.new_action"))
                .disabled(store.sessionOperationInProgress)
            }
            .padding(14)

            Divider()

            if let error = store.sessionError {
                VStack(alignment: .leading, spacing: 7) {
                    Label(store.text("session.error_title"), systemImage: "wifi.exclamationmark")
                        .font(.caption.weight(.semibold))
                    Text(store.text("session.error_hint"))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    HStack {
                        Button(store.text("common.retry")) {
                            Task { await store.retrySessionManagement() }
                        }
                        Button(store.text("common.hide"), action: store.clearSessionError)
                    }
                    .buttonStyle(.borderless)
                }
                .padding(10)
                .background(Color.orange.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
                .padding(.horizontal, 8)
                .help(error)
            }

            ScrollView {
                LazyVStack(spacing: 5) {
                    if store.sessions.isEmpty {
                        VStack(spacing: 8) {
                            Image(systemName: "bubble.left.and.exclamationmark.bubble.right")
                            Text(store.text("session.empty"))
                            Text(store.text("session.empty_hint"))
                                .font(.caption2)
                        }
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                        .padding(.vertical, 24)
                    }
                    ForEach(store.sessions) { session in
                        HStack(spacing: 4) {
                            Button {
                                Task { await store.switchSession(to: session.id) }
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    HStack(spacing: 5) {
                                        Text(session.title ?? store.text("session.new"))
                                            .font(.callout.weight(session.id == store.currentSessionID ? .semibold : .regular))
                                            .lineLimit(1)
                                        Spacer()
                                        if session.id == store.currentSessionID {
                                            Circle().fill(Color.accentColor).frame(width: 6, height: 6)
                                        }
                                    }
                                    if let preview = session.lastMessage, !preview.isEmpty {
                                        Text(preview)
                                            .font(.caption2)
                                            .foregroundStyle(.secondary)
                                            .lineLimit(2)
                                    }
                                    Text(shortDate(session.updatedAt))
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(9)
                            }
                            .buttonStyle(.plain)

                            if hoveredSessionID == session.id || session.id == store.currentSessionID {
                                Button(role: .destructive) {
                                    delete(session)
                                } label: {
                                    Image(systemName: "trash")
                                        .frame(width: 22, height: 28)
                                }
                                .buttonStyle(.borderless)
                                .help(store.text("session.delete"))
                                .accessibilityLabel(store.text(
                                    "session.delete_accessibility",
                                    session.title ?? store.text("session.new")
                                ))
                            }
                        }
                        .padding(.trailing, 5)
                        .background(
                            session.id == store.currentSessionID
                                ? Color.accentColor.opacity(0.13) : Color.clear,
                            in: RoundedRectangle(cornerRadius: 8)
                        )
                        .contentShape(Rectangle())
                        .onHover { hovering in
                            hoveredSessionID = hovering ? session.id : nil
                        }
                        .disabled(store.sessionOperationInProgress)
                    }
                }
                .padding(8)
            }

            Divider()
            ToolActivityView(store: store, records: store.toolActivity)
                .frame(maxHeight: 210)
            Divider()

            HStack {
                Button(store.text("session.rename"), action: rename)
                    .disabled(store.currentSession == nil || store.sessionOperationInProgress)
                Spacer()
            }
            .buttonStyle(.borderless)
            .padding(12)
        }
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    private func shortDate(_ value: String) -> String {
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = parser.date(from: value) else { return value }
        return date.formatted(
            Date.FormatStyle(date: .abbreviated, time: .shortened)
                .locale(Locale(identifier: store.interfaceLanguage))
        )
    }
}

private struct ToolActivityView: View {
    @ObservedObject var store: ConversationStore
    let records: [ToolActivityRecord]

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            Label(store.text("tool.activity"), systemImage: "wrench.and.screwdriver")
                .font(.caption.weight(.semibold))
            if records.isEmpty {
                Text(store.text("tool.empty"))
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 8) {
                        ForEach(records) { record in
                            HStack(alignment: .top, spacing: 7) {
                                Image(systemName: icon(record.status))
                                    .foregroundStyle(color(record.status))
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(record.name).font(.caption.weight(.medium))
                                    Text(summary(record.arguments))
                                        .font(.caption2.monospaced())
                                        .foregroundStyle(.secondary)
                                        .lineLimit(2)
                                    if let error = record.error {
                                        Text(error).font(.caption2).foregroundStyle(.red).lineLimit(2)
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        .padding(12)
    }

    private func icon(_ status: String) -> String {
        switch status {
        case "completed": return "checkmark.circle.fill"
        case "failed", "interrupted": return "xmark.circle.fill"
        default: return "clock.fill"
        }
    }

    private func color(_ status: String) -> Color {
        switch status {
        case "completed": return .green
        case "failed", "interrupted": return .red
        default: return .orange
        }
    }

    private func summary(_ arguments: String) -> String {
        let compact = arguments.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
        return compact.count > 120 ? String(compact.prefix(117)) + "…" : compact
    }
}

private struct StatusPill: View {
    let phase: ConversationPhase

    private var color: Color {
        switch phase {
        case .recording: return .green
        case .transcribing: return .purple
        case .thinking: return .orange
        case .speaking: return .blue
        case .failed: return .red
        case .connecting: return .yellow
        case .disconnected: return .secondary
        }
    }

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(phase.displayName).font(.caption.weight(.medium))
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 7)
        .background(color.opacity(0.12), in: Capsule())
    }
}

private struct StatsBar: View {
    @ObservedObject var store: ConversationStore

    var body: some View {
        HStack(spacing: 8) {
            StatChip(
                icon: "arrow.down.to.line.compact",
                title: store.text("stats.input_tokens"),
                value: formatCount(store.state.diagnostics.inputTokens)
            )
            StatChip(
                icon: "arrow.up.to.line.compact",
                title: store.text("stats.output_tokens"),
                value: formatCount(store.state.diagnostics.outputTokens)
            )
            StatChip(
                icon: "square.stack.3d.up",
                title: store.text("stats.context_size"),
                value: formatCount(store.state.diagnostics.contextSize)
            )
            StatChip(
                icon: "gauge.with.dots.needle.67percent",
                title: store.text("stats.tps"),
                value: formatRate(store.state.diagnostics.tokensPerSecond)
            )
            StatChip(
                icon: "bolt.horizontal",
                title: store.text("stats.pp_tps"),
                value: formatRate(store.state.diagnostics.promptProcessingTokensPerSecond)
            )
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .contain)
    }

    private func formatCount(_ count: Int) -> String {
        guard count > 0 else { return "—" }
        if count >= 1_000_000 { return String(format: "%.1fM", Double(count) / 1_000_000) }
        if count >= 1_000 { return String(format: "%.1fk", Double(count) / 1_000) }
        return String(count)
    }

    private func formatRate(_ rate: Double?) -> String {
        rate.map { String(format: "%.1f", $0) } ?? "—"
    }
}

private struct StatChip: View {
    let icon: String
    let title: String
    let value: String

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: icon).foregroundStyle(.secondary)
            Text(title).foregroundStyle(.secondary)
            Text(value).fontWeight(.semibold).monospacedDigit()
        }
        .font(.caption)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 7))
    }
}

private struct ContextUsagePanel: View {
    @ObservedObject var store: ConversationStore

    private var diagnostics: BackendDiagnostics { store.state.diagnostics }
    private var capacity: Int { max(1, diagnostics.contextSize) }
    private var items: [ContextItem] {
        [
            ContextItem(key: "context.system_prompt", icon: "text.quote", color: .blue,
                        tokens: diagnostics.systemPromptTokens),
            ContextItem(key: "context.skills", icon: "sparkles", color: .purple,
                        tokens: diagnostics.skillsTokens),
            ContextItem(key: "context.tools", icon: "wrench.and.screwdriver", color: .orange,
                        tokens: diagnostics.toolsTokens),
            ContextItem(key: "context.session", icon: "bubble.left.and.bubble.right", color: .green,
                        tokens: diagnostics.sessionTokens),
        ]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text(store.text("context.panel_title"))
                    .font(.headline)
                Spacer()
                Button {
                    withAnimation { store.contextPanelVisible = false }
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help(store.text("context.hide"))
            }

            Text(store.text(
                "context.used_format",
                formatCount(diagnostics.contextUsedTokens),
                formatCount(diagnostics.contextSize)
            ))
            .font(.caption)
            .foregroundStyle(.secondary)

            GeometryReader { geometry in
                HStack(spacing: 1) {
                    ForEach(items) { item in
                        if item.tokens > 0 {
                            Rectangle()
                                .fill(item.color)
                                .frame(width: segmentWidth(item.tokens, available: geometry.size.width))
                        }
                    }
                    Rectangle()
                        .fill(Color.secondary.opacity(0.15))
                        .frame(width: segmentWidth(diagnostics.contextRemainingTokens,
                                                   available: geometry.size.width))
                }
                .clipShape(RoundedRectangle(cornerRadius: 5))
            }
            .frame(height: 15)

            VStack(spacing: 8) {
                ForEach(items) { item in
                    contextRow(item)
                }
                contextRow(ContextItem(
                    key: "context.remaining",
                    icon: "circle.dotted",
                    color: .secondary,
                    tokens: diagnostics.contextRemainingTokens
                ))
            }

            if diagnostics.contextBreakdownEstimated {
                Label(store.text("context.breakdown_estimated"), systemImage: "info.circle")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer()
        }
        .padding(16)
        .background(Color(nsColor: .underPageBackgroundColor))
    }

    private func contextRow(_ item: ContextItem) -> some View {
        HStack(spacing: 9) {
            Image(systemName: item.icon)
                .foregroundStyle(item.color)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 2) {
                Text(store.text(item.key)).font(.caption.weight(.medium))
                ProgressView(value: Double(max(0, item.tokens)), total: Double(capacity))
                    .tint(item.color)
            }
            Text(formatCount(item.tokens))
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(minWidth: 42, alignment: .trailing)
        }
    }

    private func segmentWidth(_ tokens: Int, available: CGFloat) -> CGFloat {
        max(tokens > 0 ? 1 : 0, available * CGFloat(max(0, tokens)) / CGFloat(capacity))
    }

    private func formatCount(_ count: Int) -> String {
        count.formatted(.number.locale(Locale(identifier: store.interfaceLanguage)))
    }
}

private struct ContextItem: Identifiable {
    let key: String
    let icon: String
    let color: Color
    let tokens: Int
    var id: String { key }
}

private struct DiagnosticsPanel: View {
    @ObservedObject var store: ConversationStore
    @ObservedObject var audio: AudioController
    @State private var expanded = false

    private var meterValue: Double {
        min(1, max(0, (audio.diagnostics.rmsDBFS + 60) / 60))
    }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    Text(store.text("diagnostics.stage", store.diagnosticStage))
                        .font(.callout.weight(.semibold))
                    Spacer()
                    Text(store.text("diagnostics.websocket", store.websocketStatusText))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(store.text("diagnostics.microphone_level"))
                        Spacer()
                        Text(String(format: "%.1f dBFS", audio.diagnostics.rmsDBFS))
                            .monospacedDigit()
                    }
                    ProgressView(value: meterValue)
                        .tint(meterValue > 0.25 ? .green : .orange)
                    WaveformView(samples: audio.diagnostics.waveform)
                        .frame(height: 34)
                }

                HStack {
                    Text(store.text("diagnostics.microphone"))
                        .font(.caption.weight(.semibold))
                    Picker(store.text("diagnostics.microphone"), selection: Binding(
                        get: { audio.selectedInputDeviceUID },
                        set: { audio.selectInputDevice(uid: $0) }
                    )) {
                        Text(store.text("diagnostics.system_default"))
                            .tag(audio.systemDefaultSelectionID)
                        Divider()
                        ForEach(audio.availableInputDevices) { device in
                            Text(device.name + (device.isSystemDefault
                                ? store.text("diagnostics.device_default_suffix") : ""))
                                .tag(device.uid)
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: 360)
                    Button(store.text("diagnostics.refresh_devices"), action: audio.refreshInputDevices)
                    Button(store.text("diagnostics.input_settings"), action: store.openInputSettings)
                    Button(store.text("diagnostics.open_microphone_settings"), action: store.openMicrophonePrivacySettings)
                    Spacer()
                    Text(store.text("diagnostics.active_device", audio.diagnostics.activeDeviceName))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(alignment: .top, spacing: 24) {
                    diagnosticColumn(
                        title: store.text("diagnostics.mac_microphone"),
                        lines: [
                            store.text(
                                "diagnostics.device",
                                audio.diagnostics.activeDeviceName,
                                audio.diagnostics.activeDeviceIsDefault
                                    ? store.text("diagnostics.device_default_suffix") : ""
                            ),
                            store.text("diagnostics.source",
                                       Int64(audio.diagnostics.sourceSampleRate),
                                       Int64(audio.diagnostics.sourceChannels)),
                            store.text("diagnostics.format", audio.diagnostics.sourceFormat,
                                       yesNo(audio.diagnostics.sourceInterleaved)),
                            store.text("diagnostics.voice_processing",
                                       yesNo(audio.diagnostics.voiceProcessingEnabled),
                                       yesNo(audio.diagnostics.voiceProcessingMuted)),
                            store.text("diagnostics.capture_callbacks",
                                       Int64(audio.diagnostics.inputCallbacks),
                                       audio.diagnostics.inputCallbacksPerSecond),
                            store.text("diagnostics.capture_conversions",
                                       Int64(audio.diagnostics.convertedFrames),
                                       Int64(audio.diagnostics.conversionFailures)),
                            store.text("diagnostics.capture_sent",
                                       Int64(audio.diagnostics.sentFrames),
                                       audio.diagnostics.sentFramesPerSecond,
                                       ByteCountFormatter.string(
                                        fromByteCount: Int64(audio.diagnostics.sentBytes), countStyle: .file)),
                            store.text("diagnostics.capture_selected_channel",
                                       optional(audio.diagnostics.selectedChannel),
                                       Int64(audio.diagnostics.lastFrameBytes),
                                       Int64(audio.diagnostics.discontinuities)),
                            store.text("diagnostics.capture_peak", audio.diagnostics.peak,
                                       audio.diagnostics.zeroFraction * 100),
                        ]
                    )
                    diagnosticColumn(
                        title: store.text("diagnostics.backend_vad"),
                        lines: [
                            store.text("diagnostics.vad_probability",
                                       store.state.diagnostics.vadProbability,
                                       store.state.diagnostics.vadState),
                            store.text("diagnostics.vad_timing",
                                       Int64(store.state.diagnostics.speechMS),
                                       Int64(store.state.diagnostics.silenceMS)),
                            store.text("diagnostics.vad_buffer",
                                       Int64(store.state.diagnostics.bufferedMS),
                                       Int64(store.state.diagnostics.receivedBytes)),
                            store.text("diagnostics.commit",
                                       store.state.diagnostics.commitReason ?? store.text("common.none"),
                                       milliseconds(store.state.diagnostics.commitAudioMS)),
                            store.text("diagnostics.stt",
                                       milliseconds(store.state.diagnostics.sttMS),
                                       format(store.state.diagnostics.noSpeechProbability)),
                            store.text("diagnostics.confidence",
                                       format(store.state.diagnostics.confidence),
                                       format(store.state.diagnostics.compressionRatio)),
                        ]
                    )
                    diagnosticColumn(
                        title: store.text("diagnostics.response"),
                        lines: [
                            store.text("diagnostics.ttft",
                                       milliseconds(store.state.diagnostics.llmTTFTMS)),
                            store.text("diagnostics.tokens_per_second",
                                       rate(store.state.diagnostics.tokensPerSecond,
                                            estimated: store.state.diagnostics.tokensEstimated)),
                            store.text("diagnostics.first_audio",
                                       milliseconds(store.state.diagnostics.endpointToFirstAudioMS)),
                            store.text("diagnostics.tts_segment",
                                       milliseconds(store.state.diagnostics.ttsMS)),
                            store.text("diagnostics.playback", Int64(audio.playbackQueueDepth),
                                       optional(audio.currentSegmentIndex)),
                            store.text("diagnostics.played_segments", Int64(audio.playedSegments)),
                        ]
                    )
                }

                if !audio.diagnostics.channelLevels.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(store.text("diagnostics.preconversion_levels"))
                            .font(.caption.weight(.semibold))
                        ForEach(audio.diagnostics.channelLevels, id: \.index) { level in
                            HStack {
                                Text(store.text(
                                    "diagnostics.channel_line",
                                    Int64(level.index),
                                    level.index == audio.diagnostics.selectedChannel
                                        ? store.text("diagnostics.channel_selected") : ""
                                ))
                                    .frame(width: 120, alignment: .leading)
                                ProgressView(value: min(1, max(0, (level.rmsDBFS + 60) / 60)))
                                Text(String(format: "%.1f dBFS · peak %.4f · %.0f%%",
                                            level.rmsDBFS, level.peak, level.zeroFraction * 100))
                                    .font(.caption2)
                                    .monospacedDigit()
                                    .frame(width: 230, alignment: .trailing)
                            }
                        }
                    }
                }

                if !store.state.diagnostics.transcript.isEmpty {
                    Text(store.text("diagnostics.last_transcript", store.state.diagnostics.transcript))
                        .font(.caption)
                        .textSelection(.enabled)
                }
                if let warning = audio.diagnostics.captureWarning {
                    Label(warning, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                }

                HStack {
                    Toggle(store.text("diagnostics.aec"), isOn: $store.useVoiceProcessing)
                        .toggleStyle(.switch)
                    Button(store.text(audio.diagnostics.diagnosticRecording
                                      ? "diagnostics.finish_sample" : "diagnostics.record_sample")) {
                        audio.diagnostics.diagnosticRecording
                            ? store.finishDiagnosticSample()
                            : store.startDiagnosticSample()
                    }
                    Button(store.text("diagnostics.play_sample"), action: store.playDiagnosticSample)
                        .disabled(audio.diagnostics.lastDiagnosticFile == nil)
                    Button(store.text("diagnostics.copy"), action: store.copyDiagnostics)
                    Spacer()
                }
                if let file = audio.diagnostics.lastDiagnosticFile {
                    Text(store.text("diagnostics.local_wav", file))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                }
            }
            .padding(.top, 10)
        } label: {
            Label(store.text("diagnostics.performance_title"), systemImage: "waveform.path.ecg")
                .font(.caption.weight(.semibold))
        }
    }

    private func diagnosticColumn(title: String, lines: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title).font(.caption.weight(.semibold))
            ForEach(lines, id: \.self) { line in
                Text(line).font(.caption2).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func format(_ value: Double?) -> String {
        value.map { String(format: "%.3f", $0) } ?? store.text("common.none")
    }

    private func yesNo(_ value: Bool) -> String {
        store.text(value ? "common.yes" : "common.no")
    }

    private func optional<T>(_ value: T?) -> String {
        value.map { String(describing: $0) } ?? store.text("common.none")
    }

    private func milliseconds(_ value: Int?) -> String {
        value.map { "\($0) ms" } ?? store.text("common.none")
    }

    private func rate(_ value: Double?, estimated: Bool) -> String {
        guard let value else { return store.text("common.none") }
        return String(format: "%.2f%@", value, estimated ? " (est.)" : "")
    }
}

private struct WaveformView: View {
    let samples: [Float]

    var body: some View {
        GeometryReader { geometry in
            Path { path in
                guard !samples.isEmpty else { return }
                let middle = geometry.size.height / 2
                path.move(to: CGPoint(x: 0, y: middle))
                for (index, sample) in samples.enumerated() {
                    let x = geometry.size.width * CGFloat(index) / CGFloat(max(1, samples.count - 1))
                    let y = middle - CGFloat(sample) * middle
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
            .stroke(Color.accentColor, lineWidth: 1.5)
        }
        .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 5))
    }
}

private struct EmptyConversationView: View {
    @ObservedObject var store: ConversationStore
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "waveform.and.mic")
                .font(.system(size: 46, weight: .light))
                .foregroundStyle(.secondary)
            Text(store.text("empty.ready"))
                .font(.title3.weight(.semibold))
            Text(store.text("empty.subtitle"))
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
        }
    }
}

private struct MessageBubble: View {
    @ObservedObject var store: ConversationStore
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.role == .user { Spacer(minLength: 90) }
            VStack(alignment: .leading, spacing: 5) {
                Text(store.text(message.role == .user ? "message.you" : "message.agent"))
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(.secondary)
                if message.role == .assistant {
                    AssistantMarkdownView(markdown: message.text)
                        .textSelection(.enabled)
                } else {
                    Text(message.text)
                        .textSelection(.enabled)
                }
                if message.isStreaming {
                    ProgressView().controlSize(.small)
                }
            }
            .padding(13)
            .background(
                message.role == .user ? Color.accentColor.opacity(0.16) : Color(nsColor: .controlBackgroundColor),
                in: RoundedRectangle(cornerRadius: 14)
            )
            if message.role != .user { Spacer(minLength: 90) }
        }
    }
}

private struct AssistantMarkdownView: View {
    let markdown: String

    private var document: MarkdownDocument {
        MarkdownDocumentParser.parse(markdown)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            ForEach(Array(document.blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder
    private func blockView(_ block: MarkdownBlock) -> some View {
        switch block {
        case .paragraph(let lines):
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    MarkdownInlineText(source: line)
                }
            }
        case .heading(let level, let text):
            MarkdownInlineText(source: text)
                .font(headingFont(level))
                .padding(.top, level == 1 ? 3 : 0)
        case .unorderedList(let items):
            MarkdownList(items: items, ordered: false)
        case .orderedList(let items):
            MarkdownList(items: items, ordered: true)
        case .blockquote(let lines):
            HStack(alignment: .top, spacing: 9) {
                Rectangle()
                    .fill(Color.secondary.opacity(0.45))
                    .frame(width: 3)
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        MarkdownInlineText(source: line)
                    }
                }
                .foregroundStyle(.secondary)
            }
        case .code(let language, let text):
            VStack(alignment: .leading, spacing: 5) {
                if let language {
                    Text(language.uppercased())
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                }
                ScrollView(.horizontal) {
                    Text(text)
                        .font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled)
                        .padding(10)
                }
                .background(Color(nsColor: .textBackgroundColor).opacity(0.75))
                .clipShape(RoundedRectangle(cornerRadius: 7))
            }
        case .table(let headers, let rows):
            MarkdownTable(headers: headers, rows: rows)
        case .thematicBreak:
            Divider()
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: return .title2.weight(.bold)
        case 2: return .title3.weight(.bold)
        case 3: return .headline
        default: return .subheadline.weight(.semibold)
        }
    }
}

private struct MarkdownInlineText: View {
    let source: String

    var body: some View {
        Text(MarkdownDisplayRenderer.render(source))
            .fixedSize(horizontal: false, vertical: true)
    }
}

private struct MarkdownList: View {
    let items: [String]
    let ordered: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(ordered ? "\(index + 1)." : "•")
                        .fontWeight(.semibold)
                        .frame(width: ordered ? 24 : 12, alignment: .trailing)
                    MarkdownInlineText(source: item)
                }
            }
        }
        .padding(.leading, 2)
    }
}

private struct MarkdownTable: View {
    let headers: [String]
    let rows: [[String]]

    private var columnWidths: [CGFloat] {
        MarkdownTableLayout.columnWidths(headers: headers, rows: rows).map { CGFloat($0) }
    }

    var body: some View {
        ScrollView(.horizontal, showsIndicators: true) {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .top, spacing: 0) {
                    ForEach(Array(headers.enumerated()), id: \.offset) { column, cell in
                        tableCell(cell, width: columnWidths[column], header: true)
                    }
                }
                ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, row in
                    HStack(alignment: .top, spacing: 0) {
                        ForEach(Array(headers.indices), id: \.self) { column in
                            tableCell(
                                column < row.count ? row[column] : "",
                                width: columnWidths[column],
                                header: false
                            )
                        }
                    }
                    .background(
                        rowIndex.isMultiple(of: 2)
                            ? Color.secondary.opacity(0.035) : Color.clear
                    )
                }
            }
            .overlay {
                RoundedRectangle(cornerRadius: 6)
                    .stroke(Color.secondary.opacity(0.28), lineWidth: 1)
            }
            .clipShape(RoundedRectangle(cornerRadius: 6))
        }
    }

    private func tableCell(
        _ source: String, width: CGFloat, header: Bool
    ) -> some View {
        MarkdownInlineText(source: source)
            .font(header ? .callout.weight(.semibold) : .callout)
            .frame(width: max(1, width - 18), alignment: .topLeading)
            .padding(.horizontal, 9)
            .padding(.vertical, 7)
            .frame(width: width, alignment: .topLeading)
            .background(header ? Color.secondary.opacity(0.12) : Color.clear)
            .overlay {
                Rectangle().stroke(Color.secondary.opacity(0.18), lineWidth: 0.5)
            }
    }
}

private struct PartialTranscript: View {
    let text: String

    var body: some View {
        HStack {
            Spacer(minLength: 90)
            HStack(spacing: 8) {
                Image(systemName: "waveform").foregroundStyle(.blue)
                Text(text).foregroundStyle(.secondary).italic()
            }
            .padding(12)
            .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
        }
    }
}
