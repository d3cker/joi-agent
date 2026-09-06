import AppKit
import SwiftUI
import VoiceAgentCore

/// AppKit owns the open menu, so high-frequency SwiftUI audio telemetry cannot
/// dismiss it. Items are data-driven by Resources/Localization/locales.json.
struct LanguageSelector: NSViewRepresentable {
    let languages: [InterfaceLanguageDescriptor]
    @Binding var selection: String

    func makeCoordinator() -> Coordinator { Coordinator(selection: $selection) }

    func makeNSView(context: Context) -> NSPopUpButton {
        let button = NSPopUpButton(frame: .zero, pullsDown: false)
        button.target = context.coordinator
        button.action = #selector(Coordinator.changed(_:))
        update(button)
        return button
    }

    func updateNSView(_ button: NSPopUpButton, context: Context) {
        context.coordinator.selection = $selection
        update(button)
    }

    private func update(_ button: NSPopUpButton) {
        let identifiers = button.itemArray.compactMap { $0.representedObject as? String }
        if identifiers != languages.map(\.id) {
            button.removeAllItems()
            for language in languages {
                button.addItem(withTitle: language.name)
                button.lastItem?.representedObject = language.id
            }
        }
        if let index = languages.firstIndex(where: { $0.id == selection }),
           button.indexOfSelectedItem != index {
            button.selectItem(at: index)
        }
    }

    final class Coordinator: NSObject {
        var selection: Binding<String>

        init(selection: Binding<String>) { self.selection = selection }

        @objc func changed(_ sender: NSPopUpButton) {
            guard let identifier = sender.selectedItem?.representedObject as? String else { return }
            selection.wrappedValue = identifier
        }
    }
}
