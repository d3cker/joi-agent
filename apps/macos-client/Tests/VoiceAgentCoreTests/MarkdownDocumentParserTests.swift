import XCTest
@testable import VoiceAgentCore

final class MarkdownDocumentParserTests: XCTestCase {
    func testParsesTableAsRowsAndColumns() {
        let document = MarkdownDocumentParser.parse("""
        | **Model** | Wynik | Uwagi |
        |:----------|------:|:------|
        | Higgs | 8.5 | Działa |
        | DeepSeek | 53 t/s | `content` only |
        """)

        XCTAssertEqual(document.blocks, [
            .table(
                headers: ["**Model**", "Wynik", "Uwagi"],
                rows: [
                    ["Higgs", "8.5", "Działa"],
                    ["DeepSeek", "53 t/s", "`content` only"],
                ]
            )
        ])
    }

    func testPreservesExplicitLinesAndParagraphBreaks() {
        let document = MarkdownDocumentParser.parse("""
        Pierwsza linia
        Druga linia

        Trzeci akapit<br>Czwarta linia
        """)

        XCTAssertEqual(document.blocks, [
            .paragraph(lines: ["Pierwsza linia", "Druga linia"]),
            .paragraph(lines: ["Trzeci akapit", "Czwarta linia"]),
        ])
    }

    func testParsesListsHeadingsQuotesAndCodeAsBlocks() {
        let document = MarkdownDocumentParser.parse("""
        ## Nagłówek

        - pierwszy
        - **drugi**

        > cytat

        ```swift
        let value = 1
        ```
        """)

        XCTAssertEqual(document.blocks, [
            .heading(level: 2, text: "Nagłówek"),
            .unorderedList(items: ["pierwszy", "**drugi**"]),
            .blockquote(lines: ["cytat"]),
            .code(language: "swift", text: "let value = 1"),
        ])
    }

    func testEscapedPipeDoesNotCreateAnExtraTableColumn() {
        let document = MarkdownDocumentParser.parse("""
        | Pole | Wartość |
        |---|---|
        | regex | a\\|b |
        """)
        guard case .table(_, let rows) = document.blocks.first else {
            return XCTFail("Expected a table")
        }
        XCTAssertEqual(rows, [["regex", "a|b"]])
    }

    func testTableLayoutGivesLongProseEnoughWidthAndCapsIt() {
        let widths = MarkdownTableLayout.columnWidths(
            headers: ["Slug", "Opis", "Wersja"],
            rows: [[
                "web-research",
                "Badaj aktualne fakty przez prywatny SearXNG i weryfikuj źródła.",
                "1",
            ]]
        )

        XCTAssertEqual(widths.count, 3)
        XCTAssertGreaterThan(widths[1], widths[0])
        XCTAssertEqual(widths[2], 120)
        XCTAssertLessThanOrEqual(widths[1], 420)
    }
}
