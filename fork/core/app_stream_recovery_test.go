package core

import (
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestStreamRecoversListenerWithoutChangingPlaybackURL(t *testing.T) {
	engine, err := newNativeEngine(t.TempDir(), "")
	if err != nil {
		t.Fatal(err)
	}
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "video bytes")
	}))
	defer upstream.Close()
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	stream := &nativeStreamServer{
		downloader: engine.downloader, address: "http://" + listener.Addr().String(),
		sessions: map[string]*nativeStreamSession{},
	}
	stream.server = &http.Server{Handler: http.HandlerFunc(stream.nativeServe)}
	defer func() { stream.server.Close() }()
	address, token := stream.nativeOpen(providerMedia{URL: upstream.URL + "/video.mp4"})
	defer stream.nativeRelease(token)
	// Establish and then invalidate the listener while retaining its sessions.
	oldServer := stream.server
	go func() { _ = oldServer.Serve(listener) }()
	stream.server.Close()
	listener.Close()
	if connection, err := net.DialTimeout("tcp4", listener.Addr().String(), time.Second); err == nil {
		connection.Close()
		t.Fatal("closed listener still accepts connections")
	}
	if err := stream.nativeEnsureListening(); err != nil {
		t.Fatal(err)
	}
	client := &http.Client{Timeout: time.Second}
	deadline := time.Now().Add(3 * time.Second)
	for {
		response, err := client.Get(address)
		if err == nil {
			body, readErr := io.ReadAll(response.Body)
			response.Body.Close()
			if readErr != nil || response.StatusCode != http.StatusOK || string(body) != "video bytes" {
				t.Fatalf("recovered URL failed: status=%d body=%q err=%v", response.StatusCode, body, readErr)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal(err)
		}
		time.Sleep(10 * time.Millisecond)
	}
	stream.server.Close()
}
