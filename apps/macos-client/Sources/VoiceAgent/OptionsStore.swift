import Foundation
import VoiceAgentCore

@MainActor
final class OptionsStore: ObservableObject {
    @Published var draft: BackendConfigSnapshot?
    @Published var accessKey = ConfigKeychain.load()
    @Published var secretValues: [String: String] = [:]
    @Published var secretsToClear: Set<String> = []
    @Published private(set) var isWorking = false
    @Published private(set) var error: String?
    @Published private(set) var notice: String?
    @Published private(set) var restartRequired = false
    @Published private(set) var restartCapable = false
    @Published private(set) var restartFields: [String] = []

    private var original: BackendConfigSnapshot?

    func refresh(endpoint: String) async {
        await perform {
            try ConfigKeychain.save(accessKey)
            let snapshot = try await client(endpoint).fetch()
            original = snapshot
            draft = snapshot
            applyStatus(snapshot)
            notice = L10n.text("options.loaded")
        }
    }

    func save(endpoint: String) async {
        await perform {
            try ConfigKeychain.save(accessKey)
            guard var candidate = draft, let baseline = original else {
                throw OptionsStoreError.notLoaded
            }
            let api = client(endpoint)
            if candidate.settingsChanges != baseline.settingsChanges {
                candidate = try await api.patch(BackendConfigPatch(
                    document: "settings",
                    expectedRevision: baseline.settingsRevision,
                    changes: candidate.settingsChanges
                ))
                // The settings response contains the old models document. Keep
                // the user's edited model draft until it is patched below.
                candidate.models = draft?.models ?? candidate.models
            }
            if candidate.modelsChanges != baseline.modelsChanges {
                candidate = try await api.patch(BackendConfigPatch(
                    document: "models",
                    expectedRevision: baseline.modelsRevision,
                    changes: candidate.modelsChanges
                ))
            }
            for name in candidate.secrets.keys.sorted() {
                if secretsToClear.contains(name) {
                    candidate = try await api.updateSecret(name: name, value: nil)
                } else if let value = secretValues[name], !value.isEmpty {
                    candidate = try await api.updateSecret(name: name, value: value)
                }
            }
            let result = try await api.reload()
            let refreshed = try await api.fetch()
            original = refreshed
            draft = refreshed
            applyStatus(refreshed)
            secretValues = [:]
            secretsToClear = []
            if result["status"]?.stringValue == "restart_required" {
                notice = L10n.text("options.saved_restart_required")
            } else {
                notice = L10n.text("options.saved_applied")
            }
        }
    }

    func restartBackend(endpoint: String) async {
        await perform {
            try ConfigKeychain.save(accessKey)
            let api = client(endpoint)
            let response = try await api.restart()
            guard response["status"]?.stringValue == "restarting",
                  let previousInstanceID = response["instance_id"]?.stringValue else {
                throw OptionsStoreError.restartNotStarted
            }
            notice = L10n.text("options.restarting")
            for _ in 0..<120 {
                try await Task.sleep(nanoseconds: 500_000_000)
                guard let health = try? await api.health(),
                      health.status == "ok",
                      health.instanceID != nil,
                      health.instanceID != previousInstanceID else { continue }
                let snapshot = try await api.fetch()
                original = snapshot
                draft = snapshot
                applyStatus(snapshot)
                notice = L10n.text("options.restart_complete", health.version)
                return
            }
            throw OptionsStoreError.restartTimedOut
        }
    }

    func test(endpoint: String, target: String) async {
        await perform {
            try ConfigKeychain.save(accessKey)
            _ = try await client(endpoint).test(target: target)
            notice = L10n.text("options.test_passed", target)
        }
    }

    private func client(_ endpoint: String) -> BackendConfigAPIClient {
        BackendConfigAPIClient(webSocketEndpoint: endpoint, accessKey: accessKey)
    }

    private func applyStatus(_ snapshot: BackendConfigSnapshot) {
        restartRequired = snapshot.application?.status == "restart_required"
        restartCapable = snapshot.application?.controlledRestart == true
        restartFields = snapshot.application?.restartFields ?? []
    }

    private func perform(_ work: () async throws -> Void) async {
        guard !isWorking else { return }
        isWorking = true
        error = nil
        notice = nil
        do { try await work() }
        catch { self.error = error.localizedDescription }
        isWorking = false
    }
}

private enum OptionsStoreError: LocalizedError {
    case notLoaded
    case restartNotStarted
    case restartTimedOut

    var errorDescription: String? {
        switch self {
        case .notLoaded: return L10n.text("options.error.not_loaded")
        case .restartNotStarted: return L10n.text("options.error.restart_not_started")
        case .restartTimedOut: return L10n.text("options.error.restart_timeout")
        }
    }
}
