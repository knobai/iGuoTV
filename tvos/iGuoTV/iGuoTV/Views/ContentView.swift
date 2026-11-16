import SwiftUI

struct ContentView: View {
    @EnvironmentObject private var appState: AppState

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            if !appState.initialized {
                launchScreen
            } else {
                // 用 UITabBarController 保持所有标签页存活：切标签不销毁视图，
                // @State（滚动位置、已加载页数、选中分类/榜单）都保留，切回来秒开。
                // SwiftUI 的 TabView 切走时会销毁视图，故改用本容器。
                KeepAliveTabView(appState: appState)
                    .ignoresSafeArea()
            }
        }
        .task {
            await appState.bootstrap()
        }
    }

    /// 启动页：品牌标识 + 核心启动状态；失败时给出重试入口。
    private var launchScreen: some View {
        VStack(spacing: 28) {
            ZStack {
                RoundedRectangle(cornerRadius: 26, style: .continuous)
                    .fill(TVTheme.accentGradient)
                    .frame(width: 104, height: 104)
                    .shadow(color: TVTheme.accent.opacity(0.35), radius: 30, y: 12)
                Image(systemName: "play.fill")
                    .font(.system(size: 46, weight: .bold))
                    .foregroundColor(.white)
            }
            Text("iGuoTV")
                .font(.system(size: 48, weight: .bold))
            Group {
                if let error = appState.initializeError {
                    VStack(spacing: 22) {
                        Text(error)
                            .font(.system(size: 22))
                            .foregroundColor(TVTheme.textSecondary)
                            .multilineTextAlignment(.center)
                            .frame(maxWidth: 640)
                        TVPrimaryButton(title: "重试", icon: "arrow.clockwise") {
                            Task { await appState.bootstrap() }
                        }
                    }
                } else {
                    ProgressView("正在启动本地核心…")
                }
            }
            .frame(height: 130)
        }
        .padding(48)
    }
}

/// 保持标签页存活的容器：UIKit 的 UITabBarController 切标签不销毁子页面，
/// 每个标签页包一层 UIHostingController，SwiftUI 视图的 @State 在切标签时保留。
struct KeepAliveTabView: UIViewControllerRepresentable {
    var appState: AppState
    @State private var homePath = NavigationPath()

    func makeCoordinator() -> Coordinator {
        Coordinator { homePath = NavigationPath() }
    }

    func makeUIViewController(context: Context) -> UITabBarController {
        let tabBarController = UITabBarController()
        tabBarController.delegate = context.coordinator
        tabBarController.viewControllers = [
            hosting(HistoryView(path: $homePath).environmentObject(appState), title: "首页", image: "house", tag: 3),
            hosting(HomeView().environmentObject(appState), title: "剧场", image: "theatermasks", tag: 0),
            hosting(SettingsView().environmentObject(appState), title: "设置", image: "gearshape", tag: 4),
        ]
        return tabBarController
    }

    func updateUIViewController(_ uiViewController: UITabBarController, context: Context) {
        // 子页面只在 makeUIViewController 创建一次，此处不重建，避免丢状态。
    }

    final class Coordinator: NSObject, UITabBarControllerDelegate {
        private let returnHome: () -> Void

        init(returnHome: @escaping () -> Void) {
            self.returnHome = returnHome
        }

        func tabBarController(_ tabBarController: UITabBarController, didSelect viewController: UIViewController) {
            if viewController.tabBarItem.tag == 3 { returnHome() }
        }
    }

    private func hosting<V: View>(_ content: V, title: String, image: String, tag: Int) -> UIViewController {
        let controller = UIHostingController(rootView: content)
        controller.view.backgroundColor = .black
        controller.tabBarItem = UITabBarItem(title: title, image: UIImage(systemName: image), tag: tag)
        return controller
    }
}
