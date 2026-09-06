import Foundation

public struct InterfaceLanguageDescriptor: Codable, Equatable, Identifiable, Sendable {
    public let id: String
    public let name: String

    public init(id: String, name: String) {
        self.id = id
        self.name = name
    }
}

public struct LocalizationManifest: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let defaultLanguage: String
    public let languages: [InterfaceLanguageDescriptor]

    enum CodingKeys: String, CodingKey {
        case languages
        case schemaVersion = "schema_version"
        case defaultLanguage = "default_language"
    }

    public static func load(resourceRoot: URL) throws -> Self {
        let file = resourceRoot.appendingPathComponent("locales.json")
        let decoded: Self
        do {
            decoded = try JSONDecoder().decode(Self.self, from: Data(contentsOf: file))
        } catch {
            throw LocalizationError.invalidManifest
        }
        guard decoded.schemaVersion == 1,
              !decoded.languages.isEmpty,
              Set(decoded.languages.map(\.id)).count == decoded.languages.count,
              decoded.languages.contains(where: { $0.id == decoded.defaultLanguage })
        else { throw LocalizationError.invalidManifest }
        for language in decoded.languages {
            guard Self.validIdentifier(language.id), !language.name.isEmpty else {
                throw LocalizationError.invalidManifest
            }
            _ = try LocalizationCatalog.load(language: language.id, resourceRoot: resourceRoot)
        }
        return decoded
    }

    public static func validIdentifier(_ value: String) -> Bool {
        value.range(
            of: #"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$"#,
            options: .regularExpression
        ) != nil
    }
}

public enum LocalizationError: Error, Equatable {
    case missingCatalog(String)
    case invalidCatalog(String)
    case invalidManifest
}

public struct LocalizationCatalog: Equatable, Sendable {
    public let language: String
    public let values: [String: String]

    public init(language: String, values: [String: String]) {
        self.language = language
        self.values = values
    }

    public static func load(language: String, resourceRoot: URL) throws -> Self {
        let file = resourceRoot.appendingPathComponent("\(language).json")
        guard FileManager.default.fileExists(atPath: file.path) else {
            throw LocalizationError.missingCatalog(language)
        }
        do {
            let decoded = try JSONDecoder().decode(
                [String: String].self,
                from: Data(contentsOf: file)
            )
            guard !decoded.isEmpty else { throw LocalizationError.invalidCatalog(language) }
            return Self(language: language, values: decoded)
        } catch let error as LocalizationError {
            throw error
        } catch {
            throw LocalizationError.invalidCatalog(language)
        }
    }

    public func text(_ key: String, arguments: [CVarArg] = []) -> String {
        let template = values[key] ?? key
        guard !arguments.isEmpty else { return template }
        return String(
            format: template,
            locale: Locale(identifier: language),
            arguments: arguments
        )
    }
}

/// Runtime-selected localization backed exclusively by JSON resource files.
/// SwiftUI observes the language property in ConversationStore; this shared
/// accessor also lets lower-level audio/network errors use the same catalog.
public enum L10n {
    private static let lock = NSLock()
    private static var active = LocalizationCatalog(language: "en", values: [:])

    public static var language: String {
        lock.lock()
        defer { lock.unlock() }
        return active.language
    }

    @discardableResult
    public static func configure(
        language: String,
        resourceRoot: URL? = nil
    ) -> Bool {
        let root = resourceRoot
            ?? Bundle.main.resourceURL?.appendingPathComponent("Localization", isDirectory: true)
        guard let root,
              let english = try? LocalizationCatalog.load(language: "en", resourceRoot: root)
        else { return false }
        let selected = (try? LocalizationCatalog.load(language: language, resourceRoot: root))
            ?? english
        let merged = english.values.merging(selected.values) { _, localized in localized }
        lock.lock()
        active = LocalizationCatalog(language: selected.language, values: merged)
        lock.unlock()
        return true
    }

    public static func text(_ key: String, _ arguments: CVarArg...) -> String {
        lock.lock()
        let catalog = active
        lock.unlock()
        return catalog.text(key, arguments: arguments)
    }
}
