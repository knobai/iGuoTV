import SwiftUI

/// 首页：收藏剧集在前，其余按最近观看排序。
/// 卡片展示海报、看到第几集与进度条；点海报进详情页续播。
struct HistoryView: View {
    @EnvironmentObject private var store: Store
    @Binding var path: NavigationPath
    /// 待确认删除的观看记录（按遥控器播放/暂停键时设置）。
    @State private var pendingDelete: WatchProgress?

    var body: some View {
        NavigationStack(path: $path) {
            ScrollView {
                Group {
                    if store.homeDramas.isEmpty {
                        TVStatusView(
                            icon: "bookmark",
                            title: "还没有剧集",
                            message: "观看或收藏的剧集会显示在这里。",
                            expands: false
                        )
                        .frame(maxWidth: .infinity)
                    } else {
                        LazyVGrid(columns: TVTheme.posterColumns(), spacing: 32) {
                            ForEach(store.homeDramas) { drama in
                                let entry = store.latestProgress(for: drama.id)
                                TVPosterCard(
                                    drama: drama,
                                    subtitle: entry.map { subtitle(for: $0) },
                                    progress: entry.map { progressFraction(for: $0) },
                                    isPinned: store.isFollowing(drama)
                                )
                                .onPlayPauseCommand {
                                    pendingDelete = entry
                                }
                                .contextMenu {
                                    Button {
                                        store.toggleFollow(drama)
                                    } label: {
                                        Label(
                                            store.isFollowing(drama) ? "取消收藏" : "收藏",
                                            systemImage: store.isFollowing(drama) ? "bookmark.slash" : "bookmark"
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
                .padding(.horizontal, TVTheme.posterPageInset)
                .padding(.top, 20)
                .padding(.bottom, 56)
            }
            .navigationDestination(for: Drama.self) { drama in
                DetailView(drama: drama)
            }
            .navigationDestination(for: ProducerRoute.self) { route in
                ProducerView(drama: route.drama)
            }
            .confirmationDialog(
                "删除观看记录",
                isPresented: Binding(
                    get: { pendingDelete != nil },
                    set: { if !$0 { pendingDelete = nil } }
                )
            ) {
                Button("删除", role: .destructive) {
                    if let entry = pendingDelete {
                        store.deleteHistory(for: entry.drama)
                    }
                    pendingDelete = nil
                }
                Button("取消", role: .cancel) {
                    pendingDelete = nil
                }
            } message: {
                Text("确定要删除《\(pendingDelete?.drama.title ?? "")》的观看记录吗？")
            }
        }
    }

    private func progressFraction(for entry: WatchProgress) -> Double {
        guard entry.duration > 0 else { return 0 }
        return min(max(entry.position / entry.duration, 0), 1)
    }

    private func subtitle(for entry: WatchProgress) -> String {
        let episodeText = "第\(entry.episode)集"
        if entry.duration > 1 && entry.position >= entry.duration - 1 {
            return "\(episodeText) · 已看完"
        }
        return "\(episodeText) · 看到 \(tvTimecode(entry.position))"
    }
}
