package api

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"zen70/runner-agent/internal/config"
)

// DefaultAPIClientTimeout is the timeout for all API HTTP requests to the gateway.
const DefaultAPIClientTimeout = 30 * time.Second

type Client struct {
	baseURL    string
	nodeToken  string
	httpClient *http.Client
}

type RegisterRequest struct {
	TenantID           string         `json:"tenant_id"`
	NodeID             string         `json:"node_id"`
	Name               string         `json:"name"`
	NodeType           string         `json:"node_type"`
	Address            string         `json:"address,omitempty"`
	Profile            string         `json:"profile"`
	Executor           string         `json:"executor"`
	OS                 string         `json:"os"`
	Arch               string         `json:"arch"`
	Zone               string         `json:"zone,omitempty"`
	ProtocolVersion    string         `json:"protocol_version"`
	LeaseVersion       string         `json:"lease_version"`
	AgentVersion       string         `json:"agent_version,omitempty"`
	MaxConcurrency     int            `json:"max_concurrency"`
	CPUCores           int            `json:"cpu_cores"`
	MemoryMB           int            `json:"memory_mb"`
	GPUVRAMMB          int            `json:"gpu_vram_mb"`
	StorageMB          int            `json:"storage_mb"`
	Capabilities       []string       `json:"capabilities"`
	Metadata           map[string]any `json:"metadata"`
	AcceptedKinds      []string       `json:"accepted_kinds,omitempty"`
	NetworkLatencyMs   int            `json:"network_latency_ms,omitempty"`
	BandwidthMbps      int            `json:"bandwidth_mbps,omitempty"`
	CachedDataKeys     []string       `json:"cached_data_keys,omitempty"`
	PowerCapacityWatts int            `json:"power_capacity_watts,omitempty"`
	CurrentPowerWatts  int            `json:"current_power_watts,omitempty"`
	ThermalState       string         `json:"thermal_state,omitempty"`
	CloudConnectivity  string         `json:"cloud_connectivity,omitempty"`
}

type HeartbeatRequest struct {
	TenantID           string         `json:"tenant_id"`
	NodeID             string         `json:"node_id"`
	Name               string         `json:"name"`
	NodeType           string         `json:"node_type"`
	Address            string         `json:"address,omitempty"`
	Profile            string         `json:"profile"`
	Executor           string         `json:"executor"`
	OS                 string         `json:"os"`
	Arch               string         `json:"arch"`
	Zone               string         `json:"zone,omitempty"`
	ProtocolVersion    string         `json:"protocol_version"`
	LeaseVersion       string         `json:"lease_version"`
	AgentVersion       string         `json:"agent_version,omitempty"`
	MaxConcurrency     int            `json:"max_concurrency"`
	CPUCores           int            `json:"cpu_cores"`
	MemoryMB           int            `json:"memory_mb"`
	GPUVRAMMB          int            `json:"gpu_vram_mb"`
	StorageMB          int            `json:"storage_mb"`
	Status             string         `json:"status"`
	HealthReason       string         `json:"health_reason,omitempty"`
	Capabilities       []string       `json:"capabilities"`
	Metadata           map[string]any `json:"metadata"`
	AcceptedKinds      []string       `json:"accepted_kinds,omitempty"`
	NetworkLatencyMs   int            `json:"network_latency_ms,omitempty"`
	BandwidthMbps      int            `json:"bandwidth_mbps,omitempty"`
	CachedDataKeys     []string       `json:"cached_data_keys,omitempty"`
	PowerCapacityWatts int            `json:"power_capacity_watts,omitempty"`
	CurrentPowerWatts  int            `json:"current_power_watts,omitempty"`
	ThermalState       string         `json:"thermal_state,omitempty"`
	CloudConnectivity  string         `json:"cloud_connectivity,omitempty"`
}

type PullRequest struct {
	TenantID      string   `json:"tenant_id"`
	NodeID        string   `json:"node_id"`
	Limit         int      `json:"limit"`
	AcceptedKinds []string `json:"accepted_kinds"`
}

type Job struct {
	JobID          string         `json:"job_id"`
	Kind           string         `json:"kind"`
	Payload        map[string]any `json:"payload"`
	Status         string         `json:"status"`
	NodeID         string         `json:"node_id"`
	IdempotencyKey *string        `json:"idempotency_key"`
	Attempt        int            `json:"attempt"`
	LeaseToken     string         `json:"lease_token"`
	LeaseSeconds   int            `json:"lease_seconds"`
	LeasedUntil    string         `json:"leased_until"`
}

type JobResultRequest struct {
	TenantID   string         `json:"tenant_id"`
	NodeID     string         `json:"node_id"`
	LeaseToken string         `json:"lease_token"`
	Attempt    int            `json:"attempt"`
	Result     map[string]any `json:"result"`
	Log        string         `json:"log,omitempty"`
}

type JobFailRequest struct {
	TenantID        string         `json:"tenant_id"`
	NodeID          string         `json:"node_id"`
	LeaseToken      string         `json:"lease_token"`
	Attempt         int            `json:"attempt"`
	Error           string         `json:"error"`
	FailureCategory *string        `json:"failure_category,omitempty"`
	ErrorDetails    map[string]any `json:"error_details,omitempty"`
	Log             string         `json:"log,omitempty"`
}

type JobProgressRequest struct {
	TenantID   string `json:"tenant_id"`
	NodeID     string `json:"node_id"`
	LeaseToken string `json:"lease_token"`
	Attempt    int    `json:"attempt"`
	Progress   int    `json:"progress"`
	Message    string `json:"message,omitempty"`
	Log        string `json:"log,omitempty"`
}

type JobRenewRequest struct {
	TenantID      string `json:"tenant_id"`
	NodeID        string `json:"node_id"`
	LeaseToken    string `json:"lease_token"`
	Attempt       int    `json:"attempt"`
	ExtendSeconds int    `json:"extend_seconds"`
	Log           string `json:"log,omitempty"`
}

type envelope[T any] struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Data    T      `json:"data"`
}

func New(cfg config.Config) *Client {
	return &Client{
		baseURL:    strings.TrimRight(cfg.GatewayBaseURL, "/"),
		nodeToken:  strings.TrimSpace(cfg.NodeToken),
		httpClient: buildHTTPClient(cfg),
	}
}

func buildHTTPClient(cfg config.Config) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{
		MinVersion: tls.VersionTLS12,
	}

	if strings.TrimSpace(cfg.GatewayCAFile) != "" {
		rootCAs, err := loadCertPool(cfg.GatewayCAFile)
		if err == nil {
			transport.TLSClientConfig.RootCAs = rootCAs
		}
	}

	if pin := normalizedFingerprint(cfg.GatewayCertSHA256); pin != "" {
		transport.TLSClientConfig.VerifyConnection = func(state tls.ConnectionState) error {
			if len(state.PeerCertificates) == 0 {
				return fmt.Errorf("gateway TLS handshake did not present a certificate")
			}
			sum := sha256.Sum256(state.PeerCertificates[0].Raw)
			if fmt.Sprintf("%x", sum[:]) != pin {
				return fmt.Errorf("gateway certificate fingerprint mismatch")
			}
			return nil
		}
	}

	return &http.Client{
		Timeout:   DefaultAPIClientTimeout,
		Transport: transport,
	}
}

func loadCertPool(path string) (*x509.CertPool, error) {
	pemBytes, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if block, _ := pem.Decode(pemBytes); block == nil {
		return nil, fmt.Errorf("CA file %s does not contain a PEM certificate", path)
	}
	pool, err := x509.SystemCertPool()
	if err != nil || pool == nil {
		pool = x509.NewCertPool()
	}
	if !pool.AppendCertsFromPEM(pemBytes) {
		return nil, fmt.Errorf("failed to append CA certificates from %s", path)
	}
	return pool, nil
}

func normalizedFingerprint(value string) string {
	cleaned := strings.ToLower(strings.TrimSpace(value))
	cleaned = strings.ReplaceAll(cleaned, ":", "")
	cleaned = strings.ReplaceAll(cleaned, " ", "")
	return cleaned
}

func (c *Client) RegisterNode(ctx context.Context, payload RegisterRequest) error {
	return c.post(ctx, "/api/v1/nodes/register", payload, nil)
}

// HTTPClient returns the underlying *http.Client for reuse (e.g. telemetry probes).
func (c *Client) HTTPClient() *http.Client { return c.httpClient }

func (c *Client) HeartbeatNode(ctx context.Context, payload HeartbeatRequest) error {
	return c.post(ctx, "/api/v1/nodes/heartbeat", payload, nil)
}

func (c *Client) PullJobs(ctx context.Context, payload PullRequest) ([]Job, error) {
	var jobs []Job
	if err := c.post(ctx, "/api/v1/jobs/pull", payload, &jobs); err != nil {
		return nil, err
	}
	return jobs, nil
}

func (c *Client) SendResult(ctx context.Context, jobID string, payload JobResultRequest) error {
	return c.post(ctx, fmt.Sprintf("/api/v1/jobs/%s/result", jobID), payload, nil)
}

func (c *Client) SendFailure(ctx context.Context, jobID string, payload JobFailRequest) error {
	return c.post(ctx, fmt.Sprintf("/api/v1/jobs/%s/fail", jobID), payload, nil)
}

func (c *Client) SendProgress(ctx context.Context, jobID string, payload JobProgressRequest) error {
	return c.post(ctx, fmt.Sprintf("/api/v1/jobs/%s/progress", jobID), payload, nil)
}

func (c *Client) RenewLease(ctx context.Context, jobID string, payload JobRenewRequest) error {
	return c.post(ctx, fmt.Sprintf("/api/v1/jobs/%s/renew", jobID), payload, nil)
}

func (c *Client) post(ctx context.Context, path string, payload any, out any) error {
	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.nodeToken)

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("request %s failed: %s", path, strings.TrimSpace(string(raw)))
	}

	if out == nil {
		return nil
	}

	var wrapped envelope[json.RawMessage]
	if err := json.NewDecoder(resp.Body).Decode(&wrapped); err == nil && len(wrapped.Data) > 0 {
		return json.Unmarshal(wrapped.Data, out)
	}

	_, err = io.Copy(io.Discard, resp.Body)
	return err
}
