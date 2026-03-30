package heartbeat

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"zen70/runner-agent/internal/api"
	"zen70/runner-agent/internal/config"
	"zen70/runner-agent/internal/telemetry"
)

func TestLoopSendsHeartbeatPayloadWithTenantAndCapacity(t *testing.T) {
	t.Parallel()

	payloadCh := make(chan api.HeartbeatRequest, 1)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/nodes/heartbeat" {
			t.Fatalf("unexpected path %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer node-token-test" {
			t.Fatalf("expected bearer token, got %q", got)
		}
		var payload api.HeartbeatRequest
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode heartbeat payload: %v", err)
		}
		select {
		case payloadCh <- payload:
		default:
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"code":    "ZEN-OK-0",
			"message": "ok",
			"data":    map[string]any{},
		})
		cancel()
	}))
	defer server.Close()

	client := api.New(config.Config{
		GatewayBaseURL: server.URL,
		NodeToken:      "node-token-test",
	})

	cfg := config.Config{
		TenantID:          "tenant-alpha",
		NodeID:            "node-1",
		NodeName:          "IOS Client",
		NodeType:          "native-client",
		NodeAddress:       "10.0.0.5",
		Profile:           "gateway-kernel",
		Executor:          "swift-native",
		OperatingSystem:   "ios",
		Architecture:      "arm64",
		Zone:              "cn-sh",
		ProtocolVersion:   "runner.v1",
		LeaseVersion:      "job-lease.v1",
		AgentVersion:      "runner-agent.v1",
		MaxConcurrency:    1,
		CPUCores:          4,
		MemoryMB:          2048,
		GPUVRAMMB:         0,
		StorageMB:         64000,
		Capabilities:      []string{"health.ingest"},
		LeaseSeconds:      30,
		HeartbeatInterval: time.Hour,
	}

	collector := telemetry.NewCollector(server.URL, client.HTTPClient(), telemetry.Snapshot{
		NetworkLatencyMs:  50,
		CurrentPowerWatts: 100,
		ThermalState:      "normal",
		CloudConnectivity: "full",
	})

	err := Loop(ctx, cfg, client, collector)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context cancellation, got %v", err)
	}

	select {
	case payload := <-payloadCh:
		if payload.TenantID != "tenant-alpha" || payload.NodeID != "node-1" {
			t.Fatalf("unexpected identity payload: %+v", payload)
		}
		if payload.Executor != "swift-native" || payload.OS != "ios" || payload.Arch != "arm64" {
			t.Fatalf("unexpected platform payload: %+v", payload)
		}
		if payload.MaxConcurrency != 1 || payload.MemoryMB != 2048 || payload.StorageMB != 64000 {
			t.Fatalf("unexpected capacity payload: %+v", payload)
		}
		if payload.Metadata["max_concurrency"] != float64(1) {
			t.Fatalf("expected max_concurrency metadata, got %#v", payload.Metadata)
		}
	default:
		t.Fatalf("expected heartbeat payload")
	}
}
