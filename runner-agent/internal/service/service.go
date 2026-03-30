package service

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"

	"zen70/runner-agent/internal/api"
	"zen70/runner-agent/internal/config"
	runnerexec "zen70/runner-agent/internal/exec"
	"zen70/runner-agent/internal/heartbeat"
	"zen70/runner-agent/internal/jobs"
	"zen70/runner-agent/internal/telemetry"
)

type Service struct {
	cfg       config.Config
	client    *api.Client
	executor  *runnerexec.Executor
	collector *telemetry.Collector
}

func New(cfg config.Config) *Service {
	client := api.New(cfg)
	return &Service{
		cfg:    cfg,
		client: client,
		executor: runnerexec.New(runnerexec.Config{
			DefaultTimeoutSeconds: cfg.LeaseSeconds,
			MaxOutputBytes:        1 << 20,
		}),
		collector: telemetry.NewCollector(
			cfg.GatewayBaseURL,
			client.HTTPClient(),
			telemetry.Snapshot{
				NetworkLatencyMs:  cfg.NetworkLatencyMs,
				CurrentPowerWatts: cfg.CurrentPowerWatts,
				ThermalState:      cfg.ThermalState,
				CloudConnectivity: cfg.CloudConnectivity,
			},
		),
	}
}

func (s *Service) Run(ctx context.Context) error {
	if strings.TrimSpace(s.cfg.NodeToken) == "" {
		return fmt.Errorf("runner NODE_TOKEN is required")
	}
	if err := s.cfg.Validate(); err != nil {
		return err
	}
	if err := s.registerNode(ctx); err != nil {
		return err
	}

	errs := make(chan error, 3)
	var wg sync.WaitGroup
	wg.Add(3)

	go func() {
		defer wg.Done()
		s.collector.Run(ctx, s.cfg.HeartbeatInterval)
		errs <- nil // collector exits cleanly on ctx cancel
	}()

	go func() {
		defer wg.Done()
		errs <- heartbeat.Loop(ctx, s.cfg, s.client, s.collector)
	}()

	go func() {
		defer wg.Done()
		errs <- jobs.Loop(ctx, s.cfg, s.client, s.executor)
	}()

	select {
	case <-ctx.Done():
		wg.Wait()
		return nil
	case err := <-errs:
		if errors.Is(err, context.Canceled) {
			wg.Wait()
			return nil
		}
		return err
	}
}

// registerNode sends the initial registration request to the gateway.
func (s *Service) registerNode(ctx context.Context) error {
	return s.client.RegisterNode(ctx, api.RegisterRequest{
		TenantID:        s.cfg.TenantID,
		NodeID:          s.cfg.NodeID,
		Name:            s.cfg.NodeName,
		NodeType:        s.cfg.NodeType,
		Address:         s.cfg.NodeAddress,
		Profile:         s.cfg.Profile,
		Executor:        s.cfg.Executor,
		OS:              s.cfg.OperatingSystem,
		Arch:            s.cfg.Architecture,
		Zone:            s.cfg.Zone,
		ProtocolVersion: s.cfg.ProtocolVersion,
		LeaseVersion:    s.cfg.LeaseVersion,
		AgentVersion:    s.cfg.AgentVersion,
		MaxConcurrency:  s.cfg.MaxConcurrency,
		CPUCores:        s.cfg.CPUCores,
		MemoryMB:        s.cfg.MemoryMB,
		GPUVRAMMB:       s.cfg.GPUVRAMMB,
		StorageMB:       s.cfg.StorageMB,
		Capabilities:    s.cfg.Capabilities,
		Metadata: map[string]any{
			"profile":         s.cfg.Profile,
			"runtime":         "go",
			"lease_seconds":   s.cfg.LeaseSeconds,
			"agent_version":   s.cfg.AgentVersion,
			"max_concurrency": s.cfg.MaxConcurrency,
			"cpu_cores":       s.cfg.CPUCores,
			"memory_mb":       s.cfg.MemoryMB,
			"gpu_vram_mb":     s.cfg.GPUVRAMMB,
			"storage_mb":      s.cfg.StorageMB,
		},
		AcceptedKinds:      s.cfg.AcceptedKinds,
		NetworkLatencyMs:   s.cfg.NetworkLatencyMs,
		BandwidthMbps:      s.cfg.BandwidthMbps,
		CachedDataKeys:     s.cfg.CachedDataKeys,
		PowerCapacityWatts: s.cfg.PowerCapacityWatts,
		CurrentPowerWatts:  s.cfg.CurrentPowerWatts,
		ThermalState:       s.cfg.ThermalState,
		CloudConnectivity:  s.cfg.CloudConnectivity,
	})
}
