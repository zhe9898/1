package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadDefaultsToHTTPSGateway(t *testing.T) {
	t.Setenv("GATEWAY_BASE_URL", "")
	t.Setenv("GATEWAY_CA_FILE", "")
	t.Setenv("GATEWAY_CERT_SHA256", "")
	t.Setenv("RUNNER_ALLOW_INSECURE_HTTP", "")

	cfg := Load()
	if cfg.GatewayBaseURL != "https://127.0.0.1:8000" {
		t.Fatalf("expected HTTPS default gateway, got %q", cfg.GatewayBaseURL)
	}
	if cfg.AllowInsecureHTTP {
		t.Fatalf("expected insecure HTTP opt-in to default to false")
	}
}

func TestValidateRejectsRemoteHTTPGateway(t *testing.T) {
	cfg := Config{
		GatewayBaseURL: "http://gateway.example.com:8000",
		NodeToken:      "node-token",
	}
	if err := cfg.Validate(); err == nil {
		t.Fatalf("expected remote plaintext gateway to be rejected")
	}
}

func TestValidateRejectsLoopbackHTTPWithoutOptIn(t *testing.T) {
	cfg := Config{
		GatewayBaseURL: "http://127.0.0.1:8000",
		NodeToken:      "node-token",
	}
	if err := cfg.Validate(); err == nil {
		t.Fatalf("expected loopback plaintext gateway to require explicit opt-in")
	}
}

func TestValidateAllowsLoopbackHTTPWithOptIn(t *testing.T) {
	cfg := Config{
		GatewayBaseURL:    "http://127.0.0.1:8000",
		AllowInsecureHTTP: true,
		NodeToken:         "node-token",
	}
	if err := cfg.Validate(); err != nil {
		t.Fatalf("expected loopback plaintext gateway with opt-in to validate, got %v", err)
	}
}

func TestValidateRejectsMalformedCAFile(t *testing.T) {
	dir := t.TempDir()
	caFile := filepath.Join(dir, "gateway-ca.pem")
	if err := os.WriteFile(caFile, []byte("not-a-certificate"), 0o600); err != nil {
		t.Fatalf("write temp CA file: %v", err)
	}

	cfg := Config{
		GatewayBaseURL: "https://gateway.example.com",
		GatewayCAFile:  caFile,
		NodeToken:      "node-token",
	}
	if err := cfg.Validate(); err == nil {
		t.Fatalf("expected malformed CA file to be rejected")
	}
}

func TestValidateRejectsMalformedFingerprint(t *testing.T) {
	cfg := Config{
		GatewayBaseURL:    "https://gateway.example.com",
		GatewayCertSHA256: "zz:not-a-fingerprint",
		NodeToken:         "node-token",
	}
	if err := cfg.Validate(); err == nil {
		t.Fatalf("expected malformed gateway certificate fingerprint to be rejected")
	}
}

func TestEffectiveAcceptedKindsUsesAcceptedKinds(t *testing.T) {
	cfg := Config{
		Capabilities:  []string{"cap-a", "cap-b"},
		AcceptedKinds: []string{"kind-x", "kind-y"},
	}
	got := cfg.EffectiveAcceptedKinds()
	if len(got) != 2 || got[0] != "kind-x" || got[1] != "kind-y" {
		t.Fatalf("expected AcceptedKinds when set, got %v", got)
	}
}

func TestEffectiveAcceptedKindsFallsBackToCapabilities(t *testing.T) {
	cfg := Config{
		Capabilities:  []string{"cap-a", "cap-b"},
		AcceptedKinds: []string{},
	}
	got := cfg.EffectiveAcceptedKinds()
	if len(got) != 2 || got[0] != "cap-a" || got[1] != "cap-b" {
		t.Fatalf("expected Capabilities fallback when AcceptedKinds empty, got %v", got)
	}
}
