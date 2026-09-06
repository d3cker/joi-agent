import Foundation

public enum JSONValue: Codable, Equatable, Sendable {
    case string(String)
    case integer(Int)
    case number(Double)
    case boolean(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let value = try decoder.singleValueContainer()
        if value.decodeNil() { self = .null }
        else if let decoded = try? value.decode(Bool.self) { self = .boolean(decoded) }
        else if let decoded = try? value.decode(Int.self) { self = .integer(decoded) }
        else if let decoded = try? value.decode(Double.self) { self = .number(decoded) }
        else if let decoded = try? value.decode(String.self) { self = .string(decoded) }
        else if let decoded = try? value.decode([JSONValue].self) { self = .array(decoded) }
        else if let decoded = try? value.decode([String: JSONValue].self) { self = .object(decoded) }
        else {
            throw DecodingError.dataCorruptedError(
                in: value, debugDescription: "Unsupported JSON configuration value"
            )
        }
    }

    public func encode(to encoder: Encoder) throws {
        var value = encoder.singleValueContainer()
        switch self {
        case .string(let item): try value.encode(item)
        case .integer(let item): try value.encode(item)
        case .number(let item): try value.encode(item)
        case .boolean(let item): try value.encode(item)
        case .object(let item): try value.encode(item)
        case .array(let item): try value.encode(item)
        case .null: try value.encodeNil()
        }
    }

    public var integerValue: Int? {
        if case .integer(let value) = self { return value }
        return nil
    }

    public var stringValue: String? {
        if case .string(let value) = self { return value }
        return nil
    }

    public var objectValue: [String: JSONValue]? {
        if case .object(let value) = self { return value }
        return nil
    }

    public var stringArrayValue: [String]? {
        guard case .array(let values) = self else { return nil }
        let strings = values.compactMap(\.stringValue)
        return strings.count == values.count ? strings : nil
    }
}

public struct BackendSecretStatus: Codable, Equatable, Sendable {
    public let configured: Bool
}

public struct BackendApplicationStatus: Codable, Equatable, Sendable {
    public let status: String
    public let changedFields: [String]
    public let restartFields: [String]
    public let controlledRestart: Bool
    public let instanceID: String

    enum CodingKeys: String, CodingKey {
        case status
        case changedFields = "changed_fields"
        case restartFields = "restart_fields"
        case controlledRestart = "controlled_restart"
        case instanceID = "instance_id"
    }
}

public struct BackendHealth: Codable, Equatable, Sendable {
    public let status: String
    public let version: String
    public let instanceID: String?
    public let controlledRestart: Bool?

    enum CodingKeys: String, CodingKey {
        case status, version
        case instanceID = "instance_id"
        case controlledRestart = "controlled_restart"
    }
}

public struct BackendConfigSnapshot: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let configRoot: String
    public let dataRoot: String
    public var settings: [String: JSONValue]
    public var models: [String: JSONValue]
    public let secrets: [String: BackendSecretStatus]
    public let application: BackendApplicationStatus?

    enum CodingKeys: String, CodingKey {
        case settings, models, secrets, application
        case schemaVersion = "schema_version"
        case configRoot = "config_root"
        case dataRoot = "data_root"
    }

    public var settingsRevision: Int { settings["revision"]?.integerValue ?? 0 }
    public var modelsRevision: Int { models["revision"]?.integerValue ?? 0 }

    public var settingsChanges: [String: JSONValue] {
        settings.filter { !["schema_version", "revision"].contains($0.key) }
    }

    public var modelsChanges: [String: JSONValue] {
        models.filter { !["schema_version", "revision"].contains($0.key) }
    }

    public var activeModelProfile: [String: JSONValue]? {
        guard let active = models["active_profile"]?.stringValue,
              case .array(let profiles) = models["profiles"] else { return nil }
        return profiles.compactMap(\.objectValue).first {
            $0["id"]?.stringValue == active
        }
    }
}

public struct BackendConfigPatch: Codable, Equatable, Sendable {
    public let document: String
    public let expectedRevision: Int
    public let changes: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case document, changes
        case expectedRevision = "expected_revision"
    }

    public init(document: String, expectedRevision: Int, changes: [String: JSONValue]) {
        self.document = document
        self.expectedRevision = expectedRevision
        self.changes = changes
    }
}

public struct BackendSecretUpdate: Codable, Equatable, Sendable {
    public let value: String?
    public init(value: String?) { self.value = value }
}
