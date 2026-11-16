package core

import (
	"context"
	"net/http"
	"testing"
)

func TestProducerSearchFallsBackToConsistentSiblingSeason(t *testing.T) {
	dramas := []Drama{
		{ID: "hongguo:11", Title: "持械入宋：第十一季"},
		{ID: "hongguo:10", Title: "持械入宋：第十季", Producer: "瑾叶剧场"},
	}
	if got := hongguoProducerFromSearch(dramas, "hongguo:11", "持械入宋：第十一季"); got != "瑾叶剧场" {
		t.Fatalf("producer = %q, want sibling producer", got)
	}
	dramas = append(dramas, Drama{ID: "hongguo:9", Title: "持械入宋：第九季", Producer: "其他公司"})
	if got := hongguoProducerFromSearch(dramas, "hongguo:11", "持械入宋：第十一季"); got != "" {
		t.Fatalf("producer = %q, want no result when sibling data conflicts", got)
	}
}

func TestProducerPageReturnsBeforeDateEnrichment(t *testing.T) {
	calls := 0
	engine := sourceFixtureEngine(t, func(request *http.Request) (*http.Response, error) {
		calls++
		if request.URL.Path != hongguoSearchTabPath {
			t.Fatalf("unexpected request before showing page: %s", request.URL.Path)
		}
		return sourceFixtureResponse(request, 200, `{"code":0,"search_tabs":[{"tab_type":"29","has_more":true,"data":[{"video_data":[{"series_id":"123","title":"合成剧","copyright":"测试公司","series_play_cnt":"7"}]}]}]}`), nil
	})
	input := nativeInput{Drama: nativeDrama{ID: "hongguo:123", Producer: "测试公司"}, Page: 1}
	value, err := engine.nativeProducerWorksPage(context.Background(), input)
	if err != nil {
		t.Fatal(err)
	}
	result := value.(map[string]any)
	items := result["items"].([]nativeDrama)
	if calls != 1 || result["hasMore"] != true || len(items) != 1 || items[0].ID != "hongguo:123" || items[0].OnlineDate != "" {
		t.Fatalf("page did not return independently: calls=%d result=%+v", calls, result)
	}
	input.Page = hongguoProducerMaxPages
	value, err = engine.nativeProducerWorksPage(context.Background(), input)
	if err != nil || value.(map[string]any)["hasMore"] != false {
		t.Fatal("page limit was not enforced", value, err)
	}
	input.Page = 0
	if _, err = engine.nativeProducerWorksPage(context.Background(), input); err == nil {
		t.Fatal("accepted invalid page")
	}
}

func TestProducerDateFailurePreservesVisibleItems(t *testing.T) {
	engine := sourceFixtureEngine(t, func(request *http.Request) (*http.Response, error) { return nil, context.Canceled })
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	original := nativeDrama{ID: "hongguo:123", SourceID: "123", Title: "合成剧", Producer: "测试公司", Heat: "7", Cover: "https://example.invalid/cover"}
	value, err := engine.nativeProducerDates(ctx, []nativeDrama{original})
	if err != nil {
		t.Fatal(err)
	}
	items := value.(map[string]any)["items"].([]nativeDrama)
	if len(items) != 1 || items[0].ID != original.ID || items[0].Title != original.Title || items[0].Cover != original.Cover || items[0].Producer != original.Producer || items[0].Heat != original.Heat {
		t.Fatalf("lost visible drama on optional date failure: %+v", items)
	}
}
