package core

import (
	"encoding/json"
	"path/filepath"
	"testing"
)

func TestBundledProducerRequests(t *testing.T) {
	modules, errs := readBundledModules()
	if len(errs) != 0 {
		t.Fatal(errs)
	}
	module, ok := modules["hongguo"]
	if !ok {
		t.Skip("prepare the bundled Hongguo module first")
	}
	fixture := `
const producerFixtureRow = (id, title, producer, extra = {}) =>
  Object.assign({series_id_str: id, series_title: title, copyright: producer}, extra);
appRequest = function(method, path, query) {
  let more = false;
  let rows = [];
  if (query.query === "测试剧第十一季") {
    rows = [producerFixtureRow("101", "测试剧第十季", "甲")];
  } else if (query.query === "测试剧") {
    rows = [producerFixtureRow("101", "测试剧第十季", "甲")];
  } else if (query.query === "歧义剧第一季") {
    rows = [producerFixtureRow("202", "歧义剧第二季", "甲"), producerFixtureRow("203", "歧义剧第三季", "乙")];
  } else if (query.query === "甲") {
    more = true;
    if (query.offset === "0") {
      rows = [producerFixtureRow("101", "测试剧第十季", " 甲 ", {play_cnt:"123"}), producerFixtureRow("102", "其他剧", "甲乙")];
    } else {
      if (query.search_id !== "fixture" || query.passback !== query.offset) throw new Error("missing pagination state");
      rows = [producerFixtureRow("103", "下一页", "甲", {hot_score:"456"})];
    }
  }
  return {search_tabs: [{tab_type:"29", search_id:"fixture", has_more:more, data:[{video_data: rows}]}]};
};
fetchSiteText = function(address) {
  if (!address.endsWith("=101")) throw new Error("fixture date unavailable");
  return '_ROUTER_DATA={"loaderData":{"detail_page":{"seriesDetail":{"series_id":"101","series_name":"测试剧第十季","first_visible_time":"1735689600"}}}}';
};
`
	module.code = append(module.code, fixture...)
	parsed, err := parseModule(module.raw, module.code)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	engine, err := newNativeEngine(dir, dir, filepath.Join(dir, "downloads"))
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := engine.downloader.provider("hongguo"); !ok {
		t.Fatal("bundled Hongguo was not registered")
	}
	engine.downloader.providers["hongguo"] = parsed.provider(engine.downloader)
	nativeState.Lock()
	previous := nativeState.engine
	nativeState.engine = engine
	nativeState.Unlock()
	t.Cleanup(func() {
		nativeState.Lock()
		nativeState.engine = previous
		nativeState.Unlock()
	})
	type result struct {
		Producer string
		Items    []LibraryDrama
		HasMore  bool
	}
	request := func(input nativeInput) result {
		t.Helper()
		raw, err := json.Marshal(input)
		if err != nil {
			t.Fatal(err)
		}
		response := NativeRequest(string(raw))
		var reply struct {
			OK   bool
			Data result
		}
		if err := json.Unmarshal([]byte(response), &reply); err != nil || !reply.OK {
			t.Fatalf("%s page %d: %s", input.Action, input.Page, response)
		}
		return reply.Data
	}
	drama := LibraryDrama{ID: "hongguo:100", Title: "测试剧第十一季"}
	first := request(nativeInput{Action: "producerWorksPage", Drama: drama, Page: 1})
	if first.Producer != "甲" || !first.HasMore || len(first.Items) != 1 || first.Items[0].ID != "hongguo:101" || first.Items[0].Heat != "123" {
		t.Fatalf("unexpected first page: %+v", first)
	}
	second := request(nativeInput{Action: "producerWorksPage", Drama: drama, Page: 2, Query: first.Producer})
	if !second.HasMore || len(second.Items) != 1 || second.Items[0].Heat != "456" {
		t.Fatalf("unexpected second page: %+v", second)
	}
	if last := request(nativeInput{Action: "producerWorksPage", Drama: drama, Page: 6, Query: first.Producer}); last.HasMore {
		t.Fatal("page 6 must stop pagination")
	}
	ambiguous := request(nativeInput{Action: "producerWorksPage", Drama: LibraryDrama{ID: "hongguo:201", Title: "歧义剧第一季"}, Page: 1})
	if ambiguous.Producer != "" || len(ambiguous.Items) != 0 || ambiguous.HasMore {
		t.Fatalf("ambiguous producer should be rejected: %+v", ambiguous)
	}
	dates := request(nativeInput{Action: "producerDates", Dramas: append(first.Items, second.Items...)})
	if len(dates.Items) != 2 || dates.Items[0].OnlineDate != "2025-01-01" || dates.Items[1].ID != "hongguo:103" || dates.Items[1].OnlineDate != "" {
		t.Fatalf("unexpected date enrichment: %+v", dates.Items)
	}
}
