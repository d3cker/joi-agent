import SwiftUI
import VoiceAgentCore

struct SettingsView: View {
    @ObservedObject var store: ConversationStore
    @StateObject private var options = OptionsStore()
    @State private var showingRestartConfirmation = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "gearshape.2.fill")
                    .font(.title2)
                    .foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 2) {
                    Text(store.text("options.title")).font(.headline)
                    Text(store.text("options.subtitle"))
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(18)

            Divider()

            TabView {
                clientPane
                    .tabItem { Label(store.text("options.client"), systemImage: "macbook") }
                modelPane
                    .tabItem { Label(store.text("options.model"), systemImage: "brain") }
                backendPane
                    .tabItem { Label(store.text("options.backend"), systemImage: "server.rack") }
                accessPane
                    .tabItem { Label(store.text("options.access"), systemImage: "key") }
            }
            .padding(14)

            Divider()
            footer
        }
        .frame(minWidth: 820, minHeight: 650)
        .task {
            guard !options.accessKey.isEmpty else { return }
            await refresh()
        }
        .confirmationDialog(
            store.text("options.restart_confirmation"),
            isPresented: $showingRestartConfirmation
        ) {
            Button(store.text("options.restart_backend"), role: .destructive) {
                Task { await restartBackend() }
            }
            Button(store.text("common.cancel"), role: .cancel) {}
        } message: {
            Text(store.text("options.restart_confirmation_detail"))
        }
    }

    private var clientPane: some View {
        Form {
            Section(store.text("options.interface")) {
                LabeledContent(store.text("language.title")) {
                    LanguageSelector(
                        languages: store.availableInterfaceLanguages,
                        selection: $store.interfaceLanguage
                    )
                    .frame(width: 220)
                }
                Toggle(store.text("context.show"), isOn: $store.contextPanelVisible)
            }
            Section(store.text("options.connection")) {
                TextField(store.text("controls.backend_websocket"), text: $store.endpoint)
                Picker(store.text("controls.reasoning"), selection: $store.reasoningEffort) {
                    ForEach(store.availableReasoningEfforts, id: \.self) { level in
                        Text(store.reasoningDisplayName(level)).tag(level)
                    }
                }
                Toggle(store.text("diagnostics.aec"), isOn: $store.useVoiceProcessing)
            }
            Text(store.text("options.client_saved_immediately"))
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
    }

    @ViewBuilder
    private var modelPane: some View {
        if options.draft == nil {
            unloadedPane
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    backendLocationBanner
                    GroupBox(store.text("options.model_profile")) {
                        VStack(spacing: 9) {
                            ForEach(modelFieldOrder, id: \.self) { field in
                                if modelProfileValue(field) != nil {
                                    ConfigFieldRow(
                                        label: displayName(field),
                                        value: modelProfileBinding(field),
                                        applyMode: store.text("options.apply.restart")
                                    )
                                }
                            }
                        }
                        .padding(.top, 6)
                    }
                    HStack {
                        Button(store.text("options.test_llm")) {
                            Task { await options.test(endpoint: store.endpoint, target: "llm") }
                        }
                        Spacer()
                        Text(store.text("options.restart_model_note"))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                .padding(4)
            }
        }
    }

    @ViewBuilder
    private var backendPane: some View {
        if let snapshot = options.draft {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    backendLocationBanner
                    ForEach(sectionOrder.filter { snapshot.settings[$0]?.objectValue != nil }, id: \.self) { section in
                        GroupBox(store.text("options.section.\(section)")) {
                            VStack(spacing: 8) {
                                ForEach(sectionFields(section), id: \.self) { field in
                                    ConfigFieldRow(
                                        label: displayName(field),
                                        value: settingsBinding(section: section, field: field),
                                        disabled: section == "runtime",
                                        applyMode: store.text(applyModeKey(section: section, field: field))
                                    )
                                }
                            }
                            .padding(.top, 6)
                        }
                    }
                }
                .padding(4)
            }
        } else {
            unloadedPane
        }
    }

    private var accessPane: some View {
        Form {
            Section(store.text("options.conversation_access")) {
                SecureField(
                    store.text("options.client_access_key"),
                    text: $store.clientAccessKey
                )
                Text(store.text("options.client_keychain_note"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Section(store.text("options.config_access")) {
                SecureField(store.text("options.config_key"), text: $options.accessKey)
                Text(store.text("options.keychain_note"))
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Button(store.text("options.connect")) { Task { await refresh() } }
                    .disabled(options.accessKey.isEmpty || options.isWorking)
            }
            if let snapshot = options.draft {
                Section(store.text("options.paths")) {
                    LabeledContent(store.text("options.config_root"), value: snapshot.configRoot)
                    LabeledContent(store.text("options.data_root"), value: snapshot.dataRoot)
                }
                Section(store.text("options.secrets")) {
                    ForEach(snapshot.secrets.keys.sorted(), id: \.self) { name in
                        VStack(alignment: .leading, spacing: 6) {
                            HStack {
                                Text(displayName(name)).font(.callout.weight(.medium))
                                Spacer()
                                Text(store.text(snapshot.secrets[name]?.configured == true
                                    ? "options.secret_configured" : "options.secret_missing"))
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            SecureField(
                                store.text("options.secret_replacement"),
                                text: secretBinding(name)
                            )
                            Toggle(
                                store.text("options.secret_clear"),
                                isOn: clearSecretBinding(name)
                            )
                        }
                        .padding(.vertical, 3)
                    }
                }
                Section(store.text("options.connection_tests")) {
                    HStack {
                        ForEach(["llm", "stt", "tts", "openterminal", "searxng"], id: \.self) { target in
                            Button(target) {
                                Task { await options.test(endpoint: store.endpoint, target: target) }
                            }
                        }
                    }
                }
            }
        }
        .formStyle(.grouped)
    }

    private var unloadedPane: some View {
        VStack(spacing: 12) {
            Image(systemName: "gearshape.2")
                .font(.system(size: 42, weight: .light))
                .foregroundStyle(.secondary)
            Text(store.text("options.not_loaded"))
                .font(.headline)
            Text(store.text("options.not_loaded_hint"))
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            if options.isWorking { ProgressView().controlSize(.small) }
            if let error = options.error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            } else if let notice = options.notice {
                Label(notice, systemImage: "checkmark.circle.fill")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            if options.restartRequired {
                Button(store.text("options.restart_backend")) {
                    showingRestartConfirmation = true
                }
                .tint(.orange)
                .disabled(options.isWorking || store.isRunning || !options.restartCapable)
                .help(store.text(store.isRunning
                    ? "options.restart_stop_conversation" : "options.restart_help"))
            }
            Button(store.text("common.refresh")) { Task { await refresh() } }
                .disabled(options.isWorking || options.accessKey.isEmpty)
            Button(store.text("options.save_backend")) { Task { await save() } }
                .buttonStyle(.borderedProminent)
                .disabled(options.isWorking || options.draft == nil)
        }
        .padding(14)
    }

    private func refresh() async {
        await options.refresh(endpoint: store.endpoint)
        if let snapshot = options.draft { store.applyBackendConfiguration(snapshot) }
    }

    private func save() async {
        await options.save(endpoint: store.endpoint)
        if let snapshot = options.draft { store.applyBackendConfiguration(snapshot) }
    }

    private func restartBackend() async {
        await options.restartBackend(endpoint: store.endpoint)
        if let snapshot = options.draft { store.applyBackendConfiguration(snapshot) }
    }

    @ViewBuilder
    private var backendLocationBanner: some View {
        if let snapshot = options.draft {
            VStack(alignment: .leading, spacing: 6) {
                Label(store.text("options.remote_backend"), systemImage: "network")
                    .font(.headline)
                Text(store.text("options.remote_backend_detail", store.endpoint))
                Text(store.text("options.saved_on_server", snapshot.configRoot))
                if options.restartRequired {
                    Text(store.text(
                        "options.pending_restart_fields",
                        options.restartFields.joined(separator: ", ")
                    ))
                    .foregroundStyle(.orange)
                }
            }
            .font(.caption)
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.accentColor.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private let sectionOrder = [
        "server", "security", "llm", "stt", "vad", "tts", "acknowledgement", "agent", "tools", "runtime",
    ]
    private let modelFieldOrder = [
        "id", "name", "base_url", "model", "context_size", "reasoning_levels", "default_reasoning",
    ]

    private func sectionFields(_ section: String) -> [String] {
        options.draft?.settings[section]?.objectValue?.keys.sorted() ?? []
    }

    private func settingsBinding(section: String, field: String) -> Binding<JSONValue> {
        Binding(
            get: { options.draft?.settings[section]?.objectValue?[field] ?? .null },
            set: { value in
                guard var snapshot = options.draft,
                      var values = snapshot.settings[section]?.objectValue else { return }
                values[field] = value
                snapshot.settings[section] = .object(values)
                options.draft = snapshot
            }
        )
    }

    private func modelProfileValue(_ field: String) -> JSONValue? {
        options.draft?.activeModelProfile?[field]
    }

    private func modelProfileBinding(_ field: String) -> Binding<JSONValue> {
        Binding(
            get: { modelProfileValue(field) ?? .null },
            set: { value in
                guard var snapshot = options.draft,
                      let active = snapshot.models["active_profile"]?.stringValue,
                      case .array(var profiles) = snapshot.models["profiles"] else { return }
                guard let index = profiles.firstIndex(where: {
                    $0.objectValue?["id"]?.stringValue == active
                }), var profile = profiles[index].objectValue else { return }
                profile[field] = value
                profiles[index] = .object(profile)
                snapshot.models["profiles"] = .array(profiles)
                options.draft = snapshot
            }
        )
    }

    private func secretBinding(_ name: String) -> Binding<String> {
        Binding(
            get: { options.secretValues[name] ?? "" },
            set: { options.secretValues[name] = $0 }
        )
    }

    private func clearSecretBinding(_ name: String) -> Binding<Bool> {
        Binding(
            get: { options.secretsToClear.contains(name) },
            set: { enabled in
                if enabled { options.secretsToClear.insert(name) }
                else { options.secretsToClear.remove(name) }
            }
        )
    }

    private func displayName(_ value: String) -> String {
        value.replacingOccurrences(of: "_", with: " ")
    }

    private func applyModeKey(section: String, field: String) -> String {
        if section == "runtime" { return "options.apply.locked" }
        if ["llm", "stt", "vad", "tts", "acknowledgement"].contains(section) {
            return "options.apply.restart"
        }
        if section == "server" && ["host", "port"].contains(field) {
            return "options.apply.restart"
        }
        if section == "security" { return "options.apply.restart" }
        if section == "agent" && field == "session_db_path" {
            return "options.apply.restart"
        }
        return "options.apply.live"
    }
}

private struct ConfigFieldRow: View {
    let label: String
    @Binding var value: JSONValue
    var disabled = false
    var applyMode: String?

    init(
        label: String,
        value: Binding<JSONValue>,
        disabled: Bool = false,
        applyMode: String? = nil
    ) {
        self.label = label
        _value = value
        self.disabled = disabled
        self.applyMode = applyMode
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(label)
                .font(.caption)
                .foregroundStyle(.secondary)
                .frame(width: 190, alignment: .trailing)
            if let applyMode {
                Text(applyMode)
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(Color.secondary.opacity(0.12), in: Capsule())
            }
            editor
        }
        .disabled(disabled)
    }

    @ViewBuilder
    private var editor: some View {
        switch value {
        case .string(let current):
            TextField("", text: Binding(
                get: { current },
                set: { value = .string($0) }
            ))
        case .integer(let current):
            TextField("", value: Binding(
                get: { current },
                set: { value = .integer($0) }
            ), format: .number.grouping(.never))
        case .number(let current):
            TextField("", value: Binding(
                get: { current },
                set: { value = .number($0) }
            ), format: .number.grouping(.never))
        case .boolean(let current):
            Toggle("", isOn: Binding(
                get: { current },
                set: { value = .boolean($0) }
            ))
            .labelsHidden()
            .frame(maxWidth: .infinity, alignment: .leading)
        case .array(let current):
            if current.allSatisfy({ $0.stringValue != nil }) {
                TextField("", text: Binding(
                    get: { current.compactMap(\.stringValue).joined(separator: ", ") },
                    set: {
                        value = .array($0.split(separator: ",").map {
                            .string($0.trimmingCharacters(in: .whitespacesAndNewlines))
                        }.filter { $0.stringValue?.isEmpty == false })
                    }
                ))
            } else {
                Text("[…]").foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        case .object:
            Text("{…}").foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        case .null:
            Text("—").foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}
