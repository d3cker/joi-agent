import Foundation
import XCTest
@testable import VoiceAgentCore

final class ClientSettingsStoreTests: XCTestCase {
    func testCleanInstallDefaultsToEnglishInStandardJOITree() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let suite = "VoiceAgentTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }

        let store = ClientSettingsStore(rootURL: root)
        let loaded = try store.loadOrMigrate(defaults: defaults)
        XCTAssertEqual(loaded.language, "en")
        XCTAssertEqual(store.settingsURL.path, root.appendingPathComponent("client/settings.json").path)
        XCTAssertEqual(
            try FileManager.default.attributesOfItem(atPath: store.settingsURL.path)[.posixPermissions] as? Int,
            0o600
        )
    }

    func testLegacyUserDefaultsMigrateToPolishWithoutBeingDeleted() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let suite = "VoiceAgentTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set("ws://agent.local:8765/ws", forKey: "voiceAgent.endpoint")
        defaults.set("high", forKey: "voiceAgent.reasoning")
        defaults.set("legacy-session", forKey: "voiceAgent.sessionID")

        let loaded = try ClientSettingsStore(rootURL: root).loadOrMigrate(defaults: defaults)
        XCTAssertEqual(loaded.language, "pl")
        XCTAssertEqual(loaded.webSocketEndpoint, "wss://agent.local:8765/ws")
        XCTAssertEqual(loaded.reasoningLevel, "high")
        XCTAssertEqual(loaded.sessionID, "legacy-session")
        XCTAssertEqual(defaults.string(forKey: "voiceAgent.sessionID"), "legacy-session")
    }

    func testRemoteCleartextIsRejectedButLoopbackRemainsAvailable() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = ClientSettingsStore(rootURL: root)
        let remote = ClientSettingsDocument(
            language: "en", webSocketEndpoint: "ws://192.168.30.215:8765/ws",
            reasoningLevel: "low", sessionID: "test", useVoiceProcessing: true,
            contextPanelVisible: false
        )
        XCTAssertThrowsError(try store.validate(remote))
        var loopback = remote
        loopback.webSocketEndpoint = "ws://127.0.0.1:8765/ws"
        XCTAssertNoThrow(try store.validate(loopback))
        loopback.webSocketEndpoint = "wss://192.168.30.215:8765/ws"
        XCTAssertNoThrow(try store.validate(loopback))
    }

    func testSaveIncrementsRevisionAndKeepsPreviousDocument() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let suite = "VoiceAgentTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = ClientSettingsStore(rootURL: root)
        var document = try store.loadOrMigrate(defaults: defaults)
        document.contextPanelVisible = true
        let saved = try store.save(document)
        XCTAssertEqual(saved.revision, 2)
        XCTAssertTrue(saved.contextPanelVisible)
        XCTAssertTrue(FileManager.default.fileExists(atPath: store.previousSettingsURL.path))
        XCTAssertEqual(try store.loadOrMigrate(defaults: defaults), saved)
    }
}
