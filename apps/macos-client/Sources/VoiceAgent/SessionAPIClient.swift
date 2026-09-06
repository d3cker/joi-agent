import Foundation
import VoiceAgentCore

enum SessionAPIError: LocalizedError {
    case invalidResponse
    case server(status: Int, message: String)

    var errorDescription: String? {
        switch self {
        case .invalidResponse:
            return L10n.text("error.session.invalid_response")
        case .server(let status, let message):
            return L10n.text("error.session.backend", status, message)
        }
    }
}

struct SessionAPIClient {
    private struct SessionListResponse: Decodable { let sessions: [SessionSummary] }
    private struct CreateResponse: Decodable { let id: String }
    private struct ErrorResponse: Decodable { let detail: String? }

    let webSocketEndpoint: String
    let clientAccessKey: String
    var urlSession: URLSession = .shared

    func list() async throws -> [SessionSummary] {
        let (data, _) = try await request(path: "sessions")
        return try decode(SessionListResponse.self, from: data).sessions
    }

    func detail(id: String) async throws -> SessionDetail {
        let (data, _) = try await request(path: "sessions/\(escaped(id))")
        return try decode(SessionDetail.self, from: data)
    }

    func create() async throws -> String {
        let identifier = UUID().uuidString.lowercased()
        let body = try JSONSerialization.data(withJSONObject: ["session_id": identifier])
        let (data, _) = try await request(path: "sessions", method: "POST", body: body)
        return try decode(CreateResponse.self, from: data).id
    }

    func rename(id: String, title: String) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["title": title])
        _ = try await request(path: "sessions/\(escaped(id))", method: "PATCH", body: body)
    }

    func delete(id: String) async throws {
        _ = try await request(path: "sessions/\(escaped(id))", method: "DELETE")
    }

    private func request(
        path: String,
        method: String = "GET",
        body: Data? = nil
    ) async throws -> (Data, HTTPURLResponse) {
        let base = try SessionEndpoint.baseURL(from: webSocketEndpoint)
        let url = base.appendingPathComponent(path)
        var request = URLRequest(url: url)
        request.setValue("Bearer \(clientAccessKey)", forHTTPHeaderField: "Authorization")
        request.httpMethod = method
        request.httpBody = body
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        request.timeoutInterval = 8
        let (data, response) = try await urlSession.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw SessionAPIError.invalidResponse
        }
        guard (200..<300).contains(http.statusCode) else {
            let decoded = try? JSONDecoder().decode(ErrorResponse.self, from: data)
            let fallback = String(data: data, encoding: .utf8)
                ?? L10n.text("error.session.unknown")
            throw SessionAPIError.server(status: http.statusCode, message: decoded?.detail ?? fallback)
        }
        return (data, http)
    }

    private func escaped(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? value
    }

    private func decode<Value: Decodable>(_ type: Value.Type, from data: Data) throws -> Value {
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch let DecodingError.keyNotFound(key, context) {
            throw SessionAPIError.server(
                status: 200,
                message: L10n.text(
                    "error.session.missing_field",
                    key.stringValue,
                    context.codingPath.map(\.stringValue).joined(separator: ".")
                )
            )
        } catch {
            throw SessionAPIError.server(
                status: 200,
                message: L10n.text("error.session.decode", error.localizedDescription)
            )
        }
    }
}
