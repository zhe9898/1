package heartbeat

import (
	"context"
	"log"
	"time"

	"zen70/runner-agent/internal/api"
	"zen70/runner-agent/internal/config"
	"zen70/runner-agent/internal/telemetry"
)

// Loop sends periodic heartbeats until ctx is cancelled.
// Dynamic edge telemetry (latency, power, thermal, connectivity) is read
// from the runtime collector each tick; static fields come from config.
func Loop(ctx context.Context, cfg config.Config, client *api.Client, collector *telemetry.Collector) error {
	ticker := time.NewTicker(cfg.HeartbeatInterval)
	defer ticker.Stop()

	for {
		req := buildHeartbeatRequest(cfg, collector)
		if err := client.HeartbeatNode(ctx, req); err != nil {
			log.Printf("heartbeat failed: %v", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// buildHeartbeatRequest assembles the heartbeat payload.
// Static fields come from config; dynamic telemetry comes from the collector.
func buildHeartbeatRequest(cfg config.Config, collector *telemetry.Collector) api.HeartbeatRequest {
	snap := collector.Get()
	return api.HeartbeatRequest{
		TenantID:        cfg.TenantID,
		NodeID:          cfg.NodeID,
		Name:            cfg.NodeName,
		NodeType:        cfg.NodeType,
		Address:         cfg.NodeAddress,
		Profile:         cfg.Profile,
		Executor:        cfg.Executor,
		OS:              cfg.OperatingSystem,
		Arch:            cfg.Architecture,
		Zone:            cfg.Zone,
		ProtocolVersion: cfg.ProtocolVersion,
		LeaseVersion:    cfg.LeaseVersion,
		AgentVersion:    cfg.AgentVersion,
		MaxConcurrency:  cfg.MaxConcurrency,
		CPUCores:        cfg.CPUCores,
		MemoryMB:        cfg.MemoryMB,
		GPUVRAMMB:       cfg.GPUVRAMMB,
		StorageMB:       cfg.StorageMB,
		Status:          "online",
		Capabilities:    cfg.Capabilities,
		Metadata: map[string]any{
			"profile":         cfg.Profile,
			"runtime":         "go",
			"lease_seconds":   cfg.LeaseSeconds,
			"agent_version":   cfg.AgentVersion,
			"max_concurrency": cfg.MaxConcurrency,
			"cpu_cores":       cfg.CPUCores,
			"memory_mb":       cfg.MemoryMB,
			"gpu_vram_mb":     cfg.GPUVRAMMB,
			"storage_mb":      cfg.StorageMB,
		},
		AcceptedKinds:      cfg.AcceptedKinds,
		NetworkLatencyMs:   snap.NetworkLatencyMs,  // ← dynamic
		BandwidthMbps:      cfg.BandwidthMbps,      // static declaration
		CachedDataKeys:     cfg.CachedDataKeys,     // static declaration
		PowerCapacityWatts: cfg.PowerCapacityWatts, // static hardware limit
		CurrentPowerWatts:  snap.CurrentPowerWatts, // ← dynamic
		ThermalState:       snap.ThermalState,      // ← dynamic
		CloudConnectivity:  snap.CloudConnectivity, // ← dynamic
	}
}
