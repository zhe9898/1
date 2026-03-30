package service_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"zen70/runner-agent/internal/config"
	"zen70/runner-agent/internal/service"
)

type envelope struct {
	Code    string      `json:"code"`
	Message string      `json:"message"`
	Data    interface{} `json:"data"`
}

type stateSnapshot struct {
	registered    bool
	heartbeats    int
	progresses    int
	results       int
	failures      int
	lastRegister  map[string]any
	lastHeartbeat map[string]any
	lastProgress  map[string]any
	lastResult    map[string]any
	lastFail      map[string]any
}

func TestServiceRunCompletesConnectorInvokeJob(t *testing.T) {
	t.Parallel()

	var mu sync.Mutex
	dispatched := false
	state := stateSnapshot{}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		switch r.URL.Path {
		case "/api/v1/nodes/register":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on register, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.registered = true
			state.lastRegister = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/nodes/heartbeat":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on heartbeat, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.heartbeats++
			state.lastHeartbeat = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/pull":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on pull, got %q", got)
			}
			mu.Lock()
			defer mu.Unlock()
			if !dispatched {
				dispatched = true
				_ = json.NewEncoder(w).Encode(envelope{
					Code:    "ZEN-OK-0",
					Message: "ok",
					Data: []map[string]any{
						{
							"job_id":          "job-success",
							"kind":            "connector.invoke",
							"status":          "leased",
							"attempt":         1,
							"lease_token":     "lease-success",
							"lease_seconds":   30,
							"leased_until":    "2026-03-26T10:00:00",
							"idempotency_key": "job-success-key",
							"payload": map[string]any{
								"connector_id":   "demo-connector",
								"connector_kind": "http",
								"action":         "ping",
								"payload":        map[string]any{"from": "test"},
							},
						},
					},
				})
				return
			}
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: []map[string]any{}})
		case "/api/v1/jobs/job-success/result":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on result, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.results++
			state.lastResult = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/job-success/progress":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on progress, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.progresses++
			state.lastProgress = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/job-success/fail":
			t.Fatalf("unexpected failure callback")
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	runServiceAndWait(t, srv.URL, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return state.registered && state.heartbeats > 0 && state.results == 1
	})

	mu.Lock()
	defer mu.Unlock()
	if state.lastResult == nil {
		t.Fatalf("expected result payload")
	}
	if state.lastRegister["executor"] != "go-native" {
		t.Fatalf("expected executor in register payload")
	}
	if state.lastRegister["tenant_id"] != "tenant-alpha" {
		t.Fatalf("expected tenant_id in register payload")
	}
	if state.lastRegister["max_concurrency"] != float64(1) {
		t.Fatalf("expected max_concurrency in register payload")
	}
	if state.lastHeartbeat["tenant_id"] != "tenant-alpha" {
		t.Fatalf("expected tenant_id in heartbeat payload")
	}
	if state.lastRegister["cpu_cores"] == nil {
		t.Fatalf("expected cpu_cores in register payload")
	}
	if state.lastHeartbeat["memory_mb"] == nil {
		t.Fatalf("expected memory_mb in heartbeat payload")
	}
	if state.lastRegister["agent_version"] != "runner-agent.v1" {
		t.Fatalf("expected agent_version in register payload")
	}
	if state.lastRegister["os"] == "" || state.lastRegister["arch"] == "" {
		t.Fatalf("expected os/arch in register payload")
	}
	if state.lastHeartbeat["protocol_version"] != "runner.v1" {
		t.Fatalf("expected protocol version in heartbeat payload")
	}
	if state.progresses == 0 {
		t.Fatalf("expected at least one progress callback")
	}
	result, _ := state.lastResult["result"].(map[string]any)
	if result == nil {
		t.Fatalf("expected result body in callback")
	}
	if state.lastResult["node_id"] != "node-test" {
		t.Fatalf("expected node_id in result payload")
	}
	if state.lastResult["tenant_id"] != "tenant-alpha" {
		t.Fatalf("expected tenant_id in result payload")
	}
	if state.lastResult["lease_token"] != "lease-success" {
		t.Fatalf("expected lease_token in result payload")
	}
	if state.lastResult["attempt"] != float64(1) {
		t.Fatalf("expected attempt in result payload")
	}
	if result["summary"] == "" {
		t.Fatalf("expected non-empty summary")
	}
	if _, ok := result["output"].(string); !ok {
		t.Fatalf("expected string output in callback")
	}
}

func TestServiceRunReportsExecutionFailure(t *testing.T) {
	t.Parallel()

	var mu sync.Mutex
	dispatched := false
	state := stateSnapshot{}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		switch r.URL.Path {
		case "/api/v1/nodes/register", "/api/v1/nodes/heartbeat":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on %s, got %q", r.URL.Path, got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			if r.URL.Path == "/api/v1/nodes/register" {
				mu.Lock()
				state.registered = true
				state.lastRegister = payload
				mu.Unlock()
			} else {
				mu.Lock()
				state.heartbeats++
				state.lastHeartbeat = payload
				mu.Unlock()
			}
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/pull":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on pull, got %q", got)
			}
			mu.Lock()
			defer mu.Unlock()
			if !dispatched {
				dispatched = true
				_ = json.NewEncoder(w).Encode(envelope{
					Code:    "ZEN-OK-0",
					Message: "ok",
					Data: []map[string]any{
						{
							"job_id":          "job-fail",
							"kind":            "unsupported.kind",
							"status":          "leased",
							"attempt":         1,
							"lease_token":     "lease-fail",
							"lease_seconds":   30,
							"leased_until":    "2026-03-26T10:00:00",
							"idempotency_key": "job-fail-key",
							"payload": map[string]any{
								"connector_id": "demo-connector",
							},
						},
					},
				})
				return
			}
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: []map[string]any{}})
		case "/api/v1/jobs/job-fail/fail":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on fail, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.failures++
			state.lastFail = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/job-fail/progress":
			if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
				t.Fatalf("expected bearer token on progress, got %q", got)
			}
			var payload map[string]any
			_ = json.NewDecoder(r.Body).Decode(&payload)
			mu.Lock()
			state.progresses++
			state.lastProgress = payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(envelope{Code: "ZEN-OK-0", Message: "ok", Data: map[string]any{}})
		case "/api/v1/jobs/job-fail/result":
			t.Fatalf("unexpected success callback")
		default:
			http.NotFound(w, r)
		}
	}))
	defer srv.Close()

	runServiceAndWait(t, srv.URL, func() bool {
		mu.Lock()
		defer mu.Unlock()
		return state.registered && state.heartbeats > 0 && state.failures == 1
	})

	mu.Lock()
	defer mu.Unlock()
	if state.lastFail == nil {
		t.Fatalf("expected failure payload")
	}
	if state.lastFail["node_id"] != "node-test" {
		t.Fatalf("expected node_id in failure payload")
	}
	if state.lastFail["tenant_id"] != "tenant-alpha" {
		t.Fatalf("expected tenant_id in failure payload")
	}
	if state.lastFail["lease_token"] != "lease-fail" {
		t.Fatalf("expected lease_token in failure payload")
	}
	if state.lastFail["attempt"] != float64(1) {
		t.Fatalf("expected attempt in failure payload")
	}
	if state.lastFail["error"] == "" {
		t.Fatalf("expected failure error")
	}
}

func TestServiceRunRequiresNodeToken(t *testing.T) {
	t.Parallel()

	svc := service.New(config.Config{
		GatewayBaseURL: "https://127.0.0.1:8000",
		NodeID:         "node-test",
		NodeName:       "go-runner-test",
	})

	err := svc.Run(context.Background())
	if err == nil {
		t.Fatalf("expected missing node token error")
	}
}

func TestServiceRunRejectsRemoteHTTPGateway(t *testing.T) {
	t.Parallel()

	svc := service.New(config.Config{
		GatewayBaseURL: "http://gateway.example.com:8000",
		NodeID:         "node-test",
		NodeName:       "go-runner-test",
		NodeToken:      "node-token-test",
	})

	err := svc.Run(context.Background())
	if err == nil {
		t.Fatalf("expected remote plaintext gateway error")
	}
}

func runServiceAndWait(t *testing.T, baseURL string, done func() bool) {
	t.Helper()

	cfg := config.Config{
		GatewayBaseURL:    baseURL,
		AllowInsecureHTTP: true,
		NodeID:            "node-test",
		TenantID:          "tenant-alpha",
		NodeToken:         "node-token-test",
		NodeName:          "go-runner-test",
		NodeType:          "runner",
		Profile:           "go-runner",
		Executor:          "go-native",
		OperatingSystem:   "windows",
		Architecture:      "amd64",
		Zone:              "lab-a",
		ProtocolVersion:   "runner.v1",
		LeaseVersion:      "job-lease.v1",
		AgentVersion:      "runner-agent.v1",
		MaxConcurrency:    1,
		CPUCores:          8,
		MemoryMB:          16384,
		GPUVRAMMB:         0,
		StorageMB:         102400,
		Capabilities:      []string{"connector.invoke", "unsupported.kind"},
		HeartbeatInterval: 10 * time.Millisecond,
		PullInterval:      10 * time.Millisecond,
		LeaseSeconds:      30,
	}

	svc := service.New(cfg)
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	errCh := make(chan error, 1)
	go func() {
		errCh <- svc.Run(ctx)
	}()

	deadline := time.Now().Add(400 * time.Millisecond)
	for time.Now().Before(deadline) {
		if done() {
			cancel()
			err := <-errCh
			if err != nil {
				t.Fatalf("service returned error: %v", err)
			}
			return
		}
		time.Sleep(10 * time.Millisecond)
	}

	cancel()
	err := <-errCh
	if err != nil {
		t.Fatalf("service returned error before condition: %v", err)
	}
	t.Fatalf("timed out waiting for service flow")
}
