import Foundation

public struct ClientSettingsDocument: Codable, Equatable, Sendable {
    public static let schemaVersion = 1

    public var schemaVersion: Int
    public var revision: Int
    public var language: String
    public var webSocketEndpoint: String
    public var reasoningLevel: String
    public var sessionID: String
    public var useVoiceProcessing: Bool
    public var contextPanelVisible: Bool

    enum CodingKeys: String, CodingKey {
        case revision, language
        case schemaVersion = "schema_version"
        case webSocketEndpoint = "websocket_endpoint"
        case reasoningLevel = "reasoning_level"
        case sessionID = "session_id"
        case useVoiceProcessing = "use_voice_processing"
        case contextPanelVisible = "context_panel_visible"
    }

    public init(
        schemaVersion: Int = Self.schemaVersion,
        revision: Int = 1,
        language: String,
        webSocketEndpoint: String,
        reasoningLevel: String,
        sessionID: String,
        useVoiceProcessing: Bool,
        contextPanelVisible: Bool
    ) {
        self.schemaVersion = schemaVersion
        self.revision = revision
        self.language = language
        self.webSocketEndpoint = webSocketEndpoint
        self.reasoningLevel = reasoningLevel
        self.sessionID = sessionID
        self.useVoiceProcessing = useVoiceProcessing
        self.contextPanelVisible = contextPanelVisible
    }
}

public enum ClientSettingsError: LocalizedError, Equatable {
    case unsupportedSchema(Int)
    case invalidRevision
    case invalidLanguage(String)
    case invalidEndpoint(String)
    case invalidSessionID

    public var errorDescription: String? {
        switch self {
        case .unsupportedSchema(let version):
            return L10n.text("error.client.unsupported_schema", version)
        case .invalidRevision:
            return L10n.text("error.client.invalid_revision")
        case .invalidLanguage(let value):
            return L10n.text("error.client.invalid_language", value)
        case .invalidEndpoint(let value):
            return L10n.text("error.client.invalid_endpoint", value)
        case .invalidSessionID:
            return L10n.text("error.client.invalid_session")
        }
    }
}

public struct ClientSettingsStore: Sendable {
    public let rootURL: URL
    public var settingsURL: URL { rootURL.appendingPathComponent("client/settings.json") }
    public var previousSettingsURL: URL {
        rootURL.appendingPathComponent("client/settings.previous.json")
    }

    public init(rootURL: URL? = nil, environment: [String: String] = ProcessInfo.processInfo.environment) {
        if let rootURL {
            self.rootURL = rootURL.standardizedFileURL
        } else if let override = environment["JOI_CONFIG_HOME"], !override.isEmpty {
            self.rootURL = URL(fileURLWithPath: override, isDirectory: true).standardizedFileURL
        } else if let xdg = environment["XDG_CONFIG_HOME"], !xdg.isEmpty {
            self.rootURL = URL(fileURLWithPath: xdg, isDirectory: true)
                .appendingPathComponent("joi", isDirectory: true)
                .standardizedFileURL
        } else {
            self.rootURL = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(".config/joi", isDirectory: true)
                .standardizedFileURL
        }
    }

    public func loadOrMigrate(defaults: UserDefaults = .standard) throws -> ClientSettingsDocument {
        if FileManager.default.fileExists(atPath: settingsURL.path) {
            var document = try JSONDecoder().decode(
                ClientSettingsDocument.self,
                from: Data(contentsOf: settingsURL)
            )
            let upgraded = TransportSecurityPolicy.upgradingRemoteCleartextEndpoint(
                document.webSocketEndpoint
            )
            if upgraded != document.webSocketEndpoint {
                document.webSocketEndpoint = upgraded
                document.revision += 1
                try write(document, backup: true)
            }
            try validate(document)
            return document
        }

        let endpointKey = "voiceAgent.endpoint"
        let reasoningKey = "voiceAgent.reasoning"
        let sessionKey = "voiceAgent.sessionID"
        let voiceProcessingKey = "voiceAgent.useVoiceProcessing"
        let hadLegacyConfiguration = [endpointKey, reasoningKey, sessionKey].contains {
            defaults.object(forKey: $0) != nil
        }
        let document = ClientSettingsDocument(
            language: hadLegacyConfiguration ? "pl" : "en",
            webSocketEndpoint: TransportSecurityPolicy.upgradingRemoteCleartextEndpoint(
                defaults.string(forKey: endpointKey)
                    ?? "ws://127.0.0.1:8765/ws"
            ),
            reasoningLevel: defaults.string(forKey: reasoningKey) ?? "low",
            sessionID: defaults.string(forKey: sessionKey)
                ?? UUID().uuidString.lowercased(),
            useVoiceProcessing: defaults.object(forKey: voiceProcessingKey) == nil
                ? true : defaults.bool(forKey: voiceProcessingKey),
            contextPanelVisible: false
        )
        try validate(document)
        try write(document, backup: false)
        return document
    }

    @discardableResult
    public func save(_ document: ClientSettingsDocument) throws -> ClientSettingsDocument {
        var updated = document
        updated.schemaVersion = ClientSettingsDocument.schemaVersion
        updated.revision = max(1, document.revision + 1)
        try validate(updated)
        try write(updated, backup: true)
        return updated
    }

    public func validate(_ document: ClientSettingsDocument) throws {
        guard document.schemaVersion == ClientSettingsDocument.schemaVersion else {
            throw ClientSettingsError.unsupportedSchema(document.schemaVersion)
        }
        guard document.revision > 0 else { throw ClientSettingsError.invalidRevision }
        guard LocalizationManifest.validIdentifier(document.language) else {
            throw ClientSettingsError.invalidLanguage(document.language)
        }
        guard TransportSecurityPolicy.allowsWebSocketEndpoint(
            document.webSocketEndpoint
        ) else {
            throw ClientSettingsError.invalidEndpoint(document.webSocketEndpoint)
        }
        guard !document.sessionID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw ClientSettingsError.invalidSessionID
        }
    }

    private func write(_ document: ClientSettingsDocument, backup: Bool) throws {
        let directory = settingsURL.deletingLastPathComponent()
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(document) + Data([0x0A])
        if backup, FileManager.default.fileExists(atPath: settingsURL.path) {
            try Data(contentsOf: settingsURL).write(to: previousSettingsURL, options: .atomic)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o600], ofItemAtPath: previousSettingsURL.path
            )
        }
        try data.write(to: settingsURL, options: .atomic)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o600], ofItemAtPath: settingsURL.path
        )
    }
}
