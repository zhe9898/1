package runnerexec_test

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"runtime"
	"strings"
	"testing"
	"time"

	runnerexec "zen70/runner-agent/internal/exec"
)

func newTestExecutor(httpClient *http.Client) *runnerexec.Executor {
	return runnerexec.New(runnerexec.Config{
		DefaultTimeoutSeconds: 10,
		MaxOutputBytes:        4096,
	}, httpClient)
}

// ── noop tests ──────────────────────────────────────────────────────

func TestNoopBasic(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	result, err := exec.Run(context.Background(), "noop", map[string]any{}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Summary != "noop completed" {
		t.Fatalf("unexpected summary: %q", result.Summary)
	}
}

func TestNoopRespectsCancel(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := exec.Run(ctx, "noop", map[string]any{"delay_ms": 5000.0}, 30)
	if err == nil {
		t.Fatalf("expected cancellation error")
	}
}

// ── connector.invoke local echo tests ───────────────────────────────

func TestConnectorInvokeLocalEcho(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	result, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id":   "temp-sensor-01",
		"connector_kind": "mqtt",
		"action":         "read",
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Summary != "connector.invoke: temp-sensor-01/read" {
		t.Fatalf("unexpected summary: %q", result.Summary)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	if out["status"] != "executed_by_go_runner" {
		t.Fatalf("expected local echo status, got %v", out["status"])
	}
}

func TestConnectorInvokeMissingFields(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id": "x",
	}, 30)
	if err == nil {
		t.Fatalf("expected invalid_payload error")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

// ── connector.invoke real HTTP tests ────────────────────────────────

func TestConnectorInvokeHTTPSuccess(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if ct := r.Header.Get("Content-Type"); ct != "application/json" {
			t.Errorf("expected application/json, got %s", ct)
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.WriteHeader(200)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"result":  "ok",
			"action":  body["action"],
			"reading": 23.5,
		})
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id":   "sensor-01",
		"connector_kind": "http",
		"action":         "read_temperature",
		"endpoint":       srv.URL,
		"parameters":     map[string]any{"unit": "celsius"},
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if result.Summary == "" {
		t.Fatalf("expected non-empty summary")
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	if out["result"] != "ok" {
		t.Fatalf("expected result=ok, got %v", out["result"])
	}
}

func TestConnectorInvokeHTTP4xx(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(404)
		_, _ = w.Write([]byte(`{"error":"connector not found"}`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	_, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id": "missing",
		"action":       "read",
		"endpoint":     srv.URL,
	}, 30)
	if err == nil {
		t.Fatalf("expected error for 404")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "not_found" {
		t.Fatalf("expected not_found, got %q", execErr.Category)
	}
}

func TestConnectorInvokeHTTP5xx(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(503)
		_, _ = w.Write([]byte(`service unavailable`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	_, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id": "sensor-01",
		"action":       "read",
		"endpoint":     srv.URL,
	}, 30)
	if err == nil {
		t.Fatalf("expected error for 503")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "transient" {
		t.Fatalf("expected transient, got %q", execErr.Category)
	}
}

func TestConnectorInvokeHTTPTimeout(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(5 * time.Second) // will be killed by context
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := exec.Run(ctx, "connector.invoke", map[string]any{
		"connector_id": "slow-sensor",
		"action":       "read",
		"endpoint":     srv.URL,
	}, 1) // 1 second lease → short timeout
	if err == nil {
		t.Fatalf("expected timeout error")
	}
}

// ── http.request tests ──────────────────────────────────────────────

func TestHTTPRequestGET(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("expected GET, got %s", r.Method)
		}
		w.WriteHeader(200)
		_, _ = w.Write([]byte(`{"status":"healthy"}`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "http.request", map[string]any{
		"url":    srv.URL + "/healthz",
		"method": "GET",
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output: %v", err)
	}
	if out["status_code"] != float64(200) {
		t.Fatalf("expected status 200, got %v", out["status_code"])
	}
}

func TestHTTPRequestPOSTWithHeaders(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.Header.Get("X-Custom") != "test-val" {
			t.Errorf("expected custom header, got %q", r.Header.Get("X-Custom"))
		}
		w.WriteHeader(201)
		_, _ = w.Write([]byte(`{"created":true}`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "http.request", map[string]any{
		"url":     srv.URL,
		"method":  "POST",
		"body":    map[string]any{"key": "val"},
		"headers": map[string]any{"X-Custom": "test-val"},
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var out map[string]any
	_ = json.Unmarshal([]byte(result.Output), &out)
	if out["status_code"] != float64(201) {
		t.Fatalf("expected 201, got %v", out["status_code"])
	}
}

func TestHTTPRequestMissingURL(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "http.request", map[string]any{}, 30)
	if err == nil {
		t.Fatalf("expected error for missing url")
	}
}

// ── unsupported kind ────────────────────────────────────────────────

func TestUnsupportedKind(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "alien.kind", map[string]any{}, 30)
	if err == nil {
		t.Fatalf("expected error for unsupported kind")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

// ── script.run tests ────────────────────────────────────────────────

func TestScriptRunBasic(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	cmd := "echo hello-zen70"
	if runtime.GOOS == "windows" {
		cmd = "echo hello-zen70"
	}

	result, err := exec.Run(context.Background(), "script.run", map[string]any{
		"command": cmd,
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	stdout, _ := out["stdout"].(string)
	if !strings.Contains(stdout, "hello-zen70") {
		t.Fatalf("expected stdout to contain hello-zen70, got %q", stdout)
	}
	if out["exit_code"] != float64(0) {
		t.Fatalf("expected exit_code 0, got %v", out["exit_code"])
	}
	if !strings.Contains(result.Summary, "exited 0") {
		t.Fatalf("expected summary to mention exit 0, got %q", result.Summary)
	}
}

func TestScriptRunFailure(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)

	// "exit 42" should produce a non-zero exit code
	cmd := "exit 42"
	if runtime.GOOS == "windows" {
		cmd = "exit /b 42"
	}

	_, err := exec.Run(context.Background(), "script.run", map[string]any{
		"command": cmd,
	}, 30)
	if err == nil {
		t.Fatalf("expected error for non-zero exit")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	// exit code 42 maps to execution_error
	if execErr.Category != "execution_error" {
		t.Fatalf("expected execution_error, got %q", execErr.Category)
	}
}

func TestScriptRunMissingCommand(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "script.run", map[string]any{}, 30)
	if err == nil {
		t.Fatalf("expected error for missing command")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestScriptRunRespectsCancel(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	cmd := "sleep 10"
	if runtime.GOOS == "windows" {
		cmd = "timeout /t 10 /nobreak"
	}

	_, err := exec.Run(ctx, "script.run", map[string]any{
		"command": cmd,
	}, 1)
	if err == nil {
		t.Fatalf("expected timeout/cancel error")
	}
}

// ── docker.exec tests ───────────────────────────────────────────────

func TestDockerExecMissingContainer(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "docker.exec", map[string]any{
		"command": "ls",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing container")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestDockerExecMissingCommand(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "docker.exec", map[string]any{
		"container": "my-container",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing command")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestDockerExecCommandList(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	// This will fail because docker isn't available in test, but it should
	// NOT fail with invalid_payload — the payload parsing should succeed.
	_, err := exec.Run(context.Background(), "docker.exec", map[string]any{
		"container": "test-container",
		"command":   []any{"echo", "hello"},
	}, 30)
	// We expect an execution error (docker not found), not invalid_payload
	if err == nil {
		t.Logf("docker.exec succeeded (docker available in test env)")
		return
	}
	var execErr *runnerexec.ExecError
	if asExecError(err, &execErr) && execErr.Category == "invalid_payload" {
		t.Fatalf("payload should be valid; got invalid_payload: %s", execErr.Message)
	}
}

// ── cron.trigger tests ──────────────────────────────────────────────

func TestCronTriggerSuccess(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.Header.Get("X-Zen70-Trigger") != "cron" {
			t.Errorf("expected cron trigger header")
		}
		if r.Header.Get("X-Zen70-Cron-Name") != "daily-cleanup" {
			t.Errorf("expected cron name header, got %q", r.Header.Get("X-Zen70-Cron-Name"))
		}
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body["trigger"] != "cron" {
			t.Errorf("expected trigger=cron in body, got %v", body["trigger"])
		}
		w.WriteHeader(200)
		_ = json.NewEncoder(w).Encode(map[string]any{"cleaned": 42})
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	result, err := exec.Run(context.Background(), "cron.trigger", map[string]any{
		"webhook_url": srv.URL,
		"cron_name":   "daily-cleanup",
		"body":        map[string]any{"retention_days": 30},
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.Contains(result.Summary, "daily-cleanup") {
		t.Fatalf("expected summary to mention cron_name, got %q", result.Summary)
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(result.Output), &out); err != nil {
		t.Fatalf("invalid output JSON: %v", err)
	}
	if out["status_code"] != float64(200) {
		t.Fatalf("expected status 200, got %v", out["status_code"])
	}
}

func TestCronTriggerMissingURL(t *testing.T) {
	t.Parallel()
	exec := newTestExecutor(nil)
	_, err := exec.Run(context.Background(), "cron.trigger", map[string]any{
		"cron_name": "test",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for missing webhook_url")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "invalid_payload" {
		t.Fatalf("expected invalid_payload, got %q", execErr.Category)
	}
}

func TestCronTrigger5xx(t *testing.T) {
	t.Parallel()

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(500)
		_, _ = w.Write([]byte(`internal error`))
	}))
	defer srv.Close()

	exec := newTestExecutor(srv.Client())
	_, err := exec.Run(context.Background(), "cron.trigger", map[string]any{
		"webhook_url": srv.URL,
		"cron_name":   "failing-cron",
	}, 30)
	if err == nil {
		t.Fatalf("expected error for 500 response")
	}
	var execErr *runnerexec.ExecError
	if !asExecError(err, &execErr) {
		t.Fatalf("expected ExecError, got %T", err)
	}
	if execErr.Category != "transient" {
		t.Fatalf("expected transient, got %q", execErr.Category)
	}
}

// ── output truncation ───────────────────────────────────────────────

func TestOutputTruncation(t *testing.T) {
	t.Parallel()
	exec := runnerexec.New(runnerexec.Config{
		DefaultTimeoutSeconds: 10,
		MaxOutputBytes:        32,
	}, nil)
	// noop returns "{}" which is small enough; we can't easily test truncation
	// on noop, so we use connector.invoke local echo with long parameters.
	result, err := exec.Run(context.Background(), "connector.invoke", map[string]any{
		"connector_id": "x",
		"action":       "test",
		"parameters":   "a]very]long]string]that]should]exceed]32]bytes]when]serialized]to]json]output",
	}, 30)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(result.Output) > 32 {
		t.Fatalf("output not truncated: %d bytes", len(result.Output))
	}
}

// ── helpers ─────────────────────────────────────────────────────────

func asExecError(err error, target **runnerexec.ExecError) bool {
	var execErr *runnerexec.ExecError
	if ok := errorAs(err, &execErr); ok {
		*target = execErr
		return true
	}
	return false
}

// errorAs is a thin wrapper matching errors.As signature for *ExecError.
func errorAs(err error, target **runnerexec.ExecError) bool {
	type iface interface{ Error() string }
	for err != nil {
		if e, ok := err.(*runnerexec.ExecError); ok {
			*target = e
			return true
		}
		u, ok := err.(interface{ Unwrap() error })
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}
