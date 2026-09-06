import Foundation
import XCTest
@testable import VoiceAgentCore

final class BackendConfigurationTests: XCTestCase {
    func testSnapshotDecodesAndBuildsSafePatchDocuments() throws {
        let snapshot = try JSONDecoder().decode(BackendConfigSnapshot.self, from: Data(sample.utf8))
        XCTAssertEqual(snapshot.settingsRevision, 4)
        XCTAssertEqual(snapshot.modelsRevision, 7)
        XCTAssertEqual(snapshot.activeModelProfile?["model"]?.stringValue, "deepseek-v4")
        XCTAssertEqual(
            snapshot.activeModelProfile?["reasoning_levels"]?.stringArrayValue,
            ["none", "low", "high", "max"]
        )
        XCTAssertNil(snapshot.settingsChanges["revision"])
        XCTAssertNil(snapshot.settingsChanges["schema_version"])
        XCTAssertNotNil(snapshot.settingsChanges["stt"])
        XCTAssertNil(snapshot.modelsChanges["revision"])
        XCTAssertEqual(snapshot.application?.status, "restart_required")
        XCTAssertEqual(snapshot.application?.restartFields, ["llm_model"])
        XCTAssertTrue(snapshot.application?.controlledRestart == true)
    }

    func testPatchUsesOptimisticRevisionAndSnakeCaseWireKey() throws {
        let patch = BackendConfigPatch(
            document: "models",
            expectedRevision: 9,
            changes: ["active_profile": .string("default")]
        )
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(patch)) as? [String: Any]
        )
        XCTAssertEqual(object["document"] as? String, "models")
        XCTAssertEqual(object["expected_revision"] as? Int, 9)
        XCTAssertNil(object["expectedRevision"])
    }

    private let sample = #"""
    {
      "schema_version":1,
      "config_root":"/home/user/.config/joi",
      "data_root":"/home/user/.local/share/joi",
      "settings":{"schema_version":1,"revision":4,"stt":{"mode":"real"}},
      "models":{"schema_version":1,"revision":7,"active_profile":"default","profiles":[{"id":"default","model":"deepseek-v4","reasoning_levels":["none","low","high","max"]}]},
      "secrets":{"llm_api_key":{"configured":false}},
      "application":{"status":"restart_required","changed_fields":["llm_model"],"restart_fields":["llm_model"],"controlled_restart":true,"instance_id":"instance-a"}
    }
    """#
}
