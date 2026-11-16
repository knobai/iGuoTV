import SwiftUI

@main
struct IGuoTVApp: App {
    @StateObject private var appState = AppState()
    @StateObject private var store = Store()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appState)
                .environmentObject(store)
        }
    }
}
