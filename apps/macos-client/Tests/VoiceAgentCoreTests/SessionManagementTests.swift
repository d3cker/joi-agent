import XCTest
@testable import VoiceAgentCore

final class SessionManagementTests: XCTestCase {
    func testRESTBaseURLIsDerivedFromWebSocketEndpoint() throws {
        XCTAssertEqual(
            try SessionEndpoint.baseURL(from: "ws://192.168.30.215:8765/ws").absoluteString,
            "http://192.168.30.215:8765"
        )
        XCTAssertEqual(
            try SessionEndpoint.baseURL(from: "wss://agent.local/api/ws?session_id=old").absoluteString,
            "https://agent.local/api"
        )
    }

    func testInvalidSessionEndpointIsRejected() {
        XCTAssertThrowsError(try SessionEndpoint.baseURL(from: "http://agent.local/ws"))
        XCTAssertThrowsError(try SessionEndpoint.baseURL(from: "not a URL"))
    }

    func testClientAuthorizationUsesBearerHeader() throws {
        let request = TransportSecurityPolicy.authorizedRequest(
            url: try XCTUnwrap(URL(string: "https://192.168.30.215:8765/sessions")),
            clientAccessKey: "client-secret"
        )
        XCTAssertEqual(request.value(forHTTPHeaderField: "Authorization"), "Bearer client-secret")
    }

    func testSessionAndToolActivityDecodeFromBackendContract() throws {
        let payload = #"""
        {
          "id":"session-1","title":"Plan projektu",
          "created_at":"2026-09-05T10:00:00.000+00:00",
          "updated_at":"2026-09-05T10:01:00.000+00:00",
          "reasoning_effort":"high",
          "messages":[{"role":"user","text":"Sprawdź pliki"}],
          "tool_activity":[{
            "call_id":"call-1","response_id":"response-1","name":"grep",
            "arguments":"{\"query\":\"TODO\"}","step":1,"status":"completed",
            "started_at":"2026-09-05T10:00:05.000+00:00",
            "finished_at":"2026-09-05T10:00:06.000+00:00","error":null
          }]
        }
        """#
        let detail = try JSONDecoder().decode(SessionDetail.self, from: Data(payload.utf8))
        XCTAssertEqual(detail.title, "Plan projektu")
        XCTAssertEqual(detail.messages.first?.historyItem.text, "Sprawdź pliki")
        XCTAssertEqual(detail.toolActivity.first?.name, "grep")
        XCTAssertEqual(detail.toolActivity.first?.status, "completed")
    }


    func testCurrentClientDecodesPre041SessionResponsesWithoutOptionalFields() throws {
        let listPayload = #"""
        {"id":"legacy","title":"Old session","created_at":"2026-09-05T10:00:00.000+00:00","updated_at":"2026-09-05T10:01:00.000+00:00","reasoning_effort":"low","message_count":4}
        """#
        let summary = try JSONDecoder().decode(SessionSummary.self, from: Data(listPayload.utf8))
        XCTAssertNil(summary.lastMessage)

        let detailPayload = #"""
        {"id":"legacy","title":"Old session","created_at":"2026-09-05T10:00:00.000+00:00","updated_at":"2026-09-05T10:01:00.000+00:00","reasoning_effort":"low","messages":[{"role":"assistant","text":"Gotowe"}]}
        """#
        let detail = try JSONDecoder().decode(SessionDetail.self, from: Data(detailPayload.utf8))
        XCTAssertEqual(detail.messages.first?.text, "Gotowe")
        XCTAssertTrue(detail.toolActivity.isEmpty)
    }
}
