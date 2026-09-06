import SwiftUI

@main
struct VoiceAgentApp: App {
    init() {
        ConnectionImport.runIfRequested()
    }

    @StateObject private var store = ConversationStore()

    var body: some Scene {
        WindowGroup {
            ContentView(store: store)
                .frame(minWidth: 940, minHeight: 650)
                .task { await store.runCommandLineDiagnosticIfRequested() }
        }
        .defaultSize(width: 1120, height: 760)
        .commands {
            CommandGroup(after: .newItem) {
                Button(store.text(store.isRunning
                    ? "controls.stop_conversation" : "controls.start_conversation")) {
                    store.toggle()
                }
                .keyboardShortcut("r", modifiers: [.command])
            }
        }
        Window(Text(store.text("options.title")), id: "options") {
            SettingsView(store: store)
        }
        .defaultSize(width: 900, height: 720)
    }
}
