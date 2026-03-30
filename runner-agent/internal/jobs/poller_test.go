package jobs

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"

	"zen70/runner-agent/internal/api"
	"zen70/runner-agent/internal/config"
	runnerexec "zen70/runner-agent/internal/exec"
)

func TestLoopReportsProgressAndResult(t *testing.T) {
	t.Parallel()

	var (
		mu            sync.Mutex
		pulls         int
		progressCalls []api.JobProgressRequest
		resultPayload *api.JobResultRequest
		pullPayload   *api.PullRequest
	)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/jobs/pull":
			var payload api.PullRequest
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatalf("decode pull request: %v", err)
			}
			mu.Lock()
			pullPayload = &payload
			pulls++
			currentPull := pulls
			mu.Unlock()

			if currentPull == 1 {
				_ = json.NewEncoder(w).Encode(map[string]any{
					"code":    "ZEN-OK-0",
					"message": "ok",
					"data": []map[string]any{
						{
							"job_id":        "job-success",
							"kind":          "noop",
							"payload":       map[string]any{},
							"status":        "leased",
							"node_id":       "node-1",
							"attempt":       2,
							"lease_token":   "lease-success",
							"lease_seconds": 30,
							"leased_until":  "2026-03-28T12:00:00Z",
						},
					},
				})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data":    []map[string]any{},
			})
		case "/api/v1/jobs/job-success/progress":
			var payload api.JobProgressRequest
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatalf("decode progress payload: %v", err)
			}
			mu.Lock()
			progressCalls = append(progressCalls, payload)
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data":    map[string]any{},
			})
		case "/api/v1/jobs/job-success/result":
			var payload api.JobResultRequest
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatalf("decode result payload: %v", err)
			}
			mu.Lock()
			resultPayload = &payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data":    map[string]any{},
			})
			cancel()
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	client := api.New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	cfg := config.Config{
		TenantID:     "tenant-alpha",
		NodeID:       "node-1",
		Capabilities: []string{"noop"},
		PullInterval: time.Hour,
	}

	err := Loop(ctx, cfg, client, runnerexec.New(runnerexec.Config{DefaultTimeoutSeconds: 300, MaxOutputBytes: 1 << 20}))
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if pullPayload == nil || pullPayload.TenantID != "tenant-alpha" || pullPayload.NodeID != "node-1" {
		t.Fatalf("unexpected pull payload: %+v", pullPayload)
	}
	if len(progressCalls) != 2 {
		t.Fatalf("expected two progress callbacks, got %d", len(progressCalls))
	}
	if progressCalls[0].Progress != 5 || progressCalls[1].Progress != 100 {
		t.Fatalf("unexpected progress sequence: %+v", progressCalls)
	}
	if resultPayload == nil {
		t.Fatalf("expected result payload")
	}
	if resultPayload.TenantID != "tenant-alpha" || resultPayload.NodeID != "node-1" {
		t.Fatalf("unexpected result identity: %+v", resultPayload)
	}
	if resultPayload.LeaseToken != "lease-success" || resultPayload.Attempt != 2 {
		t.Fatalf("unexpected result lease payload: %+v", resultPayload)
	}
	if resultPayload.Result["summary"] != "noop completed" {
		t.Fatalf("unexpected result body: %+v", resultPayload.Result)
	}
}

func TestLoopReportsFailureForUnsupportedJobs(t *testing.T) {
	t.Parallel()

	var (
		mu          sync.Mutex
		failPayload *api.JobFailRequest
	)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/api/v1/jobs/pull":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data": []map[string]any{
					{
						"job_id":        "job-fail",
						"kind":          "unsupported.kind",
						"payload":       map[string]any{},
						"status":        "leased",
						"node_id":       "node-1",
						"attempt":       1,
						"lease_token":   "lease-fail",
						"lease_seconds": 30,
						"leased_until":  "2026-03-28T12:00:00Z",
					},
				},
			})
		case "/api/v1/jobs/job-fail/progress":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data":    map[string]any{},
			})
		case "/api/v1/jobs/job-fail/fail":
			var payload api.JobFailRequest
			if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
				t.Fatalf("decode fail payload: %v", err)
			}
			mu.Lock()
			failPayload = &payload
			mu.Unlock()
			_ = json.NewEncoder(w).Encode(map[string]any{
				"code":    "ZEN-OK-0",
				"message": "ok",
				"data":    map[string]any{},
			})
			cancel()
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	client := api.New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	cfg := config.Config{
		TenantID:     "tenant-alpha",
		NodeID:       "node-1",
		Capabilities: []string{"unsupported.kind"},
		PullInterval: time.Hour,
	}

	err := Loop(ctx, cfg, client, runnerexec.New(runnerexec.Config{DefaultTimeoutSeconds: 300, MaxOutputBytes: 1 << 20}))
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got %v", err)
	}

	mu.Lock()
	defer mu.Unlock()
	if failPayload == nil {
		t.Fatalf("expected fail payload")
	}
	if failPayload.TenantID != "tenant-alpha" || failPayload.NodeID != "node-1" {
		t.Fatalf("unexpected fail identity: %+v", failPayload)
	}
	if failPayload.LeaseToken != "lease-fail" || failPayload.Attempt != 1 {
		t.Fatalf("unexpected fail lease payload: %+v", failPayload)
	}
	if failPayload.Error == "" {
		t.Fatalf("expected execution error in fail payload")
	}
}
