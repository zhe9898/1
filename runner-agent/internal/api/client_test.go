package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"zen70/runner-agent/internal/config"
)

func TestClientPullJobsAddsBearerAndUnwrapsEnvelope(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/jobs/pull" {
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
			t.Fatalf("expected bearer token, got %q", got)
		}

		var payload PullRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode pull payload: %v", err)
		}
		if payload.TenantID != "tenant-alpha" || payload.NodeID != "node-1" {
			t.Fatalf("unexpected pull payload: %+v", payload)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"code":    "ZEN-OK-0",
			"message": "ok",
			"data": []map[string]any{
				{
					"job_id":        "job-1",
					"kind":          "noop",
					"payload":       map[string]any{},
					"status":        "leased",
					"node_id":       "node-1",
					"attempt":       1,
					"lease_token":   "lease-1",
					"lease_seconds": 30,
					"leased_until":  "2026-03-28T12:00:00Z",
				},
			},
		})
	}))
	defer server.Close()

	client := New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	jobs, err := client.PullJobs(context.Background(), PullRequest{
		TenantID:      "tenant-alpha",
		NodeID:        "node-1",
		Limit:         1,
		AcceptedKinds: []string{"noop"},
	})
	if err != nil {
		t.Fatalf("pull jobs: %v", err)
	}
	if len(jobs) != 1 {
		t.Fatalf("expected one job, got %d", len(jobs))
	}
	if jobs[0].JobID != "job-1" || jobs[0].LeaseToken != "lease-1" {
		t.Fatalf("unexpected decoded job: %+v", jobs[0])
	}
}

func TestClientPostReturnsServerErrorBody(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "lease mismatch", http.StatusConflict)
	}))
	defer server.Close()

	client := New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	err := client.SendFailure(context.Background(), "job-1", JobFailRequest{
		TenantID:   "tenant-alpha",
		NodeID:     "node-1",
		LeaseToken: "lease-1",
		Attempt:    1,
		Error:      "boom",
	})
	if err == nil {
		t.Fatalf("expected request failure")
	}
	if !strings.Contains(err.Error(), "lease mismatch") {
		t.Fatalf("expected server body in error, got %v", err)
	}
}
