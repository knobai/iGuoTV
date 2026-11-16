import SwiftUI

/// 首页：内容发现页。
///
/// 结构：目录浏览 —— 站源、分类、榜单筛选 + 海报网格（保留原有全部分类、
/// 无限滚动与榜单能力，切换分类时自动回到目录顶部）。
struct HomeView: View {
    @EnvironmentObject private var appState: AppState
    @State private var categories: [Category] = [.all]
    @State private var selectedCategory = ""
    @State private var items: [Drama] = []
    @State private var filterQuery = ""
    @State private var keyboardVisible = false
    @State private var posterFocusRequest = 0
    @FocusState private var filterFocused: Bool
    @FocusState private var focusedKey: String?
    @State private var page = 1
    @State private var hasMore = false
    @State private var warning = ""
    @State private var loading = false
    @State private var error: String?
    @State private var refreshing = false // 按播放/暂停键手动刷新时为 true
    @State private var categoriesSource = ""
    @State private var rankingBoards: [RankingBoard] = []
    @State private var selectedBoard = ""
    @State private var rankingBoardsSource = ""
    @State private var didApplyDefaultRanking = false
    /// 是否已应用过「红果默认展开榜单」：只应用一次，避免覆盖用户之后
    /// 手动点的分类或「全部」；视图存活期间选择一直保留。

    /// 榜单模式的哨兵分类 id：与核心分类 id 不冲突。
    private static let rankingCategoryId = "__ranking__"
    private var isRankingMode: Bool { selectedCategory == Self.rankingCategoryId }

    private var loadKey: String { appState.selectedSource + "|" + selectedCategory + "|" + selectedBoard }

    private var filteredItems: [Drama] {
        items.filter { DramaTitleFilter.matches($0.title, query: filterQuery) }
    }

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    VStack(alignment: .leading, spacing: TVTheme.sectionSpacing) {
                        catalogSection
                    }
                    .padding(.horizontal, TVTheme.posterPageInset)
                    .padding(.top, 0)
                    .padding(.bottom, 56)
                }
                // 首页按播放/暂停键：手动刷新（清空缓存、强制重拉）。
                // 挂在首页内容上（不是 NavigationStack），进详情页后不触发。
                .onPlayPauseCommand {
                    Task { await refresh() }
                }
                .onChange(of: loadKey) { _ in
                    // 切换站源 / 分类 / 榜单后回到目录区。
                    DispatchQueue.main.async {
                        proxy.scrollTo("catalog-top", anchor: .top)
                    }
                }
            }
            .navigationDestination(for: Drama.self) { drama in
                DetailView(drama: drama)
            }
            .navigationDestination(for: ProducerRoute.self) { route in
                ProducerView(drama: route.drama)
            }
            .onAppear {
                // 首次进入首页且为红果站源时，默认展开榜单，不用每次手动点。
                if !didApplyDefaultRanking, appState.selectedSource == "hongguo" {
                    didApplyDefaultRanking = true
                    selectedCategory = Self.rankingCategoryId
                }
            }
            .task(id: loadKey) {
                await reload(firstPage: true)
            }
        }
    }

    // MARK: - 目录浏览区

    private var catalogSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            if appState.allSources {
                sourceRow
            }
            categoryRow
            Button {
                keyboardVisible = true
                focusedKey = "a"
            } label: {
                HStack(spacing: 14) {
                    Image(systemName: "magnifyingglass")
                    Text(filterQuery.isEmpty ? "过滤当前剧集：输入拼音首字母，例如 cxrs" : filterQuery)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .font(.system(size: 26))
                .padding(.horizontal, 24)
                .padding(.vertical, 18)
                .foregroundColor(filterFocused ? .black : .white)
                .background(
                    RoundedRectangle(cornerRadius: 18)
                        .fill(filterFocused ? Color.white : TVTheme.surface)
                )
            }
            .buttonStyle(TVPressButtonStyle())
            .focused($filterFocused)
            .onMoveCommand { direction in
                if direction == .down, !keyboardVisible, !filteredItems.isEmpty {
                    posterFocusRequest += 1
                }
            }
            if keyboardVisible {
                inlineKeyboard
            }
            if !warning.isEmpty {
                Text(warning)
                    .font(.system(size: 19))
                    .foregroundColor(TVTheme.textTertiary)
            }
            // 海报区往下挪：单独给海报加顶部间距，只动海报、不动上面那行。
            catalogContent
                .padding(.top, 12)
        }
        .id("catalog-top")
    }

    @ViewBuilder
    private var inlineKeyboard: some View {
        if #available(tvOS 17.0, *) {
            keyboardContent
                .onKeyPress(characters: .alphanumerics) { press in
                    filterQuery += press.characters.lowercased()
                    return .handled
                }
                .onKeyPress(.delete) {
                    if !filterQuery.isEmpty { filterQuery.removeLast() }
                    return .handled
                }
        } else {
            keyboardContent
        }
    }

    private var keyboardContent: some View {
        VStack(spacing: 12) {
            HStack(spacing: 8) {
                ForEach(Array("abcdefghijklmnopqrstuvwxyz").map(String.init), id: \.self) { key in
                    keyboardKey(key)
                }
                keyboardKey("⌫")
            }
            HStack(spacing: 8) {
                ForEach(Array("0123456789").map(String.init), id: \.self) { key in
                    keyboardKey(key)
                }
                keyboardKey("空格")
                keyboardKey("清除")
                keyboardKey("完成")
            }
        }
        .padding(.vertical, 12)
        .onAppear { focusedKey = "a" }
        .onExitCommand { closeKeyboard() }
    }

    private func keyboardKey(_ key: String) -> some View {
        Button {
            switch key {
            case "⌫":
                if !filterQuery.isEmpty { filterQuery.removeLast() }
            case "空格": filterQuery += " "
            case "清除": filterQuery = ""
            case "完成": closeKeyboard()
            default: filterQuery += key
            }
        } label: {
            Text(key)
                .font(.system(size: 24, weight: .medium))
                .frame(width: key.count > 1 ? 88 : 44, height: 48)
                .foregroundColor(focusedKey == key ? .black : .white)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(focusedKey == key ? Color.white : TVTheme.surface)
                )
        }
        .buttonStyle(TVPressButtonStyle())
        .focused($focusedKey, equals: key)
        .accessibilityLabel(key == "⌫" ? "删除最后一个字符" : key)
    }

    private func closeKeyboard() {
        keyboardVisible = false
        filterFocused = true
    }

    @ViewBuilder
    private var catalogContent: some View {
        if loading && items.isEmpty {
            TVLoadingView("正在加载…")
                .padding(.vertical, 160)
        } else if let error, items.isEmpty {
            VStack(spacing: 26) {
                TVStatusView(
                    icon: "exclamationmark.triangle",
                    title: "内容加载失败",
                    message: error,
                    expands: false
                )
                TVPrimaryButton(title: "重试", icon: "arrow.clockwise") {
                    Task { await reload(firstPage: true) }
                }
            }
            .padding(.vertical, 90)
        } else if items.isEmpty {
            TVStatusView(
                icon: "sparkles",
                title: "暂无内容",
                message: "换个分类或站源看看。",
                expands: false
            )
            .padding(.vertical, 90)
        } else if filteredItems.isEmpty {
            TVStatusView(
                icon: "magnifyingglass",
                title: "没有匹配的剧集",
                message: "仅过滤当前已加载的剧集，试试其他首字母或清除输入。",
                expands: false
            )
            .padding(.vertical, 90)
        } else {
            VStack(spacing: 20) {
                TVPosterGrid(items: filteredItems, showRank: isRankingMode && filterQuery.isEmpty, focusFirstRequest: posterFocusRequest, returnToSearch: {
                    if keyboardVisible { focusedKey = "完成" }
                    else { filterFocused = true }
                }, onReachEnd: {
                    guard filterQuery.isEmpty else { return }
                    Task { await loadMore() }
                })
                if loading && !items.isEmpty {
                    TVLoadingView(refreshing ? "正在刷新…" : "正在加载更多…")
                        .padding(.vertical, 24)
                }
            }
        }
    }

    private var sourceRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                ForEach(SourceSite.available(allSources: appState.allSources)) { site in
                    TVChip(title: site.name, isSelected: site.id == appState.selectedSource) {
                        // 切到红果默认展开榜单，其他站源默认全部。
                        selectedCategory = site.id == "hongguo" ? Self.rankingCategoryId : ""
                        appState.selectedSource = site.id
                    }
                }
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 2)
        }
    }

    /// 分类 + 榜单同一行：红果站源的榜单排在最前（「全部」之前），
    /// 再点当前榜单返回「全部」；分类与榜单互斥选择。
    private var categoryRow: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 12) {
                // 榜单：仅红果站源有，排在「全部」之前；「红果榜单」标签隐藏，只展示榜单列表。
                if appState.selectedSource == "hongguo" {
                    ForEach(rankingBoards.filter { !["真人剧榜", "漫剧榜", "AI剧榜"].contains($0.name) }) { board in
                        TVChip(
                            title: board.name,
                            isSelected: isRankingMode && board.id == selectedBoard
                        ) {
                            if isRankingMode && board.id == selectedBoard {
                                selectedCategory = ""
                            } else {
                                selectedCategory = Self.rankingCategoryId
                                selectedBoard = board.id
                            }
                        }
                    }
                }
                ForEach(categories) { category in
                    TVChip(title: category.name, isSelected: category.id == selectedCategory) {
                        selectedCategory = category.id
                    }
                }
            }
            .padding(.vertical, 6)
            .padding(.horizontal, 2)
        }
    }

    // MARK: - 数据加载（业务逻辑与原版一致）

    /// 手动刷新（首页按播放/暂停键）：清空首页缓存、强制重新拉取第一页。
    private func refresh() async {
        guard !loading, !refreshing else { return }
        refreshing = true
        loading = true // 先占位，避免刷新期间触发加载更多导致数据错乱
        await CoreClient.shared.invalidateHomeCache()
        await reload(firstPage: true)
        refreshing = false
        // reload 内部已把 loading 置 false，这里不再重复设置
    }

    private func reload(firstPage: Bool) async {
        if firstPage { page = 1 }
        loading = true
        error = nil
        do {
            // 分类列表：与模式无关，始终拉取，保证行里始终有子分类。
            // 只在站源变化时拿一次（首屏/切站源/刷新时）；榜单模式下拉取失败不报错
            //（不影响看榜单），分类模式下拉取失败则报错（看不到分类内容）。
            if firstPage, categoriesSource != appState.selectedSource {
                do {
                    let fetched = try await CoreClient.shared.categories(
                        source: appState.selectedSource
                    )
                    // 核心返回的分类里可能自带「全部」，与本地的 .all 重复；
                    // 按名称去重，保留第一个（即本地的 .all）。
                    var seenNames = Set<String>()
                    categories = ([.all] + fetched).filter { seenNames.insert($0.name).inserted }
                    categoriesSource = appState.selectedSource
                } catch {
                    if !isRankingMode { throw error }
                    // 榜单模式：分类拉取失败不阻塞，只展示榜单。
                }
            }
            // 榜单列表（红果站源）：与模式无关，始终拉取，保证行里始终有子榜单。
            // 只在站源变化时拿一次（首屏/切站源/刷新时）；分类模式下拉取失败不报错
            //（不影响看分类），榜单模式下拉取失败则报错（看不到榜单内容）。
            if firstPage, appState.selectedSource == "hongguo",
               rankingBoardsSource != appState.selectedSource {
                do {
                    let boards = try await CoreClient.shared.rankingBoards()
                    rankingBoards = boards.filter { $0.source == "hongguo" }
                    rankingBoardsSource = appState.selectedSource
                } catch {
                    if isRankingMode { throw error }
                    // 分类模式：榜单拉取失败不阻塞，只展示分类。
                }
            }
            if isRankingMode {
                // 榜单模式：默认选中第一个榜。
                if selectedBoard.isEmpty
                    || !rankingBoards.contains(where: { $0.id == selectedBoard }) {
                    selectedBoard = rankingBoards.first?.id ?? ""
                }
                if selectedBoard.isEmpty {
                    items = []
                    hasMore = false
                    warning = ""
                } else {
                    let result = try await CoreClient.shared.rankings(
                        board: selectedBoard, page: page
                    )
                    if firstPage {
                        items = result.items
                    } else {
                        items += result.items
                    }
                    hasMore = result.hasMore
                    warning = result.warning
                }
            } else {
                let result = try await CoreClient.shared.catalog(
                    source: appState.selectedSource, page: page, category: selectedCategory
                )
                if firstPage {
                    items = result.items
                } else {
                    items += result.items
                }
                hasMore = result.hasMore
                warning = result.warning
            }
        } catch {
            self.error = error.localizedDescription
        }
        loading = false
    }

    private func loadMore() async {
        guard hasMore, !loading else { return }
        page += 1
        await reload(firstPage: false)
    }
}

/// 当前目录的本地剧名过滤，不发起站源搜索。
enum DramaTitleFilter {
    static func matches(_ title: String, query: String) -> Bool {
        let query = normalized(query)
        guard !query.isEmpty else { return true }
        if normalized(title).contains(query) { return true }
        let initials = title.compactMap { character -> String? in
            let text = String(character)
            let latin = text.applyingTransform(.mandarinToLatin, reverse: false) ?? text
            return normalized(latin).first.map(String.init)
        }.joined()
        return initials.contains(query)
    }

    private static func normalized(_ text: String) -> String {
        text.folding(options: [.diacriticInsensitive, .caseInsensitive], locale: Locale(identifier: "zh_CN"))
            .filter { $0.isLetter || $0.isNumber }
    }
}
