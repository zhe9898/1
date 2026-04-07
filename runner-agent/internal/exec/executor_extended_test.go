package runnerexec_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	runnerexec "zen70/runner-agent/internal/exec"
)

// ── healthcheck HTTP tests ──────────────────────────────────────────

func TestHealthcheckRejectsNonStringTarget(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target": []any{"http://example.test"},
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload error")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestContainerRunRejectsMixedCommandList(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "container.run", map[string]any{
		"image":   "busybox:latest",
		"command": []any{"echo", 123},
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload error")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestHealthcheckHTTPSuccess(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("User-Agent") != "zen70-healthcheck/1.0" {
			t.Errorf("expected zen70 user-agent, got %q", r.Header.Get("User-Agent"))
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":     srv.URL + "/health",
		"check_type": "http",
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result.Summary, "OK") {
		t.Fatalf("expected OK in summary, got %q", result.Summary)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	if out["healthy"] != true {
		t.Fatalf("expected healthy=true, got %v", out["healthy"])
	}
	if out["latency_ms"] == nil || out["latency_ms"].(float64) < 0 {
		t.Fatalf("expected non-negative latency_ms, got %v", out["latency_ms"])
	}
}

func TestHealthcheckHTTPUnhealthy(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(503)
		_, _ = w.Write([]byte(`service down`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	_, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":          srv.URL,
		"check_type":      "http",
		"expected_status": float64(200),
	}, 30)
	if err == nil {
		t.Fatalf("expected error for unhealthy check")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "execution_error" {
		t.Fatalf("expected execution_error, got %q", execErr.Category)
	}
}

func TestHealthcheckCustomExpectedStatus(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(204) // No Content
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":          srv.URL,
		"check_type":      "http",
		"expected_status": float64(204),
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error (204 should match expected): %v", err)
	}
	var out map[string]any
	_ = json.Unmarshal([]byte(result.Output), &out)
	if out["healthy"] != true {
		t.Fatalf("expected healthy=true for matching 204")
	}
}

func TestHealthcheckHTTPWithHeaders(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer test-token" {
			w.WriteHeader(401)
			return
		}
		w.WriteHeader(200)
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":     srv.URL,
		"check_type": "http",
		"headers":    map[string]any{"Authorization": "Bearer test-token"},
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result.Summary, "OK") {
		t.Fatalf("expected OK, got %q", result.Summary)
	}
}

func TestHealthcheckMissingTarget(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "healthcheck", map[string]any{}, 30)
	if err == nil {
		t.Fatalf("expected error for missing target")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestHealthcheckInvalidCheckType(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":     "example.com",
		"check_type": "grpc",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for unsupported check_type")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

// ── healthcheck TCP tests ───────────────────────────────────────────

func TestHealthcheckTCPSuccess(t *testing.T) {
	t.Parallel()

	// Use the HTTP test server's address for TCP check — it's listening on TCP
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(200)
	}))
	defer srv.Close()

	// Extract host:port from srv.URL (e.g., "http://127.0.0.1:PORT")
	addr := strings.TrimPrefix(srv.URL, "http://")

	exec := newTestExecutor(nil)
	result, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":     addr,
		"check_type": "tcp",
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var out map[string]any
	_ = json.Unmarshal([]byte(result.Output), &out)
	if out["healthy"] != true {
		t.Fatalf("expected healthy=true for TCP probe, got %v", out["healthy"])
	}
	if !strings.Contains(result.Summary, "OK") {
		t.Fatalf("expected OK in summary, got %q", result.Summary)
	}
}

func TestHealthcheckTCPFail(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	// Connect to a port that's (almost certainly) not listening
	_, err := exec.Run(context.Background(), "healthcheck", map[string]any{
		"target":     "127.0.0.1:1",
		"check_type": "tcp",
		"timeout_ms": float64(500),
	}, 30)
	if err == nil {
		t.Fatalf("expected error for refused TCP connection")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "transient" {
		t.Fatalf("expected transient, got %q", execErr.Category)
	}
}

// ── file.transfer tests ─────────────────────────────────────────────

func TestFileTransferBasic(t *testing.T) {
	t.Parallel()

	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "source.txt")
	dstPath := filepath.Join(tmpDir, "dest.txt")

	content := []byte("hello from zen70 file.transfer test")
	if err := os.WriteFile(srcPath, content, 0o644); err != nil {
		t.Fatalf("failed to create test source: %v", err)
	}

	exec := newTestExecutor(nil)
	result, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": srcPath,
		"dst": dstPath,
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Verify output
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	if out["bytes"] != float64(len(content)) {
		t.Fatalf("expected %d bytes, got %v", len(content), out["bytes"])
	}
	if out["sha256"] == nil || out["sha256"] == "" {
		t.Fatalf("expected non-empty sha256")
	}

	// Verify file content
	got, err := os.ReadFile(dstPath)
	if err != nil {
		t.Fatalf("failed to read dest: %v", err)
	}
	if string(got) != string(content) {
		t.Fatalf("content mismatch: %q vs %q", got, content)
	}
}

func TestFileTransferWithSHA256Verify(t *testing.T) {
	t.Parallel()

	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "src.bin")
	dstPath := filepath.Join(tmpDir, "dst.bin")

	content := []byte("integrity-checked-content")
	if err := os.WriteFile(srcPath, content, 0o644); err != nil {
		t.Fatalf("failed to create test source: %v", err)
	}

	// Pre-compute correct SHA-256
	// sha256("integrity-checked-content") = known hash
	exec := newTestExecutor(nil)

	// First transfer without verify to get the hash
	result, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src":       srcPath,
		"dst":       dstPath,
		"overwrite": true,
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var out map[string]any
	_ = json.Unmarshal([]byte(result.Output), &out)
	correctHash := out["sha256"].(string)

	// Now transfer again with correct hash
	dstPath2 := filepath.Join(tmpDir, "dst2.bin")
	_, err = exec.Run(context.Background(), "file.transfer", map[string]any{
		"src":           srcPath,
		"dst":           dstPath2,
		"verify_sha256": correctHash,
	}, 30)
	if err != nil {
		t.Fatalf("should succeed with correct hash: %v", err)
	}

	// Now try with wrong hash
	dstPath3 := filepath.Join(tmpDir, "dst3.bin")
	_, err = exec.Run(context.Background(), "file.transfer", map[string]any{
		"src":           srcPath,
		"dst":           dstPath3,
		"verify_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for SHA-256 mismatch")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "execution_error" {
		t.Fatalf("expected execution_error for hash mismatch, got %q", execErr.Category)
	}

	// Verify the bad dest was cleaned up
	if _, err := os.Stat(dstPath3); !os.IsNotExist(err) {
		t.Fatalf("expected destination to be removed after hash mismatch")
	}
}

func TestFileTransferOverwriteProtection(t *testing.T) {
	t.Parallel()

	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "src.txt")
	dstPath := filepath.Join(tmpDir, "dst.txt")

	_ = os.WriteFile(srcPath, []byte("source"), 0o644)
	_ = os.WriteFile(dstPath, []byte("existing"), 0o644)

	exec := newTestExecutor(nil)

	// Without overwrite → should fail
	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": srcPath,
		"dst": dstPath,
	}, 30)
	if err == nil {
		t.Fatalf("expected error when destination exists and overwrite=false")
	}

	// With overwrite → should succeed
	_, err = exec.Run(context.Background(), "file.transfer", map[string]any{
		"src":       srcPath,
		"dst":       dstPath,
		"overwrite": true,
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error with overwrite=true: %v", err)
	}
}

func TestFileTransferMissingFields(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": "/tmp/test",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing dst")
	}

	_, err = exec.Run(context.Background(), "file.transfer", map[string]any{
		"dst": "/tmp/test",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing src")
	}
}

func TestFileTransferRejectsURIPaths(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": "file:///etc/shadow",
		"dst": "/tmp/out.txt",
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload for URI source path")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestFileTransferRejectsPathsOutsideConfiguredRoots(t *testing.T) {
	allowedRoot := t.TempDir()
	outsideRoot := t.TempDir()
	t.Setenv("ZEN70_FILE_TRANSFER_ROOTS", allowedRoot)

	srcPath := filepath.Join(outsideRoot, "src.txt")
	dstPath := filepath.Join(allowedRoot, "dst.txt")
	if err := os.WriteFile(srcPath, []byte("blocked"), 0o644); err != nil {
		t.Fatalf("failed to create source file: %v", err)
	}

	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": srcPath,
		"dst": dstPath,
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload for source outside allowed roots")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestFileTransferSourceNotFound(t *testing.T) {
	t.Parallel()

	tmpDir := t.TempDir()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src": filepath.Join(tmpDir, "nonexistent.txt"),
		"dst": filepath.Join(tmpDir, "dst.txt"),
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing source")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "not_found" {
		t.Fatalf("expected not_found, got %q", execErr.Category)
	}
}

func TestFileTransferMkdir(t *testing.T) {
	t.Parallel()

	tmpDir := t.TempDir()
	srcPath := filepath.Join(tmpDir, "src.txt")
	dstPath := filepath.Join(tmpDir, "sub", "dir", "deep", "dst.txt")

	_ = os.WriteFile(srcPath, []byte("nested dir test"), 0o644)

	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "file.transfer", map[string]any{
		"src":   srcPath,
		"dst":   dstPath,
		"mkdir": true,
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	got, err := os.ReadFile(dstPath)
	if err != nil {
		t.Fatalf("failed to read dest: %v", err)
	}
	if string(got) != "nested dir test" {
		t.Fatalf("unexpected content: %q", got)
	}
}

func TestDataSyncRejectsNonRsyncURIs(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "data.sync", map[string]any{
		"source_uri": "https://example.test/export",
		"dest_uri":   "rsync://cluster-a/archive",
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload for non-rsync source URI")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestWasmRunRejectsPlainHTTPModuleURI(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	_, err := exec.Run(context.Background(), "wasm.run", map[string]any{
		"module_uri": "http://example.test/malicious.wasm",
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid payload for plain HTTP wasm URI")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}
