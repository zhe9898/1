// Package runnerexec provides the job execution layer for the ZEN70 Go runner.
//
// Each job kind is dispatched to a dedicated handler with:
// - Context-based timeout (derived from job lease)
// - Structured error classification (ExecError → FailureCategory)
// - Output size limiting
// - Graceful cancellation awareness
// - Real HTTP invocation for connector.invoke and http.request kinds
package runnerexec

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"
)

// ── Error types ─────────────────────────────────────────────────────

// ExecError carries structured failure classification that maps to the
// Python FailureCategory enum so the gateway can make smart retry decisions.
type ExecError struct {
	Message  string
	Category string         // "timeout", "resource_exhausted", "invalid_payload", …
	Details  map[string]any // optional context for diagnostics
}

func (e *ExecError) Error() string { return e.Message }

// ── Result / Config ─────────────────────────────────────────────────

// Result holds job execution output.
type Result struct {
	Summary string
	Output  string
}

// Config holds executor tunables.
type Config struct {
	DefaultTimeoutSeconds int // fallback when lease doesn't specify timeout
	MaxOutputBytes        int // output truncation limit
}

// ── Active job tracking ─────────────────────────────────────────────

// activeJob tracks a running job for cancel/recover support.
type activeJob struct {
	JobID  string
	Kind   string
	Start  time.Time
	Cancel context.CancelFunc
}

// ActiveJobInfo is the read-only view returned by ActiveJobs().
type ActiveJobInfo struct {
	JobID   string
	Kind    string
	Running time.Duration
}

// ── Executor ────────────────────────────────────────────────────────

// Executor dispatches and monitors job execution per kind.
// It tracks active jobs and supports explicit cancellation.
type Executor struct {
	cfg        Config
	httpClient *http.Client

	mu         sync.Mutex
	activeJobs map[string]*activeJob // jobID → activeJob
}

// New creates an Executor with sane defaults.
// httpClient is used for connector.invoke and http.request kinds;
// pass nil to use a default client with 30s timeout.
func New(cfg Config, httpClient *http.Client) *Executor {
	if cfg.DefaultTimeoutSeconds <= 0 {
		cfg.DefaultTimeoutSeconds = 300
	}
	if cfg.MaxOutputBytes <= 0 {
		cfg.MaxOutputBytes = 1 << 20 // 1 MB
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Executor{
		cfg:        cfg,
		httpClient: httpClient,
		activeJobs: make(map[string]*activeJob),
	}
}

// Run executes a job with timeout enforcement and error classification.
// leaseSeconds is the remaining lease time; the executor will finish
// slightly before it to allow reporting headroom.
func (e *Executor) Run(ctx context.Context, kind string, payload map[string]any, leaseSeconds int) (Result, error) {
	return e.RunJob(ctx, "", kind, payload, leaseSeconds)
}

// RunJob executes a job with tracking by jobID for cancel/active-jobs support.
func (e *Executor) RunJob(ctx context.Context, jobID string, kind string, payload map[string]any, leaseSeconds int) (Result, error) {
	timeout := e.effectiveTimeout(leaseSeconds)
	execCtx, cancel := context.WithTimeout(ctx, timeout)

	// Track active job
	if jobID != "" {
		e.mu.Lock()
		e.activeJobs[jobID] = &activeJob{
			JobID:  jobID,
			Kind:   kind,
			Start:  time.Now(),
			Cancel: cancel,
		}
		e.mu.Unlock()
	}

	defer func() {
		cancel()
		if jobID != "" {
			e.mu.Lock()
			delete(e.activeJobs, jobID)
			e.mu.Unlock()
		}
	}()

	result, err := e.dispatch(execCtx, kind, payload)
	if err != nil {
		return result, classifyError(err, kind)
	}
	return e.truncateOutput(result), nil
}

// Cancel cancels a running job by its ID. Returns true if the job was found
// and cancelled, false if the job was not active.
func (e *Executor) Cancel(jobID string) bool {
	e.mu.Lock()
	aj, ok := e.activeJobs[jobID]
	e.mu.Unlock()
	if !ok {
		return false
	}
	aj.Cancel()
	return true
}

// ActiveJobs returns a snapshot of all currently executing jobs.
func (e *Executor) ActiveJobs() []ActiveJobInfo {
	e.mu.Lock()
	defer e.mu.Unlock()
	now := time.Now()
	info := make([]ActiveJobInfo, 0, len(e.activeJobs))
	for _, aj := range e.activeJobs {
		info = append(info, ActiveJobInfo{
			JobID:   aj.JobID,
			Kind:    aj.Kind,
			Running: now.Sub(aj.Start),
		})
	}
	return info
}

// ActiveJobCount returns the number of currently executing jobs.
func (e *Executor) ActiveJobCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.activeJobs)
}

// RecoverOrphanedJobs is called on startup to handle any jobs that were
// in-progress when the process last exited. Since the executor is in-memory,
// there are no orphaned jobs after a restart — but the runner-agent should
// report them as abandoned to the gateway. This method returns the count of
// jobs that were cleaned up (always 0 for a fresh start).
func (e *Executor) RecoverOrphanedJobs() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	count := len(e.activeJobs)
	// On startup this is always empty, but after hot-reload it cleans up.
	e.activeJobs = make(map[string]*activeJob)
	return count
}

// effectiveTimeout calculates execution timeout with headroom for reporting.
func (e *Executor) effectiveTimeout(leaseSeconds int) time.Duration {
	if leaseSeconds > 10 {
		return time.Duration(leaseSeconds-5) * time.Second
	}
	if leaseSeconds > 0 {
		return time.Duration(leaseSeconds) * time.Second
	}
	return time.Duration(e.cfg.DefaultTimeoutSeconds) * time.Second
}

// truncateOutput ensures output doesn't exceed MaxOutputBytes.
func (e *Executor) truncateOutput(r Result) Result {
	if len(r.Output) > e.cfg.MaxOutputBytes {
		r.Output = r.Output[:e.cfg.MaxOutputBytes]
	}
	return r
}

// ── Kind dispatch ───────────────────────────────────────────────────

func (e *Executor) dispatch(ctx context.Context, kind string, payload map[string]any) (Result, error) {
	switch kind {
	case "noop":
		return runNoop(ctx, payload)
	case "connector.invoke":
		return e.runConnectorInvoke(ctx, payload)
	case "http.request":
		return e.runHTTPRequest(ctx, payload)
	case "script.run":
		return runScript(ctx, payload)
	case "docker.exec":
		return runDockerExec(ctx, payload)
	case "cron.trigger":
		return e.runCronTrigger(ctx, payload)
	case "healthcheck":
		return e.runHealthcheck(ctx, payload)
	case "file.transfer":
		return runFileTransfer(ctx, payload)
	case "container.run":
		return runContainerRun(ctx, payload)
	case "cron.tick":
		return runCronTick(ctx, payload)
	case "data.sync":
		return runDataSync(ctx, payload)
	case "wasm.run":
		return runWasmRun(ctx, payload)
	default:
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("unsupported job kind: %s", kind),
			Category: "invalid_payload",
		}
	}
}

// ── Kind handlers ───────────────────────────────────────────────────

// runNoop handles noop jobs (health-check / testing). Respects context
// and optional delay_ms payload field.
func runNoop(ctx context.Context, payload map[string]any) (Result, error) {
	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	if delay, _ := payload["delay_ms"].(float64); delay > 0 {
		timer := time.NewTimer(time.Duration(delay) * time.Millisecond)
		defer timer.Stop()
		select {
		case <-ctx.Done():
			return Result{}, ctx.Err()
		case <-timer.C:
		}
	}
	return Result{Summary: "noop completed", Output: "{}"}, nil
}

// runConnectorInvoke handles connector dispatch jobs.
//
// When the payload contains an "endpoint" URL, the executor makes a real
// HTTP POST to that endpoint with the action and parameters as the request
// body. This is the production path for edge connector invocations.
//
// When no endpoint is provided (e.g. in integration tests or local-only
// mode), the executor performs payload validation and returns a structured
// echo response — ensuring the contract boundary is exercised even without
// a live connector.
func (e *Executor) runConnectorInvoke(ctx context.Context, payload map[string]any) (Result, error) {
	connectorID, _ := payload["connector_id"].(string)
	connectorKind, _ := payload["connector_kind"].(string)
	action, _ := payload["action"].(string)

	if connectorID == "" || action == "" {
		return Result{}, &ExecError{
			Message:  "connector.invoke requires connector_id and action",
			Category: "invalid_payload",
			Details:  map[string]any{"connector_id": connectorID, "action": action},
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	// ── Real HTTP invocation path ───────────────────────────────────
	endpoint, _ := payload["endpoint"].(string)
	if endpoint != "" {
		return e.invokeConnectorHTTP(ctx, endpoint, connectorID, connectorKind, action, payload)
	}

	// ── Local echo path (no endpoint) ───────────────────────────────
	output := map[string]any{
		"connector_id":   connectorID,
		"connector_kind": connectorKind,
		"action":         action,
		"status":         "executed_by_go_runner",
	}
	if params, ok := payload["parameters"]; ok {
		output["parameters"] = params
	}

	body, _ := json.Marshal(output)
	return Result{
		Summary: fmt.Sprintf("connector.invoke: %s/%s", connectorID, action),
		Output:  string(body),
	}, nil
}

// invokeConnectorHTTP performs a real HTTP POST to a connector endpoint.
func (e *Executor) invokeConnectorHTTP(
	ctx context.Context,
	endpoint string,
	connectorID string,
	connectorKind string,
	action string,
	payload map[string]any,
) (Result, error) {
	reqBody := map[string]any{
		"connector_id":   connectorID,
		"connector_kind": connectorKind,
		"action":         action,
	}
	if params, ok := payload["parameters"]; ok {
		reqBody["parameters"] = params
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to marshal connector request: %v", err),
			Category: "invalid_payload",
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(bodyBytes))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create HTTP request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"endpoint": endpoint},
		}
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := e.httpClient.Do(req)
	if err != nil {
		return Result{}, classifyHTTPError(err, connectorID, action)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, int64(e.cfg.MaxOutputBytes)+1))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to read connector response: %v", err),
			Category: "transient",
			Details:  map[string]any{"connector_id": connectorID},
		}
	}

	if resp.StatusCode >= 400 {
		return Result{}, classifyHTTPStatusError(resp.StatusCode, connectorID, action, string(respBody))
	}

	return Result{
		Summary: fmt.Sprintf("connector.invoke: %s/%s → %d", connectorID, action, resp.StatusCode),
		Output:  string(respBody),
	}, nil
}

// runHTTPRequest performs a generic HTTP request (GET/POST/PUT/DELETE).
func (e *Executor) runHTTPRequest(ctx context.Context, payload map[string]any) (Result, error) {
	urlStr, _ := payload["url"].(string)
	method, _ := payload["method"].(string)
	if urlStr == "" {
		return Result{}, &ExecError{
			Message:  "http.request requires url",
			Category: "invalid_payload",
		}
	}
	if method == "" {
		method = http.MethodGet
	}
	method = strings.ToUpper(method)

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	var bodyReader io.Reader
	if reqBody, ok := payload["body"]; ok {
		bodyBytes, err := json.Marshal(reqBody)
		if err != nil {
			return Result{}, &ExecError{
				Message:  fmt.Sprintf("failed to marshal request body: %v", err),
				Category: "invalid_payload",
			}
		}
		bodyReader = bytes.NewReader(bodyBytes)
	}

	req, err := http.NewRequestWithContext(ctx, method, urlStr, bodyReader)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create HTTP request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"url": urlStr},
		}
	}

	// Apply custom headers
	if headers, ok := payload["headers"].(map[string]any); ok {
		for k, v := range headers {
			if vs, ok := v.(string); ok {
				req.Header.Set(k, vs)
			}
		}
	}
	if req.Header.Get("Content-Type") == "" && bodyReader != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := e.httpClient.Do(req)
	if err != nil {
		return Result{}, classifyHTTPError(err, "http.request", method+" "+urlStr)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, int64(e.cfg.MaxOutputBytes)+1))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to read response: %v", err),
			Category: "transient",
		}
	}

	output := map[string]any{
		"status_code": resp.StatusCode,
		"body":        string(respBody),
	}
	outBytes, _ := json.Marshal(output)

	return Result{
		Summary: fmt.Sprintf("http.request: %s %s → %d", method, urlStr, resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}

// ── Error classification ────────────────────────────────────────────

// runScript executes a shell command with a configurable working directory.
//
// Payload fields:
//   - command (string, required): the shell command line to run
//   - work_dir (string, optional): working directory for the process
//
// Security: command injection is NOT a concern here because the runner-agent
// is specifically designed to execute commands dispatched by the trusted
// backend scheduler. The payload is not user-supplied; it comes from
// authenticated job definitions.
func runScript(ctx context.Context, payload map[string]any) (Result, error) {
	command, _ := payload["command"].(string)
	if command == "" {
		return Result{}, &ExecError{
			Message:  "script.run requires command",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	var cmd *exec.Cmd
	if runtime.GOOS == "windows" {
		cmd = exec.CommandContext(ctx, "cmd", "/C", command)
	} else {
		cmd = exec.CommandContext(ctx, "sh", "-c", command)
	}

	if workDir, _ := payload["work_dir"].(string); workDir != "" {
		cmd.Dir = workDir
	}

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()

	output := map[string]any{
		"stdout":    stdout.String(),
		"stderr":    stderr.String(),
		"exit_code": cmd.ProcessState.ExitCode(),
	}
	outBytes, _ := json.Marshal(output)

	if err != nil {
		exitCode := -1
		if cmd.ProcessState != nil {
			exitCode = cmd.ProcessState.ExitCode()
		}
		return Result{
				Summary: fmt.Sprintf("script.run exited %d", exitCode),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("script.run exited %d: %v", exitCode, err),
				Category: classifyExitCode(exitCode),
				Details:  map[string]any{"exit_code": exitCode, "stderr": stderr.String()},
			}
	}

	return Result{
		Summary: fmt.Sprintf("script.run exited 0"),
		Output:  string(outBytes),
	}, nil
}

// classifyExitCode maps process exit codes to failure categories.
func classifyExitCode(code int) string {
	switch {
	case code == 126 || code == 127:
		return "invalid_payload" // command not found / not executable
	case code == 137:
		return "resource_exhausted" // OOM-killed (SIGKILL)
	case code >= 128:
		return "transient" // killed by signal
	default:
		return "execution_error"
	}
}

// runDockerExec runs a command inside an existing Docker container.
//
// Payload fields:
//   - container (string, required): container name or ID
//   - command (string or []string, required): command to run inside the container
//   - work_dir (string, optional): working directory inside the container
func runDockerExec(ctx context.Context, payload map[string]any) (Result, error) {
	container, _ := payload["container"].(string)
	if container == "" {
		return Result{}, &ExecError{
			Message:  "docker.exec requires container",
			Category: "invalid_payload",
		}
	}

	// command can be a plain string or a list of strings
	var cmdParts []string
	switch v := payload["command"].(type) {
	case string:
		if v == "" {
			return Result{}, &ExecError{
				Message:  "docker.exec requires command",
				Category: "invalid_payload",
			}
		}
		cmdParts = []string{v}
	case []any:
		for _, item := range v {
			if s, ok := item.(string); ok {
				cmdParts = append(cmdParts, s)
			}
		}
		if len(cmdParts) == 0 {
			return Result{}, &ExecError{
				Message:  "docker.exec requires non-empty command list",
				Category: "invalid_payload",
			}
		}
	default:
		return Result{}, &ExecError{
			Message:  "docker.exec requires command (string or list)",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	args := []string{"exec"}
	if workDir, _ := payload["work_dir"].(string); workDir != "" {
		args = append(args, "-w", workDir)
	}
	args = append(args, container)
	args = append(args, cmdParts...)

	cmd := exec.CommandContext(ctx, "docker", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err := cmd.Run()

	exitCode := 0
	if cmd.ProcessState != nil {
		exitCode = cmd.ProcessState.ExitCode()
	}

	output := map[string]any{
		"stdout":    stdout.String(),
		"stderr":    stderr.String(),
		"exit_code": exitCode,
		"container": container,
	}
	outBytes, _ := json.Marshal(output)

	if err != nil {
		return Result{
				Summary: fmt.Sprintf("docker.exec %s exited %d", container, exitCode),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("docker.exec %s exited %d: %v", container, exitCode, err),
				Category: classifyExitCode(exitCode),
				Details:  map[string]any{"container": container, "exit_code": exitCode},
			}
	}

	return Result{
		Summary: fmt.Sprintf("docker.exec %s exited 0", container),
		Output:  string(outBytes),
	}, nil
}

// runCronTrigger fires an HTTP POST to a pre-configured webhook URL,
// used for scheduled/periodic tasks. This is essentially a lightweight
// HTTP trigger tailored for cron-style job definitions.
//
// Payload fields:
//   - webhook_url (string, required): URL to POST to
//   - cron_name (string, optional): human-readable name for the trigger
//   - body (any, optional): JSON body to send
func (e *Executor) runCronTrigger(ctx context.Context, payload map[string]any) (Result, error) {
	webhookURL, _ := payload["webhook_url"].(string)
	if webhookURL == "" {
		return Result{}, &ExecError{
			Message:  "cron.trigger requires webhook_url",
			Category: "invalid_payload",
		}
	}

	cronName, _ := payload["cron_name"].(string)
	if cronName == "" {
		cronName = "unnamed"
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	reqBody := map[string]any{
		"trigger":   "cron",
		"cron_name": cronName,
		"fired_at":  time.Now().UTC().Format(time.RFC3339),
	}
	if body, ok := payload["body"]; ok {
		reqBody["payload"] = body
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to marshal cron trigger body: %v", err),
			Category: "invalid_payload",
		}
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, webhookURL, bytes.NewReader(bodyBytes))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create cron trigger request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"webhook_url": webhookURL},
		}
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Zen70-Trigger", "cron")
	req.Header.Set("X-Zen70-Cron-Name", cronName)

	resp, err := e.httpClient.Do(req)
	if err != nil {
		return Result{}, classifyHTTPError(err, "cron.trigger", cronName)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, int64(e.cfg.MaxOutputBytes)+1))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to read cron trigger response: %v", err),
			Category: "transient",
		}
	}

	if resp.StatusCode >= 400 {
		return Result{}, classifyHTTPStatusError(resp.StatusCode, "cron.trigger", cronName, string(respBody))
	}

	output := map[string]any{
		"cron_name":   cronName,
		"status_code": resp.StatusCode,
		"body":        string(respBody),
	}
	outBytes, _ := json.Marshal(output)

	return Result{
		Summary: fmt.Sprintf("cron.trigger %s → %d", cronName, resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}

// ── Error classification (original section) ─────────────────────────

// classifyError wraps raw errors into ExecError with the right FailureCategory.
func classifyError(err error, kind string) error {
	if err == nil {
		return nil
	}
	var execErr *ExecError
	if errors.As(err, &execErr) {
		return err // already classified
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return &ExecError{
			Message:  fmt.Sprintf("job %s timed out: %v", kind, err),
			Category: "timeout",
			Details:  map[string]any{"kind": kind},
		}
	}
	if errors.Is(err, context.Canceled) {
		return &ExecError{
			Message:  fmt.Sprintf("job %s canceled: %v", kind, err),
			Category: "canceled",
			Details:  map[string]any{"kind": kind},
		}
	}
	return &ExecError{
		Message:  err.Error(),
		Category: "transient",
		Details:  map[string]any{"kind": kind},
	}
}

// classifyHTTPError converts network-level HTTP errors into ExecError.
func classifyHTTPError(err error, target string, action string) *ExecError {
	if errors.Is(err, context.DeadlineExceeded) {
		return &ExecError{
			Message:  fmt.Sprintf("HTTP request to %s timed out: %v", target, err),
			Category: "timeout",
			Details:  map[string]any{"target": target, "action": action},
		}
	}
	if errors.Is(err, context.Canceled) {
		return &ExecError{
			Message:  fmt.Sprintf("HTTP request to %s canceled: %v", target, err),
			Category: "canceled",
			Details:  map[string]any{"target": target, "action": action},
		}
	}
	return &ExecError{
		Message:  fmt.Sprintf("HTTP request to %s failed: %v", target, err),
		Category: "transient",
		Details:  map[string]any{"target": target, "action": action},
	}
}

// classifyHTTPStatusError maps HTTP status codes to failure categories.
func classifyHTTPStatusError(status int, target string, action string, body string) *ExecError {
	category := "transient"
	switch {
	case status == 400 || status == 422:
		category = "invalid_payload"
	case status == 401 || status == 403:
		category = "permission_denied"
	case status == 404:
		category = "not_found"
	case status == 429:
		category = "resource_exhausted"
	case status >= 400 && status < 500:
		category = "invalid_payload"
	case status >= 500:
		category = "transient"
	}

	truncatedBody := body
	if len(truncatedBody) > 512 {
		truncatedBody = truncatedBody[:512]
	}

	return &ExecError{
		Message:  fmt.Sprintf("connector %s/%s returned HTTP %d", target, action, status),
		Category: category,
		Details: map[string]any{
			"target":      target,
			"action":      action,
			"status_code": status,
			"body":        truncatedBody,
		},
	}
}
