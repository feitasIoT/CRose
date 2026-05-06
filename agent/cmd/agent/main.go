package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os/exec"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"

	"mygoagent/internal/config"
	"mygoagent/internal/handler"
	"mygoagent/internal/mqtt"
	"mygoagent/internal/nodered"
	"mygoagent/internal/orchestrator"
)

func parseBearerToken(h string) string {
	h = strings.TrimSpace(h)
	if h == "" {
		return ""
	}
	parts := strings.SplitN(h, " ", 2)
	if len(parts) != 2 {
		return ""
	}
	if !strings.EqualFold(parts[0], "bearer") {
		return ""
	}
	return strings.TrimSpace(parts[1])
}

type statusRecorder struct {
	http.ResponseWriter
	status  int
	written bool
}

func (r *statusRecorder) WriteHeader(status int) {
	r.status = status
	r.written = true
	r.ResponseWriter.WriteHeader(status)
}

func (r *statusRecorder) Write(b []byte) (int, error) {
	if !r.written {
		r.WriteHeader(http.StatusOK)
	}
	return r.ResponseWriter.Write(b)
}

func withLogging(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		recorder := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(recorder, r)
		log.Printf("HTTP %s %s from %s -> status=%d duration=%s",
			r.Method, r.URL.Path, r.RemoteAddr, recorder.status, time.Since(start))
	})
}

func readNodeREDLogs(ctx context.Context, identifier, cursor string, limit int) ([]string, string, error) {
	if runtime.GOOS != "linux" {
		return nil, "", fmt.Errorf("unsupported_os: %s", runtime.GOOS)
	}
	identifier = strings.TrimSpace(identifier)
	if identifier == "" {
		return nil, "", errors.New("identifier_required")
	}
	if limit < 1 {
		limit = 200
	}
	if limit > 1000 {
		limit = 1000
	}

	type queryKind string
	const (
		queryTagNodered queryKind = "tag:nodered"
		queryUnitAt     queryKind = "unit:nodered@"
		queryUnitDash   queryKind = "unit:nodered-"
		queryTagNodeRed queryKind = "tag:Node-RED"
		queryUnitAgent  queryKind = "unit:agent"
	)

	parseCursor := func(raw string) (queryKind, string) {
		raw = strings.TrimSpace(raw)
		if raw == "" {
			if identifier == "agent" {
				return queryUnitAgent, ""
			}
			return queryTagNodered, ""
		}
		if parts := strings.SplitN(raw, "|", 2); len(parts) == 2 {
			switch queryKind(parts[0]) {
			case queryTagNodered, queryUnitAt, queryUnitDash, queryTagNodeRed, queryUnitAgent:
				return queryKind(parts[0]), strings.TrimSpace(parts[1])
			}
		}
		if identifier == "agent" {
			return queryUnitAgent, raw
		}
		return queryTagNodered, raw
	}

	buildArgs := func(kind queryKind, cursorValue string) []string {
		args := []string{
			"--no-pager",
			"--output",
			"short-iso",
			"--show-cursor",
			"-n",
			strconv.Itoa(limit),
		}
		if strings.TrimSpace(cursorValue) != "" {
			args = append(args, "--after-cursor", strings.TrimSpace(cursorValue))
		}
		switch kind {
		case queryUnitAgent:
			return append(args, "-u", "croseagent.service")
		case queryUnitAt:
			return append(args, "-u", "nodered@"+identifier+".service")
		case queryUnitDash:
			return append(args, "-u", "nodered-"+identifier+".service")
		case queryTagNodeRed:
			return append(args, "-t", "Node-RED")
		default:
			return append(args, "-t", "nodered-"+identifier)
		}
	}

	runQuery := func(kind queryKind, cursorValue string) ([]string, string, error) {
		args := buildArgs(kind, cursorValue)
		cmd := exec.CommandContext(ctx, "journalctl", args...)
		out, err := cmd.CombinedOutput()
		if err != nil {
			return nil, "", fmt.Errorf("journalctl: %w: %s", err, strings.TrimSpace(string(out)))
		}

		raw := strings.ReplaceAll(string(out), "\r\n", "\n")
		lines := make([]string, 0, limit)
		nextCursor := ""
		for _, line := range strings.Split(raw, "\n") {
			line = strings.TrimRight(line, "\r")
			if line == "" {
				continue
			}
			if strings.HasPrefix(line, "-- cursor:") {
				nextCursor = strings.TrimSpace(strings.TrimPrefix(line, "-- cursor:"))
				continue
			}
			if strings.HasPrefix(line, "--") && strings.Contains(line, "No entries") {
				continue
			}
			lines = append(lines, line)
		}
		return lines, nextCursor, nil
	}

	encodeCursor := func(kind queryKind, cursorValue string) string {
		cursorValue = strings.TrimSpace(cursorValue)
		if cursorValue == "" {
			return ""
		}
		return string(kind) + "|" + cursorValue
	}

	kind, cursorValue := parseCursor(cursor)
	if cursorValue != "" {
		lines, nextCursor, err := runQuery(kind, cursorValue)
		if err != nil {
			return nil, "", err
		}
		return lines, encodeCursor(kind, nextCursor), nil
	}

	kinds := []queryKind{queryTagNodered, queryUnitAt, queryUnitDash, queryTagNodeRed}
	if identifier == "agent" {
		kinds = []queryKind{queryUnitAgent}
	}
	for _, k := range kinds {
		lines, nextCursor, err := runQuery(k, "")
		if err != nil {
			return nil, "", err
		}
		if len(lines) > 0 {
			return lines, encodeCursor(k, nextCursor), nil
		}
	}
	return []string{}, "", nil
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	configPath := flag.String("config", "config.yaml", "config file path")
	flag.Parse()
	cfg, err := config.Load(*configPath)
	if err != nil {
		log.Fatalf("load config error: %v", err)
	}
	log.Printf("agent starting deviceID=%s", cfg.DeviceID)

	nr := nodered.NewClient(cfg.NodeRED.BaseURL, cfg.NodeRED.Token, cfg.NodeRED.APIVersion)
	orch := orchestrator.NewSystemdOrchestrator(cfg.Systemd)
	h := handler.NewProcessor(nr, orch)

	client, err := mqtt.NewClient(cfg.MQTT, cfg.DeviceID)
	if err != nil {
		log.Fatalf("mqtt client error: %v", err)
	}
	defer client.Disconnect()

	err = client.Subscribe(cfg.MQTT.TopicCmd, func(payload []byte) (string, error) {
		return h.Handle(payload)
	})
	if err != nil {
		log.Fatalf("mqtt subscribe error: %v", err)
	}
	/*
		err = client.Subscribe(cfg.MQTT.TopicStatus, func(payload []byte) (string, error) {
			log.Printf("status echo received payload=%s", string(payload))
			return "", nil
		})
		if err != nil {
			log.Fatalf("mqtt subscribe status error: %v", err)
		}
	*/
	log.Printf("agent ready cmdTopic=%s statusTopic=%s", cfg.MQTT.TopicCmd, cfg.MQTT.TopicStatus)

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	httpListen := strings.TrimSpace(cfg.HTTP.ListenAddr)
	if httpListen == "" {
		httpListen = "0.0.0.0:18080"
	}
	httpToken := strings.TrimSpace(cfg.HTTP.Token)
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	})
	mux.HandleFunc("/v1/nodered/logs", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		if httpToken != "" {
			token := parseBearerToken(r.Header.Get("Authorization"))
			if token == "" {
				token = strings.TrimSpace(r.Header.Get("X-API-Token"))
			}
			if token == "" {
				token = strings.TrimSpace(r.URL.Query().Get("token"))
			}
			if token != httpToken {
				w.WriteHeader(http.StatusUnauthorized)
				return
			}
		}

		identifier := r.URL.Query().Get("identifier")
		cursor := r.URL.Query().Get("cursor")
		limit := 200
		if s := strings.TrimSpace(r.URL.Query().Get("limit")); s != "" {
			if n, err := strconv.Atoi(s); err == nil {
				limit = n
			}
		}

		lines, nextCursor, err := readNodeREDLogs(r.Context(), identifier, cursor, limit)
		if err != nil {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusBadRequest)
			b, _ := json.Marshal(map[string]string{"error": err.Error()})
			_, _ = w.Write(b)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		b, _ := json.Marshal(map[string]any{
			"lines":       lines,
			"next_cursor": nextCursor,
		})
		_, _ = w.Write(b)
	})
	srv := &http.Server{
		Addr:              httpListen,
		Handler:           withLogging(mux),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("http server error: %v", err)
			stop()
		}
	}()
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}()
	log.Printf("http server listening addr=%s", httpListen)
	log.Printf("running; press Ctrl+C to exit")
	<-ctx.Done()
	client.PublishPresence("offline", true)
	time.Sleep(200 * time.Millisecond)
}
