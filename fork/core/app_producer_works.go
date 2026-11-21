package core

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"
)

const (
	producerMaxPages  = 6
	producerDateLimit = 40
)

type producerWorksProvider interface {
	ProducerWorksPage(ctx context.Context, page int, sourceID, title, producer string) (producerPage, error)
	ProducerDate(ctx context.Context, sourceID string) (string, error)
}

type producerPage struct {
	Producer string  `json:"producer"`
	Items    []Drama `json:"items"`
	HasMore  bool    `json:"hasMore"`
}

func (p *jsProvider) ProducerWorksPage(ctx context.Context, page int, sourceID, title, producer string) (producerPage, error) {
	var result producerPage
	err := p.call(ctx, "producerWorksPage", &result, page, sourceID, title, producer)
	return result, err
}

func (p *jsProvider) ProducerDate(ctx context.Context, sourceID string) (string, error) {
	var date string
	err := p.call(ctx, "producerDate", &date, sourceID)
	return date, err
}

func (engine *nativeEngine) hongguoProducerProvider() (producerWorksProvider, error) {
	if !nativeSourceAvailable("hongguo") {
		return nil, errNativeBuildSource
	}
	p, found := engine.downloader.provider("hongguo")
	if provider, ok := providerAs[producerWorksProvider](p); found && ok {
		return provider, nil
	}
	return nil, errors.New("当前红果模块不支持出品方片单")
}

func (engine *nativeEngine) nativeProducerWorksPage(ctx context.Context, input nativeInput) (any, error) {
	source, sourceID, valid := SplitProviderDramaID(input.Drama.ID)
	if !valid || source != "hongguo" {
		return nil, errors.New("仅红果剧集有出品方信息")
	}
	if input.Page < 1 || input.Page > producerMaxPages {
		return nil, errors.New("出品方页码无效")
	}
	provider, err := engine.hongguoProducerProvider()
	if err != nil {
		return nil, err
	}
	result, err := provider.ProducerWorksPage(ctx, input.Page, sourceID, input.Drama.Title, strings.TrimSpace(input.Query))
	if err != nil {
		return nil, err
	}
	items := make([]LibraryDrama, 0, len(result.Items))
	for _, raw := range result.Items {
		item := NativeNormalize(raw)
		if item.Source != "hongguo" || item.SourceID == "" {
			continue
		}
		item.Heat = FirstNonEmpty(item.Heat, item.Views)
		items = append(items, item)
	}
	return map[string]any{"producer": result.Producer, "items": items, "hasMore": result.HasMore && input.Page < producerMaxPages}, nil
}

func (engine *nativeEngine) nativeProducerDates(ctx context.Context, items []LibraryDrama) (any, error) {
	if items == nil {
		items = []LibraryDrama{}
	}
	provider, err := engine.hongguoProducerProvider()
	if err != nil {
		return nil, err
	}
	// Dates are optional. Keep the cards and their original order if a lookup fails.
	ctx, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	var workers sync.WaitGroup
	jobs := make(chan int)
	for range 4 {
		workers.Go(func() {
			for index := range jobs {
				_, sourceID, _ := SplitProviderDramaID(items[index].ID)
				itemCtx, stop := context.WithTimeout(ctx, 10*time.Second)
				date, err := provider.ProducerDate(itemCtx, sourceID)
				stop()
				if err == nil {
					items[index].OnlineDate = ProviderReleaseDate(date)
				}
			}
		})
	}
	count := 0
send:
	for index := range items {
		source, _, valid := SplitProviderDramaID(items[index].ID)
		if !valid || source != "hongguo" || items[index].OnlineDate != "" {
			continue
		}
		select {
		case jobs <- index:
			count++
		case <-ctx.Done():
			break send
		}
		if count == producerDateLimit {
			break
		}
	}
	close(jobs)
	workers.Wait()
	return map[string]any{"items": items}, nil
}
