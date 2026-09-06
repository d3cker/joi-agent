import XCTest
@testable import VoiceAgentCore

final class AudioCapturePolicyTests: XCTestCase {
    func testSystemDefaultCaptureNeedsNoExplicitAudioUnitProperty() {
        XCTAssertEqual(
            AudioCapturePolicy.plan(
                permission: .allowed,
                systemDefaultUID: "built-in",
                requestedUID: nil
            ),
            .systemDefault(deviceUID: "built-in")
        )
    }

    func testUnsupportedExplicitSelectionFallsBackToSystemDefault() {
        XCTAssertEqual(
            AudioCapturePolicy.plan(
                permission: .allowed,
                systemDefaultUID: "built-in",
                requestedUID: "aggregate-device"
            ),
            .systemDefaultFallback(
                deviceUID: "built-in",
                requestedUID: "aggregate-device"
            )
        )
    }

    func testDeniedPermissionIsNotReportedAsRoutingFailure() {
        XCTAssertEqual(
            AudioCapturePolicy.plan(
                permission: .denied,
                systemDefaultUID: "built-in",
                requestedUID: nil
            ),
            .blockedPermission
        )
    }

    func testMissingSystemInputIsDistinctFromPermissionFailure() {
        XCTAssertEqual(
            AudioCapturePolicy.plan(
                permission: .allowed,
                systemDefaultUID: nil,
                requestedUID: nil
            ),
            .blockedNoSystemInput
        )
    }
}
