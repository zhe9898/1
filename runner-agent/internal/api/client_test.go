package api

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
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

func TestClientRenewLeaseReturnsUpdatedToken(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/jobs/job-1/renew" {
			t.Fatalf("unexpected path %s", r.URL.Path)
		}

		var payload JobRenewRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode renew payload: %v", err)
		}
		if payload.LeaseToken != "lease-old" || payload.Attempt != 2 {
			t.Fatalf("unexpected renew payload: %+v", payload)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"code":    "ZEN-OK-0",
			"message": "ok",
			"data": map[string]any{
				"job_id":        "job-1",
				"kind":          "noop",
				"payload":       map[string]any{},
				"status":        "leased",
				"node_id":       "node-1",
				"attempt":       2,
				"lease_token":   "lease-new",
				"lease_seconds": 30,
				"leased_until":  "2026-03-28T12:00:30Z",
			},
		})
	}))
	defer server.Close()

	client := New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	renewed, err := client.RenewLease(context.Background(), "job-1", JobRenewRequest{
		TenantID:      "tenant-alpha",
		NodeID:        "node-1",
		LeaseToken:    "lease-old",
		Attempt:       2,
		ExtendSeconds: 30,
	})
	if err != nil {
		t.Fatalf("renew lease: %v", err)
	}
	if renewed.LeaseToken != "lease-new" || renewed.Attempt != 2 {
		t.Fatalf("unexpected renewed job: %+v", renewed)
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

func TestClientPostReturnsMediumErrorBodiesWithoutTruncation(t *testing.T) {
	t.Parallel()

	mediumBody := strings.Repeat("x", maxErrorBodyBytes-128)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, mediumBody, http.StatusBadGateway)
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
	if !strings.Contains(err.Error(), mediumBody) {
		t.Fatalf("expected complete medium-sized error body, got %v", err)
	}
	if strings.Contains(err.Error(), "...(truncated)") {
		t.Fatalf("did not expect truncation marker in error, got %v", err)
	}
}

func TestClientPostSummarizesHugeErrorBodies(t *testing.T) {
	t.Parallel()

	largeBody := strings.Repeat("x", maxErrorBodyBytes+32)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, largeBody, http.StatusBadGateway)
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
	if !strings.Contains(err.Error(), "...(truncated, total_bytes=") {
		t.Fatalf("expected structured truncation marker in error, got %v", err)
	}
	expectedDigest := sha256.Sum256([]byte(largeBody + "\n"))
	if !strings.Contains(err.Error(), fmt.Sprintf("sha256=%x", expectedDigest)) {
		t.Fatalf("expected full-body sha256 in error, got %v", err)
	}
}
