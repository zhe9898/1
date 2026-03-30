// Package telemetry provides runtime system probing for dynamic edge telemetry.
//
// Static configuration values (from env vars) serve as initial defaults.
// The Collector periodically probes the gateway and local system to produce
// fresh Snapshot values consumed by the heartbeat loop.
package telemetry

import (
	"context"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Snapshot holds the current dynamic telemetry readings.
type Snapshot struct {
	NetworkLatencyMs  int
	CurrentPowerWatts int
	ThermalState      string
	CloudConnectivity string
}

// Collector periodically probes system state and gateway connectivity.
type Collector struct {
	mu         sync.RWMutex
	current    Snapshot
	defaults   Snapshot
	gatewayURL string
	httpClient *http.Client
}

// NewCollector creates a Collector with initial defaults from config.
// gatewayURL is used for latency / connectivity probing.
// httpClient should share the TLS configuration of the main API client.
func NewCollector(gatewayURL string, httpClient *http.Client, defaults Snapshot) *Collector {
	return &Collector{
		current:    defaults,
		defaults:   defaults,
		gatewayURL: strings.TrimRight(gatewayURL, "/"),
		httpClient: httpClient,
	}
}

// Get returns a thread-safe copy of the latest telemetry snapshot.
func (c *Collector) Get() Snapshot {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.current
}

// Run starts the periodic collection loop. Blocks until ctx is cancelled.
func (c *Collector) Run(ctx context.Context, interval time.Duration) {
	c.collect(ctx) // immediate first collection
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.collect(ctx)
		}
	}
}

// ── Internal collection ─────────────────────────────────────────────

func (c *Collector) collect(ctx context.Context) {
	latency, connectivity := c.probeGateway(ctx)
	snap := Snapshot{
		NetworkLatencyMs:  latency,
		CurrentPowerWatts: readSysPower(c.defaults.CurrentPowerWatts),
		ThermalState:      readSysThermal(c.defaults.ThermalState),
		CloudConnectivity: connectivity,
	}
	c.mu.Lock()
	c.current = snap
	c.mu.Unlock()
}

// probeGateway sends a single HTTP HEAD to gateway /healthz and returns
// (latencyMs, connectivityState). One probe → two readings.
func (c *Collector) probeGateway(ctx context.Context) (int, string) {
	if c.gatewayURL == "" {
		return c.defaults.NetworkLatencyMs, c.defaults.CloudConnectivity
	}

	probeCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(probeCtx, http.MethodHead, c.gatewayURL+"/healthz", nil)
	if err != nil {
		return c.defaults.NetworkLatencyMs, "offline"
	}

	start := time.Now()
	resp, err := c.httpClient.Do(req)
	if err != nil {
		log.Printf("[telemetry] gateway probe failed: %v", err)
		return c.defaults.NetworkLatencyMs, "offline"
	}
	resp.Body.Close()

	latencyMs := int(time.Since(start).Milliseconds())
	connectivity := "online"
	if resp.StatusCode >= 500 {
		connectivity = "degraded"
	}
	return latencyMs, connectivity
}

// ── Linux sysfs probes (fallback to defaults on non-Linux / error) ──

// readSysThermal reads /sys/class/thermal/thermal_zone0/temp (millidegrees C).
func readSysThermal(fallback string) string {
	data, err := os.ReadFile("/sys/class/thermal/thermal_zone0/temp")
	if err != nil {
		return fallback
	}
	milliC, err := strconv.Atoi(strings.TrimSpace(string(data)))
	if err != nil {
		return fallback
	}
	switch tempC := milliC / 1000; {
	case tempC >= 85:
		return "throttling"
	case tempC >= 70:
		return "hot"
	case tempC >= 45:
		return "normal"
	default:
		return "cool"
	}
}

// readSysPower reads /sys/class/power_supply/*/power_now (microwatts → watts).
func readSysPower(fallback int) int {
	matches, _ := filepath.Glob("/sys/class/power_supply/*/power_now")
	for _, path := range matches {
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		uW, err := strconv.Atoi(strings.TrimSpace(string(data)))
		if err != nil {
			continue
		}
		return uW / 1_000_000
	}
	return fallback
}
