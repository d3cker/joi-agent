import Foundation

public enum TransportSecurityPolicy {
    public static func isLoopbackHost(_ host: String) -> Bool {
        let normalized = host
            .trimmingCharacters(in: CharacterSet(charactersIn: "[]"))
            .lowercased()
        if normalized == "localhost" || normalized == "::1" { return true }
        let parts = normalized.split(separator: ".", omittingEmptySubsequences: false)
        return parts.count == 4 && parts.first == "127"
            && parts.allSatisfy { Int($0).map { (0...255).contains($0) } == true }
    }

    public static func allowsWebSocketEndpoint(_ endpoint: String) -> Bool {
        guard let components = URLComponents(string: endpoint),
              let scheme = components.scheme?.lowercased(),
              let host = components.host else { return false }
        return scheme == "wss" || (scheme == "ws" && isLoopbackHost(host))
    }

    public static func upgradingRemoteCleartextEndpoint(_ endpoint: String) -> String {
        guard var components = URLComponents(string: endpoint),
              components.scheme?.lowercased() == "ws",
              let host = components.host,
              !isLoopbackHost(host) else { return endpoint }
        components.scheme = "wss"
        return components.string ?? endpoint
    }

    public static func authorizedRequest(url: URL, clientAccessKey: String) -> URLRequest {
        var request = URLRequest(url: url)
        request.setValue("Bearer \(clientAccessKey)", forHTTPHeaderField: "Authorization")
        return request
    }
}
