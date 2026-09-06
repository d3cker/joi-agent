import CryptoKit
import Foundation
import Security
import VoiceAgentCore

enum ConnectionImport {
    /// Run under the app's own signature so Keychain items belong to the real client.
    /// This is an explicit administrator operation, never triggered by network data.
    static func runIfRequested() {
        let args = CommandLine.arguments
        guard args.count > 1, args[1] == "--import-connection" else { return }
        do {
            guard args.count == 3 else { throw ImportError.invalidArguments }
            let profileURL = URL(fileURLWithPath: args[2])
            let profile = try JSONDecoder().decode(ConnectionProfile.self, from: Data(contentsOf: profileURL))
            try profile.validate()
            guard let der = profile.caCertificateDER,
                  SecCertificateCreateWithData(nil, der as CFData) != nil,
                  SHA256.hash(data: der).map({ String(format: "%02x", $0) }).joined() == profile.caSHA256.lowercased()
            else { throw ImportError.invalidCertificate }
            let settingsStore = ClientSettingsStore()
            let caURL = settingsStore.rootURL.appendingPathComponent("security/backend-ca.crt")
            try FileManager.default.createDirectory(at: caURL.deletingLastPathComponent(), withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700])
            try Data(profile.caCertificatePEM.utf8).write(to: caURL, options: .atomic)
            // User trust, NOT global system trust; macOS may request confirmation.
            let trust = Process()
            trust.executableURL = URL(fileURLWithPath: "/usr/bin/security")
            trust.arguments = ["add-trusted-cert", "-r", "trustRoot", "-p", "ssl", "-k",
                FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Keychains/login.keychain-db").path,
                caURL.path]
            try trust.run()
            trust.waitUntilExit()
            guard trust.terminationStatus == 0 else { throw ImportError.trustDenied }
            var settings = try settingsStore.loadOrMigrate()
            settings.webSocketEndpoint = profile.webSocketEndpoint
            let previousClient = ConfigKeychain.loadClientAccessKey()
            let previousAdmin = ConfigKeychain.load()
            do {
                try ConfigKeychain.saveClientAccessKey(profile.clientAPIKey)
                try ConfigKeychain.save(profile.configAPIKey)
                try settingsStore.save(settings)
            } catch {
                try? ConfigKeychain.saveClientAccessKey(previousClient)
                try? ConfigKeychain.save(previousAdmin)
                throw error
            }
            print("Client configured. Restart VoiceAgent if it is already running. Private keys were stored in Keychain, not settings.json.")
            exit(0)
        } catch DecodingError.keyNotFound(let key, _) {
            fputs("Connection profile is missing field '\(key.stringValue)'. Generate a new profile with the Joi installer.\n", stderr)
            exit(1)
        } catch {
            // Deliberately omit raw profile content and decoding debug output.
            fputs("Connection import failed: \(error.localizedDescription)\n", stderr)
            exit(1)
        }
    }

    private enum ImportError: LocalizedError {
        case invalidArguments, invalidCertificate, trustDenied
        var errorDescription: String? {
            switch self {
            case .invalidArguments: return "Usage: VoiceAgent --import-connection <private-profile.json>"
            case .invalidCertificate: return "Invalid CA certificate or fingerprint mismatch."
            case .trustDenied: return "macOS did not approve the CA trust operation; configuration was not changed."
            }
        }
    }
}
