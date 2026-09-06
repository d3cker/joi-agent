import Foundation
import VoiceAgentCore

struct BackendConfigAPIClient {
    let webSocketEndpoint: String
    let accessKey: String

    func fetch() async throws -> BackendConfigSnapshot {
        try await request(path: "config", method: "GET", body: Optional<Data>.none)
    }

    func patch(_ patch: BackendConfigPatch) async throws -> BackendConfigSnapshot {
        try await request(
            path: "config", method: "PATCH", body: try JSONEncoder().encode(patch)
        )
    }

    func updateSecret(name: String, value: String?) async throws -> BackendConfigSnapshot {
        try await request(
            path: "config/secrets/\(name)",
            method: "PUT",
            body: try JSONEncoder().encode(BackendSecretUpdate(value: value))
        )
    }

    func reload() async throws -> [String: JSONValue] {
        try await request(path: "config/reload", method: "POST", body: Data("{}".utf8))
    }

    func restart() async throws -> [String: JSONValue] {
        try await request(path: "config/restart", method: "POST", body: Data("{}".utf8))
    }

    func health() async throws -> BackendHealth {
        try await request(
            path: "health",
            method: "GET",
            body: Optional<Data>.none,
            requiresAccessKey: false,
            timeout: 2
        )
    }

    func test(target: String) async throws -> [String: JSONValue] {
        try await request(path: "config/test/\(target)", method: "POST", body: Data("{}".utf8))
    }

    private func request<T: Decodable>(
        path: String,
        method: String,
        body: Data?,
        requiresAccessKey: Bool = true,
        timeout: TimeInterval = 20
    ) async throws -> T {
        guard !requiresAccessKey
                || !accessKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BackendConfigClientError.missingAccessKey
        }
        let base: URL
        do {
            base = try SessionEndpoint.baseURL(from: webSocketEndpoint)
        } catch {
            throw BackendConfigClientError.invalidEndpoint
        }
        let url = path.split(separator: "/").reduce(base) {
            $0.appendingPathComponent(String($1))
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if requiresAccessKey {
            request.setValue(accessKey, forHTTPHeaderField: "X-JOI-Config-Key")
        }
        request.httpBody = body
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw BackendConfigClientError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONDecoder().decode(ErrorEnvelope.self, from: data).detail)
                ?? String(data: data, encoding: .utf8)
                ?? L10n.text("error.session.unknown")
            throw BackendConfigClientError.backend(status: http.statusCode, detail: detail)
        }
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw BackendConfigClientError.decode(error.localizedDescription)
        }
    }
}

private struct ErrorEnvelope: Decodable { let detail: String }

enum BackendConfigClientError: LocalizedError {
    case missingAccessKey
    case invalidEndpoint
    case invalidResponse
    case backend(status: Int, detail: String)
    case decode(String)

    var errorDescription: String? {
        switch self {
        case .missingAccessKey: return L10n.text("options.error.missing_key")
        case .invalidEndpoint: return L10n.text("options.error.invalid_endpoint")
        case .invalidResponse: return L10n.text("options.error.invalid_response")
        case .backend(let status, let detail):
            return L10n.text("options.error.backend", Int64(status), detail)
        case .decode(let detail): return L10n.text("options.error.decode", detail)
        }
    }
}
