import Foundation
import Security
import VoiceAgentCore

enum ConfigKeychain {
    private static let service = "local.voiceagent.macos"
    private static let configAccount = "joi-config-api-key"
    private static let clientAccount = "joi-client-api-key"

    static func load() -> String {
        load(account: configAccount)
    }

    static func loadClientAccessKey() -> String {
        load(account: clientAccount)
    }

    private static func load(account: String) -> String {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let value = String(data: data, encoding: .utf8) else { return "" }
        return value
    }

    static func save(_ value: String) throws {
        try save(value, account: configAccount)
    }

    static func saveClientAccessKey(_ value: String) throws {
        try save(value, account: clientAccount)
    }

    private static func save(_ value: String, account: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemUpdate(
            query as CFDictionary,
            [kSecValueData as String: data] as CFDictionary
        )
        if status == errSecItemNotFound {
            var item = query
            item[kSecValueData as String] = data
            let added = SecItemAdd(item as CFDictionary, nil)
            guard added == errSecSuccess else { throw ConfigKeychainError.status(added) }
        } else if status != errSecSuccess {
            throw ConfigKeychainError.status(status)
        }
    }
}

private enum ConfigKeychainError: LocalizedError {
    case status(OSStatus)

    var errorDescription: String? {
        switch self {
        case .status(let status):
            return L10n.text("options.error.keychain", Int64(status))
        }
    }
}
