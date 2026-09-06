import Foundation

public enum CapturePermission: Equatable, Sendable {
    case allowed
    case denied
}

public enum AudioRoutingPlan: Equatable, Sendable {
    case blockedPermission
    case blockedNoSystemInput
    case systemDefault(deviceUID: String)
    case systemDefaultFallback(deviceUID: String, requestedUID: String)
}

/// AVAudioEngine follows the system default input. Explicit device selection is
/// intentionally represented as a settings fallback; no AudioUnit property is
/// written on VoiceProcessingIO or AVAudioEngine's private input unit.
public enum AudioCapturePolicy {
    public static func plan(
        permission: CapturePermission,
        systemDefaultUID: String?,
        requestedUID: String?
    ) -> AudioRoutingPlan {
        guard permission == .allowed else { return .blockedPermission }
        guard let systemDefaultUID, !systemDefaultUID.isEmpty else {
            return .blockedNoSystemInput
        }
        guard let requestedUID, !requestedUID.isEmpty,
              requestedUID != systemDefaultUID else {
            return .systemDefault(deviceUID: systemDefaultUID)
        }
        return .systemDefaultFallback(
            deviceUID: systemDefaultUID,
            requestedUID: requestedUID
        )
    }
}
