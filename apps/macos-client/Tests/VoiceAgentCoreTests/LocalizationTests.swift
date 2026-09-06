import Foundation
import XCTest
@testable import VoiceAgentCore

final class LocalizationTests: XCTestCase {
    private var resourceRoot: URL {
        URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .appendingPathComponent("Resources/Localization", isDirectory: true)
    }

    func testEnglishAndPolishCatalogsHaveIdenticalNonEmptyKeys() throws {
        let manifest = try LocalizationManifest.load(resourceRoot: resourceRoot)
        XCTAssertEqual(manifest.defaultLanguage, "en")
        XCTAssertEqual(manifest.languages.map(\.id), ["en", "pl"])
        let english = try LocalizationCatalog.load(language: "en", resourceRoot: resourceRoot)
        let polish = try LocalizationCatalog.load(language: "pl", resourceRoot: resourceRoot)
        XCTAssertEqual(Set(english.values.keys), Set(polish.values.keys))
        XCTAssertFalse(english.values.values.contains(where: { $0.isEmpty }))
        XCTAssertFalse(polish.values.values.contains(where: { $0.isEmpty }))
        for key in english.values.keys {
            XCTAssertEqual(placeholders(english.values[key]!), placeholders(polish.values[key]!), key)
        }
    }

    func testManifestDiscoversAdditionalCatalogWithoutSwiftChanges() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data(#"{"schema_version":1,"default_language":"en","languages":[{"id":"en","name":"English"},{"id":"de","name":"Deutsch"}]}"#.utf8)
            .write(to: root.appendingPathComponent("locales.json"))
        try Data(#"{"test":"English"}"#.utf8).write(to: root.appendingPathComponent("en.json"))
        try Data(#"{"test":"Deutsch"}"#.utf8).write(to: root.appendingPathComponent("de.json"))
        XCTAssertEqual(
            try LocalizationManifest.load(resourceRoot: root).languages.map(\.id),
            ["en", "de"]
        )
    }

    func testRuntimeLanguageSwitchChangesModelLabels() {
        XCTAssertTrue(L10n.configure(language: "en", resourceRoot: resourceRoot))
        XCTAssertEqual(ConversationPhase.recording.displayName, "Listening")
        XCTAssertEqual(ReasoningLevel.maximum.displayName, "Maximum")
        XCTAssertTrue(L10n.configure(language: "pl", resourceRoot: resourceRoot))
        XCTAssertEqual(ConversationPhase.recording.displayName, "Słucham")
        XCTAssertEqual(ReasoningLevel.maximum.displayName, "Maksymalne")
    }

    func testUnknownLanguageFallsBackToEnglish() {
        XCTAssertTrue(L10n.configure(language: "xx", resourceRoot: resourceRoot))
        XCTAssertEqual(L10n.text("controls.start"), "Start")
    }

    func testPhaseLabelsSwitchInBothDirectionsWithoutChangingSessionState() throws {
        let phases: [ConversationPhase] = [
            .disconnected, .connecting, .recording, .transcribing,
            .thinking, .speaking, .failed,
        ]
        var state = ConversationState()
        for phase in phases {
            state.phase = phase
            for language in ["pl", "en", "pl"] {
                XCTAssertTrue(L10n.configure(language: language, resourceRoot: resourceRoot))
                let catalog = try LocalizationCatalog.load(language: language, resourceRoot: resourceRoot)
                XCTAssertEqual(state.phase.displayName, catalog.text("phase.\(phase.rawValue)"))
                XCTAssertEqual(state.phase, phase, "Language changes must not require a session transition")
            }
        }
        XCTAssertTrue(L10n.configure(language: "en", resourceRoot: resourceRoot))
        XCTAssertEqual(ConversationPhase.disconnected.displayName, "Disconnected")
    }

    private func placeholders(_ value: String) -> [String] {
        let expression = try! NSRegularExpression(
            pattern: #"%(?:\d+\$)?[-+0# ]*(?:\d+|\*)?(?:\.\d+)?(?:ll)?[a-zA-Z@]"#
        )
        let range = NSRange(value.startIndex..., in: value)
        return expression.matches(in: value, range: range).compactMap {
            Range($0.range, in: value).map { String(value[$0]) }
        }
    }
}
