import Foundation

/// Installation handoff. Contains secrets: never include it in logs or diagnostics.
public struct ConnectionProfile: Decodable {
    public let schemaVersion: Int
    public let webSocketEndpoint: String
    public let caCertificatePEM: String
    public let caSHA256: String
    public let clientAPIKey: String
    public let configAPIKey: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case webSocketEndpoint = "websocket_endpoint"
        case caCertificatePEM = "ca_certificate_pem"
        case caSHA256 = "ca_sha256"
        case clientAPIKey = "client_api_key"
        case configAPIKey = "config_api_key"
    }

    public func validate() throws {
        guard schemaVersion == 1,
              let url = URL(string: webSocketEndpoint), url.scheme == "wss",
              url.host != nil, url.path == "/ws", url.user == nil, url.password == nil,
              url.query == nil, url.fragment == nil,
              clientAPIKey.count >= 32, configAPIKey.count >= 32,
              caSHA256.count == 64, caSHA256.allSatisfy({ $0.isHexDigit }),
              caCertificateDER != nil else {
            throw ClientSettingsError.invalidEndpoint("Invalid installation connection profile")
        }
    }

    public var caCertificateDER: Data? {
        let payload = caCertificatePEM
            .replacingOccurrences(of: "-----BEGIN CERTIFICATE-----", with: "")
            .replacingOccurrences(of: "-----END CERTIFICATE-----", with: "")
            .filter { !$0.isWhitespace }
        return Data(base64Encoded: payload)
    }
}
