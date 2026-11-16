package core

import (
	"net"
	"net/http"
	"strings"
	"time"
)

// tvOS can invalidate the listening socket while the app is in the background.
// Rebind the same address so existing playback URLs and sessions remain valid.
// Called under engine.mu before publishing a playback URL.
func (stream *nativeStreamServer) nativeEnsureListening() error {
	address := strings.TrimPrefix(stream.address, "http://")
	connection, err := net.DialTimeout("tcp4", address, 250*time.Millisecond)
	if err == nil {
		connection.Close()
		return nil
	}
	_ = stream.server.Close()
	listener, err := net.Listen("tcp4", address)
	if err != nil {
		return err
	}
	server := &http.Server{Handler: http.HandlerFunc(stream.nativeServe), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384}
	stream.server = server
	go func() { _ = server.Serve(listener) }()
	stream.nativeLog(nativeStreamDiagnostic{}, "listener_restarted", stream.address, "", 0, "")
	return nil
}
