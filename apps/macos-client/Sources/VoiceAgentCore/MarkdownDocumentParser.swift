import Foundation

public struct MarkdownDocument: Equatable, Sendable {
    public let blocks: [MarkdownBlock]

    public init(blocks: [MarkdownBlock]) {
        self.blocks = blocks
    }
}

public enum MarkdownBlock: Equatable, Sendable {
    case paragraph(lines: [String])
    case heading(level: Int, text: String)
    case unorderedList(items: [String])
    case orderedList(items: [String])
    case blockquote(lines: [String])
    case code(language: String?, text: String)
    case table(headers: [String], rows: [[String]])
    case thematicBreak
}

public enum MarkdownDocumentParser {
    public static func parse(_ markdown: String) -> MarkdownDocument {
        let expandedBreaks = markdown.replacingOccurrences(
            of: #"(?i)<br\s*/?>"#,
            with: "\n",
            options: .regularExpression
        )
        let lines = expandedBreaks
            .replacingOccurrences(of: "\r\n", with: "\n")
            .replacingOccurrences(of: "\r", with: "\n")
            .components(separatedBy: "\n")
        var blocks: [MarkdownBlock] = []
        var index = 0

        while index < lines.count {
            let line = lines[index]
            if line.trimmingCharacters(in: .whitespaces).isEmpty {
                index += 1
                continue
            }

            if let fence = fencedCodeStart(line) {
                var codeLines: [String] = []
                index += 1
                while index < lines.count && !isFenceClose(lines[index]) {
                    codeLines.append(lines[index])
                    index += 1
                }
                if index < lines.count { index += 1 }
                blocks.append(.code(
                    language: fence.isEmpty ? nil : fence,
                    text: codeLines.joined(separator: "\n")
                ))
                continue
            }

            if index + 1 < lines.count,
               let headers = tableCells(line),
               isTableSeparator(lines[index + 1], expectedColumns: headers.count) {
                index += 2
                var rows: [[String]] = []
                while index < lines.count,
                      !lines[index].trimmingCharacters(in: .whitespaces).isEmpty,
                      let cells = tableCells(lines[index]) {
                    rows.append(normalize(cells, columns: headers.count))
                    index += 1
                }
                blocks.append(.table(headers: headers, rows: rows))
                continue
            }

            if let heading = heading(line) {
                blocks.append(.heading(level: heading.level, text: heading.text))
                index += 1
                continue
            }

            if isThematicBreak(line) {
                blocks.append(.thematicBreak)
                index += 1
                continue
            }

            if let first = unorderedItem(line) {
                var items = [first]
                index += 1
                while index < lines.count, let item = unorderedItem(lines[index]) {
                    items.append(item)
                    index += 1
                }
                blocks.append(.unorderedList(items: items))
                continue
            }

            if let first = orderedItem(line) {
                var items = [first]
                index += 1
                while index < lines.count, let item = orderedItem(lines[index]) {
                    items.append(item)
                    index += 1
                }
                blocks.append(.orderedList(items: items))
                continue
            }

            if let first = quoteLine(line) {
                var quoteLines = [first]
                index += 1
                while index < lines.count, let value = quoteLine(lines[index]) {
                    quoteLines.append(value)
                    index += 1
                }
                blocks.append(.blockquote(lines: quoteLines))
                continue
            }

            var paragraph = [line.trimmingCharacters(in: .whitespaces)]
            index += 1
            while index < lines.count,
                  !lines[index].trimmingCharacters(in: .whitespaces).isEmpty,
                  !startsBlock(lines, at: index) {
                paragraph.append(lines[index].trimmingCharacters(in: .whitespaces))
                index += 1
            }
            blocks.append(.paragraph(lines: paragraph))
        }
        return MarkdownDocument(blocks: blocks)
    }

    private static func startsBlock(_ lines: [String], at index: Int) -> Bool {
        let line = lines[index]
        if fencedCodeStart(line) != nil || heading(line) != nil
            || isThematicBreak(line) || unorderedItem(line) != nil
            || orderedItem(line) != nil || quoteLine(line) != nil {
            return true
        }
        return index + 1 < lines.count
            && tableCells(line) != nil
            && isTableSeparator(
                lines[index + 1],
                expectedColumns: tableCells(line)?.count ?? 0
            )
    }

    private static func fencedCodeStart(_ line: String) -> String? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("```") else { return nil }
        return String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
    }

    private static func isFenceClose(_ line: String) -> Bool {
        line.trimmingCharacters(in: .whitespaces).hasPrefix("```")
    }

    private static func heading(_ line: String) -> (level: Int, text: String)? {
        let trimmed = line.trimmingCharacters(in: .whitespaces)
        let hashes = trimmed.prefix { $0 == "#" }
        guard (1...6).contains(hashes.count) else { return nil }
        let remainder = trimmed.dropFirst(hashes.count)
        guard remainder.first?.isWhitespace == true else { return nil }
        return (
            hashes.count,
            String(remainder).trimmingCharacters(in: .whitespaces)
        )
    }

    private static func unorderedItem(_ line: String) -> String? {
        capture(line, pattern: #"^\s*[-+*]\s+(.+?)\s*$"#)
    }

    private static func orderedItem(_ line: String) -> String? {
        capture(line, pattern: #"^\s*\d+[.)]\s+(.+?)\s*$"#)
    }

    private static func quoteLine(_ line: String) -> String? {
        capture(line, pattern: #"^\s*>\s?(.*?)\s*$"#)
    }

    private static func isThematicBreak(_ line: String) -> Bool {
        line.range(
            of: #"^\s{0,3}(?:\*\s*){3,}$|^\s{0,3}(?:-\s*){3,}$|^\s{0,3}(?:_\s*){3,}$"#,
            options: .regularExpression
        ) != nil
    }

    private static func tableCells(_ line: String) -> [String]? {
        guard line.contains("|") else { return nil }
        var cells: [String] = []
        var current = ""
        var escaped = false
        var inCode = false
        for character in line {
            if escaped {
                current.append(character)
                escaped = false
                continue
            }
            if character == "\\" {
                escaped = true
                continue
            }
            if character == "`" {
                inCode.toggle()
                current.append(character)
                continue
            }
            if character == "|" && !inCode {
                cells.append(current.trimmingCharacters(in: .whitespaces))
                current = ""
            } else {
                current.append(character)
            }
        }
        if escaped { current.append("\\") }
        cells.append(current.trimmingCharacters(in: .whitespaces))
        if cells.first?.isEmpty == true { cells.removeFirst() }
        if cells.last?.isEmpty == true { cells.removeLast() }
        return cells.count >= 2 ? cells : nil
    }

    private static func isTableSeparator(
        _ line: String, expectedColumns: Int
    ) -> Bool {
        guard expectedColumns >= 2, let cells = tableCells(line),
              cells.count == expectedColumns else { return false }
        return cells.allSatisfy { cell in
            cell.range(
                of: #"^:?-{3,}:?$"#,
                options: .regularExpression
            ) != nil
        }
    }

    private static func normalize(_ cells: [String], columns: Int) -> [String] {
        if cells.count == columns { return cells }
        if cells.count > columns { return Array(cells.prefix(columns)) }
        return cells + Array(repeating: "", count: columns - cells.count)
    }

    private static func capture(_ line: String, pattern: String) -> String? {
        guard let expression = try? NSRegularExpression(pattern: pattern),
              let match = expression.firstMatch(
                in: line,
                range: NSRange(line.startIndex..., in: line)
              ),
              match.numberOfRanges > 1,
              let range = Range(match.range(at: 1), in: line) else { return nil }
        return String(line[range])
    }
}

public enum MarkdownTableLayout {
    /// Deterministic column widths keep SwiftUI from measuring a wrapped cell
    /// at one width and drawing the row border at another.  Wide prose columns
    /// remain readable and the whole table scrolls horizontally when needed.
    public static func columnWidths(
        headers: [String], rows: [[String]]
    ) -> [Double] {
        headers.indices.map { column in
            let values = [headers[column]] + rows.map { row in
                column < row.count ? row[column] : ""
            }
            let longestLine = values
                .flatMap { $0.components(separatedBy: "\n") }
                .map(\.count)
                .max() ?? 0
            return min(420, max(120, Double(longestLine) * 7.2 + 28))
        }
    }
}
