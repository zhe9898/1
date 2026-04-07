package config

import (
	"crypto/x509"
	"encoding/pem"
	"fmt"
	"net"
	"net/url"
	"os"
	"runtime"
	"strconv"
	"strings"
	"time"

	"zen70/runner-agent/internal/device"
)

type Config struct {
	GatewayBaseURL    string
	GatewayCAFile     string
	GatewayCertSHA256 string
	AllowInsecureHTTP bool
	NodeID            string
	TenantID          string
	NodeToken         string
	NodeName          string
	NodeAddress       string
	NodeType          string
	Profile           string
	Executor          string
	OperatingSystem   string
	Architecture      string
	Zone              string
	ProtocolVersion   string
	LeaseVersion      string
	AgentVersion      string
	MaxConcurrency    int
	CPUCores          int
	MemoryMB          int
	GPUVRAMMB         int
	StorageMB         int
	Capabilities      []string
	// Edge computing telemetry
	AcceptedKinds      []string
	NetworkLatencyMs   int
	BandwidthMbps      int
	CachedDataKeys     []string
	PowerCapacityWatts int
	CurrentPowerWatts  int
	ThermalState       string
	CloudConnectivity  string
	// Cloud elasticity: when set, the agent presents this token at registration
	// so the backend can auto-activate this node without manual admin approval.
	CloudToken        string
	// DeviceProfile is the hardware classification reported to the backend.
	// When empty, Load() auto-detects the profile via /proc/cpuinfo and DMI.
	DeviceProfile     string
	HeartbeatInterval time.Duration
	PullInterval      time.Duration
	LeaseSeconds      int
}

func Load() Config {
	memoryMB := max(0, getenvInt("RUNNER_MEMORY_MB", 0))
	arch := getenv("RUNNER_ARCH", runtime.GOARCH)

	// Resolve device profile: honour explicit env var, otherwise auto-detect.
	deviceProfile := strings.TrimSpace(os.Getenv("RUNNER_DEVICE_PROFILE"))
	if deviceProfile == "" {
		deviceProfile = device.DetectProfile(arch, memoryMB)
	}

	return Config{
		GatewayBaseURL:     getenv("GATEWAY_BASE_URL", "https://127.0.0.1:8000"),
		GatewayCAFile:      strings.TrimSpace(os.Getenv("GATEWAY_CA_FILE")),
		GatewayCertSHA256:  strings.TrimSpace(os.Getenv("GATEWAY_CERT_SHA256")),
		AllowInsecureHTTP:  getenvBool("RUNNER_ALLOW_INSECURE_HTTP", false),
		NodeID:             getenv("RUNNER_NODE_ID", hostnameOr("runner-agent")),
		TenantID:           getenv("RUNNER_TENANT_ID", "default"),
		NodeToken:          getenvEither([]string{"NODE_TOKEN", "ZEN70_NODE_TOKEN"}, ""),
		NodeName:           getenv("RUNNER_NODE_NAME", hostnameOr("runner-agent")),
		NodeAddress:        os.Getenv("RUNNER_NODE_ADDRESS"),
		NodeType:           getenv("RUNNER_NODE_TYPE", "runner"),
		Profile:            getenv("RUNNER_PROFILE", "go-runner"),
		Executor:           getenv("RUNNER_EXECUTOR", "go-native"),
		OperatingSystem:    getenv("RUNNER_OS", runtime.GOOS),
		Architecture:       arch,
		Zone:               strings.TrimSpace(os.Getenv("RUNNER_ZONE")),
		ProtocolVersion:    getenv("RUNNER_PROTOCOL_VERSION", "runner.v1"),
		LeaseVersion:       getenv("RUNNER_LEASE_VERSION", "job-lease.v1"),
		AgentVersion:       getenv("RUNNER_AGENT_VERSION", "runner-agent.v1"),
		MaxConcurrency:     max(1, getenvInt("RUNNER_MAX_CONCURRENCY", 1)),
		CPUCores:           max(1, getenvInt("RUNNER_CPU_CORES", runtime.NumCPU())),
		MemoryMB:           memoryMB,
		GPUVRAMMB:          max(0, getenvInt("RUNNER_GPU_VRAM_MB", 0)),
		StorageMB:          max(0, getenvInt("RUNNER_STORAGE_MB", 0)),
		Capabilities:       splitCSV(getenv("RUNNER_CAPABILITIES", "connector.invoke,noop")),
		AcceptedKinds:      splitCSV(getenv("RUNNER_ACCEPTED_KINDS", "")),
		NetworkLatencyMs:   max(0, getenvInt("RUNNER_NETWORK_LATENCY_MS", 0)),
		BandwidthMbps:      max(0, getenvInt("RUNNER_BANDWIDTH_MBPS", 0)),
		CachedDataKeys:     splitCSV(getenv("RUNNER_CACHED_DATA_KEYS", "")),
		PowerCapacityWatts: max(0, getenvInt("RUNNER_POWER_CAPACITY_WATTS", 0)),
		CurrentPowerWatts:  max(0, getenvInt("RUNNER_CURRENT_POWER_WATTS", 0)),
		ThermalState:       getenv("RUNNER_THERMAL_STATE", "normal"),
		CloudConnectivity:  getenv("RUNNER_CLOUD_CONNECTIVITY", "online"),
		CloudToken:         strings.TrimSpace(os.Getenv("RUNNER_CLOUD_TOKEN")),
		DeviceProfile:      deviceProfile,
		HeartbeatInterval:  time.Duration(getenvInt("RUNNER_HEARTBEAT_SECONDS", 15)) * time.Second,
		PullInterval:       time.Duration(getenvInt("RUNNER_PULL_SECONDS", 5)) * time.Second,
		LeaseSeconds:       getenvInt("RUNNER_LEASE_SECONDS", 30),
	}
}

func (c Config) Validate() error {
	if strings.TrimSpace(c.GatewayBaseURL) == "" {
		return fmt.Errorf("GATEWAY_BASE_URL is required")
	}
	parsed, err := url.Parse(c.GatewayBaseURL)
	if err != nil {
		return fmt.Errorf("invalid GATEWAY_BASE_URL: %w", err)
	}
	if parsed.Hostname() == "" {
		return fmt.Errorf("GATEWAY_BASE_URL must include a host")
	}

	scheme := strings.ToLower(parsed.Scheme)
	switch scheme {
	case "https":
	case "http":
		if !isLoopbackHost(parsed.Hostname()) {
			return fmt.Errorf("runner-agent requires HTTPS for non-loopback gateway hosts")
		}
		if !c.AllowInsecureHTTP {
			return fmt.Errorf("runner-agent requires HTTPS by default; set RUNNER_ALLOW_INSECURE_HTTP=true only for localhost development")
		}
	default:
		return fmt.Errorf("unsupported gateway URL scheme %q", parsed.Scheme)
	}

	if rawPin := strings.TrimSpace(c.GatewayCertSHA256); rawPin != "" {
		normalizedPin := normalizeFingerprint(rawPin)
		if normalizedPin == "" || len(normalizedPin) != 64 {
			return fmt.Errorf("GATEWAY_CERT_SHA256 must be a 64-character SHA256 fingerprint")
		}
	}
	if c.GatewayCAFile != "" {
		pemBytes, readErr := os.ReadFile(c.GatewayCAFile)
		if readErr != nil {
			return fmt.Errorf("GATEWAY_CA_FILE is not readable: %w", readErr)
		}
		if info, statErr := os.Stat(c.GatewayCAFile); statErr != nil {
			return fmt.Errorf("GATEWAY_CA_FILE is not readable: %w", statErr)
		} else if info.IsDir() {
			return fmt.Errorf("GATEWAY_CA_FILE must be a file, got directory")
		}
		if block, _ := pem.Decode(pemBytes); block == nil {
			return fmt.Errorf("GATEWAY_CA_FILE does not contain a PEM certificate")
		}
		pool := x509.NewCertPool()
		if !pool.AppendCertsFromPEM(pemBytes) {
			return fmt.Errorf("GATEWAY_CA_FILE does not contain a valid trust chain")
		}
	}
	return nil
}

func getenv(key string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}

func getenvEither(keys []string, fallback string) string {
	for _, key := range keys {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return fallback
}

func getenvBool(key string, fallback bool) bool {
	raw := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	if raw == "" {
		return fallback
	}
	switch raw {
	case "1", "true", "yes", "on":
		return true
	case "0", "false", "no", "off":
		return false
	default:
		return fallback
	}
}

func getenvInt(key string, fallback int) int {
	raw := strings.TrimSpace(os.Getenv(key))
	if raw == "" {
		return fallback
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return fallback
	}
	return value
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func hostnameOr(fallback string) string {
	name, err := os.Hostname()
	if err != nil || strings.TrimSpace(name) == "" {
		return fallback
	}
	return name
}

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func normalizeFingerprint(value string) string {
	cleaned := strings.ToLower(strings.TrimSpace(value))
	cleaned = strings.ReplaceAll(cleaned, ":", "")
	cleaned = strings.ReplaceAll(cleaned, " ", "")
	for _, r := range cleaned {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return ""
		}
	}
	return cleaned
}

// EffectiveAcceptedKinds returns AcceptedKinds if explicitly configured,
// otherwise falls back to Capabilities for backward compatibility.
func (c Config) EffectiveAcceptedKinds() []string {
	if len(c.AcceptedKinds) > 0 {
		return c.AcceptedKinds
	}
	return c.Capabilities
}

func isLoopbackHost(host string) bool {
	host = strings.TrimSpace(strings.ToLower(host))
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
