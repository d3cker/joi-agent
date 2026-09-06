import Foundation
import XCTest
@testable import VoiceAgentCore

final class ConnectionProfileTests: XCTestCase {
    func profile(endpoint: String = "wss://192.0.2.15:8765/ws", version: Int = 1) throws -> ConnectionProfile {
        let doc: [String: Any] = ["schema_version": version, "websocket_endpoint": endpoint,
            "ca_certificate_pem": "-----BEGIN CERTIFICATE-----\nYWJj\n-----END CERTIFICATE-----",
            "ca_sha256": String(repeating: "a", count: 64), "client_api_key": String(repeating: "c", count: 43),
            "config_api_key": String(repeating: "d", count: 43)]
        return try JSONDecoder().decode(ConnectionProfile.self, from: JSONSerialization.data(withJSONObject: doc))
    }

    func testInstallationProfile() throws {
        let value = try profile()
        XCTAssertNoThrow(try value.validate())
        XCTAssertEqual(value.caCertificateDER, Data("abc".utf8))
    }

    func testRejectsCleartextCredentialsInURLAndUnknownSchema() throws {
        for endpoint in ["ws://127.0.0.1:8765/ws", "wss://user:secret@192.0.2.15/ws", "wss://192.0.2.15/ws?token=secret"] {
            XCTAssertThrowsError(try profile(endpoint: endpoint).validate())
        }
        XCTAssertThrowsError(try profile(version: 2).validate())
    }
}
