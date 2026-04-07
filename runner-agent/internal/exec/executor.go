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
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"
)

// ── Executor defaults ────────────────────────────────────────────────

const (
	// DefaultJobTimeoutSeconds is the fallback job timeout when the lease
	// doesn't specify one (5 minutes).
	DefaultJobTimeoutSeconds = 300

	// DefaultMaxOutputBytes is the output truncation limit (1 MB).
	DefaultMaxOutputBytes = 1 << 20

	// DefaultHTTPClientTimeout is the timeout for HTTP requests made by
	// connector.invoke and http.request kinds.
	DefaultHTTPClientTimeout = 30 * time.Second
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
		cfg.DefaultTimeoutSeconds = DefaultJobTimeoutSeconds
	}
	if cfg.MaxOutputBytes <= 0 {
		cfg.MaxOutputBytes = DefaultMaxOutputBytes
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: DefaultHTTPClientTimeout}
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

func invalidPayloadField(key string, reason string) *ExecError {
	return &ExecError{
		Message:  fmt.Sprintf("invalid payload field %q: %s", key, reason),
		Category: "invalid_payload",
		Details:  map[string]any{"field": key, "reason": reason},
	}
}

func requiredStringField(payload map[string]any, key string) (string, error) {
	raw, ok := payload[key]
	if !ok || raw == nil {
		return "", invalidPayloadField(key, "missing required string")
	}
	value, ok := raw.(string)
	if !ok {
		return "", invalidPayloadField(key, "must be a string")
	}
	if value == "" {
		return "", invalidPayloadField(key, "must be a non-empty string")
	}
	return value, nil
}

func optionalStringField(payload map[string]any, key string) (string, error) {
	raw, ok := payload[key]
	if !ok || raw == nil {
		return "", nil
	}
	value, ok := raw.(string)
	if !ok {
		return "", invalidPayloadField(key, "must be a string")
	}
	return value, nil
}

func optionalObjectField(payload map[string]any, key string) (map[string]any, error) {
	raw, ok := payload[key]
	if !ok || raw == nil {
		return nil, nil
	}
	value, ok := raw.(map[string]any)
	if !ok {
		return nil, invalidPayloadField(key, "must be an object")
	}
	return value, nil
}

func optionalFloatField(payload map[string]any, key string) (float64, error) {
	raw, ok := payload[key]
	if !ok || raw == nil {
		return 0, nil
	}
	value, ok := raw.(float64)
	if !ok {
		return 0, invalidPayloadField(key, "must be a number")
	}
	return value, nil
}

func optionalStringListField(payload map[string]any, key string) ([]string, error) {
	raw, ok := payload[key]
	if !ok || raw == nil {
		return nil, nil
	}
	switch value := raw.(type) {
	case []string:
		out := make([]string, 0, len(value))
		for _, item := range value {
			if item == "" {
				return nil, invalidPayloadField(key, "must not contain empty strings")
			}
			out = append(out, item)
		}
		return out, nil
	case []any:
		out := make([]string, 0, len(value))
		for _, item := range value {
			text, ok := item.(string)
			if !ok {
				return nil, invalidPayloadField(key, "must be a list of strings")
			}
			if text == "" {
				return nil, invalidPayloadField(key, "must not contain empty strings")
			}
			out = append(out, text)
		}
		return out, nil
	default:
		return nil, invalidPayloadField(key, "must be a list of strings")
	}
}

// ── Kind dispatch ───────────────────────────────────────────────────

func (e *Executor) dispatch(ctx context.Context, kind string, payload map[string]any) (Result, error) {
	switch kind {
	case "noop":
		return runNoop(ctx, payload)
	case "shell.exec":
		return runScript(ctx, payload)
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
	case "alert.notify":
		return e.runAlertNotify(ctx, payload)
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
	connectorID, err := requiredStringField(payload, "connector_id")
	if err != nil {
		return Result{}, err
	}
	connectorKind, err := optionalStringField(payload, "connector_kind")
	if err != nil {
		return Result{}, err
	}
	action, err := requiredStringField(payload, "action")
	if err != nil {
		return Result{}, err
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	// ── Real HTTP invocation path ───────────────────────────────────
	endpoint, err := optionalStringField(payload, "endpoint")
	if err != nil {
		return Result{}, err
	}
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
	validatedEndpoint, execErr := validateOutboundHTTPURL(endpoint, "endpoint")
	if execErr != nil {
		return Result{}, execErr
	}
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

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, validatedEndpoint.String(), bytes.NewReader(bodyBytes))
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
	urlStr, err := requiredStringField(payload, "url")
	if err != nil {
		return Result{}, err
	}
	validatedURL, execErr := validateOutboundHTTPURL(urlStr, "url")
	if execErr != nil {
		return Result{}, execErr
	}
	method, err := optionalStringField(payload, "method")
	if err != nil {
		return Result{}, err
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

	req, err := http.NewRequestWithContext(ctx, method, validatedURL.String(), bodyReader)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create HTTP request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"url": urlStr},
		}
	}

	// Apply custom headers
	headers, err := optionalObjectField(payload, "headers")
	if err != nil {
		return Result{}, err
	}
	if headers != nil {
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

// runAlertNotify delivers validated webhook notifications generated by the
// control plane's alerting subsystem.
func (e *Executor) runAlertNotify(ctx context.Context, payload map[string]any) (Result, error) {
	action, err := optionalObjectField(payload, "action")
	if err != nil {
		return Result{}, err
	}
	if action == nil {
		return Result{}, invalidPayloadField("action", "missing required object")
	}

	actionType, err := requiredStringField(action, "type")
	if err != nil {
		return Result{}, err
	}
	if actionType != "webhook" {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("unsupported alert action type: %s", actionType),
			Category: "invalid_payload",
		}
	}

	urlStr, err := requiredStringField(action, "url")
	if err != nil {
		return Result{}, err
	}
	validatedURL, execErr := validateOutboundHTTPURL(urlStr, "url")
	if execErr != nil {
		return Result{}, execErr
	}

	method, err := optionalStringField(action, "method")
	if err != nil {
		return Result{}, err
	}
	if method == "" {
		method = http.MethodPost
	}
	method = strings.ToUpper(method)
	if method != http.MethodPost {
		return Result{}, invalidPayloadField("method", "alert.notify only supports POST")
	}

	timeoutSeconds, err := optionalFloatField(action, "timeout_seconds")
	if err != nil {
		return Result{}, err
	}
	requestCtx := ctx
	cancel := func() {}
	if timeoutSeconds > 0 {
		requestCtx, cancel = context.WithTimeout(ctx, time.Duration(timeoutSeconds*float64(time.Second)))
	}
	defer cancel()

	ruleName, err := requiredStringField(payload, "rule_name")
	if err != nil {
		return Result{}, err
	}
	severity, err := requiredStringField(payload, "severity")
	if err != nil {
		return Result{}, err
	}
	message, err := requiredStringField(payload, "message")
	if err != nil {
		return Result{}, err
	}
	triggeredAt, err := requiredStringField(payload, "triggered_at")
	if err != nil {
		return Result{}, err
	}
	details, err := optionalObjectField(payload, "details")
	if err != nil {
		return Result{}, err
	}
	reqPayload := map[string]any{
		"alert_id":     payload["alert_id"],
		"rule_name":    ruleName,
		"severity":     severity,
		"message":      message,
		"details":      details,
		"triggered_at": triggeredAt,
	}
	bodyBytes, err := json.Marshal(reqPayload)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to marshal alert payload: %v", err),
			Category: "invalid_payload",
		}
	}

	req, err := http.NewRequestWithContext(requestCtx, http.MethodPost, validatedURL.String(), bytes.NewReader(bodyBytes))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create alert webhook request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"url": urlStr},
		}
	}
	req.Header.Set("Content-Type", "application/json")

	headers, err := optionalObjectField(action, "headers")
	if err != nil {
		return Result{}, err
	}
	if headers != nil {
		for key, rawValue := range headers {
			headerValue, ok := rawValue.(string)
			if !ok {
				return Result{}, invalidPayloadField("headers", "must be an object of string values")
			}
			req.Header.Set(key, headerValue)
		}
	}

	resp, err := e.httpClient.Do(req)
	if err != nil {
		return Result{}, classifyHTTPError(err, "alert.notify", urlStr)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(io.LimitReader(resp.Body, int64(e.cfg.MaxOutputBytes)+1))
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to read alert webhook response: %v", err),
			Category: "transient",
		}
	}
	if resp.StatusCode >= 400 {
		return Result{}, classifyHTTPStatusError(resp.StatusCode, "alert.notify", urlStr, string(respBody))
	}

	output := map[string]any{
		"delivered":   true,
		"status_code": resp.StatusCode,
		"body":        string(respBody),
	}
	outBytes, _ := json.Marshal(output)
	return Result{
		Summary: fmt.Sprintf("alert.notify: %s 鈫?%d", validatedURL.String(), resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}

// runScript executes a shell command or inline script with an optional working
// directory.
//
// Payload fields:
//   - command (string, optional): shell command line to run
//   - script (string, optional): inline script source
//   - work_dir / working_dir (string, optional): working directory for the process
//
// Security: this handler executes privileged commands. The control plane must
// restrict script-capable kinds to authorized callers, and the runner still
// treats payloads as untrusted input that requires structural validation here.
func runScript(ctx context.Context, payload map[string]any) (Result, error) {
	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	var cmd *exec.Cmd
	script, err := optionalStringField(payload, "script")
	if err != nil {
		return Result{}, err
	}
	if script != "" {
		interpreter, err := optionalStringField(payload, "interpreter")
		if err != nil {
			return Result{}, err
		}
		if interpreter == "" {
			interpreter = "bash"
		}
		args, err := optionalStringListField(payload, "args")
		if err != nil {
			return Result{}, err
		}
		switch strings.ToLower(interpreter) {
		case "bash":
			cmd = exec.CommandContext(ctx, "bash", append([]string{"-lc", script}, args...)...)
		case "python":
			cmd = exec.CommandContext(ctx, "python", append([]string{"-c", script}, args...)...)
		case "node":
			cmd = exec.CommandContext(ctx, "node", append([]string{"-e", script}, args...)...)
		case "powershell":
			powerShellBin := "powershell"
			if runtime.GOOS != "windows" {
				powerShellBin = "pwsh"
			}
			cmd = exec.CommandContext(ctx, powerShellBin, append([]string{"-Command", script}, args...)...)
		default:
			return Result{}, &ExecError{
				Message:  fmt.Sprintf("unsupported interpreter: %s", interpreter),
				Category: "invalid_payload",
			}
		}
		envMap, err := optionalObjectField(payload, "env")
		if err != nil {
			return Result{}, err
		}
		if envMap != nil {
			envPairs := make([]string, 0, len(envMap))
			for k, v := range envMap {
				vs, ok := v.(string)
				if !ok {
					return Result{}, &ExecError{
						Message:  fmt.Sprintf("script env %q must be a string", k),
						Category: "invalid_payload",
					}
				}
				envPairs = append(envPairs, k+"="+vs)
			}
			cmd.Env = append(os.Environ(), envPairs...)
		}
	} else {
		command, err := requiredStringField(payload, "command")
		if err != nil {
			return Result{}, err
		}
		if runtime.GOOS == "windows" {
			cmd = exec.CommandContext(ctx, "cmd", "/C", command)
		} else {
			cmd = exec.CommandContext(ctx, "sh", "-c", command)
		}
	}

	workDir, err := optionalStringField(payload, "working_dir")
	if err != nil {
		return Result{}, err
	}
	if workDir == "" {
		workDir, err = optionalStringField(payload, "work_dir")
		if err != nil {
			return Result{}, err
		}
	}
	if workDir != "" {
		cmd.Dir = workDir
	}

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err = cmd.Run()
	durationSeconds := time.Since(start).Seconds()

	output := map[string]any{
		"stdout":           stdout.String(),
		"stderr":           stderr.String(),
		"exit_code":        cmd.ProcessState.ExitCode(),
		"duration_seconds": durationSeconds,
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
	container, err := requiredStringField(payload, "container")
	if err != nil {
		return Result{}, err
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
	workDir, err := optionalStringField(payload, "work_dir")
	if err != nil {
		return Result{}, err
	}
	if workDir != "" {
		args = append(args, "-w", workDir)
	}
	args = append(args, container)
	args = append(args, cmdParts...)

	cmd := exec.CommandContext(ctx, "docker", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	err = cmd.Run()

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
	webhookURL, err := requiredStringField(payload, "webhook_url")
	if err != nil {
		return Result{}, err
	}
	validatedWebhookURL, execErr := validateOutboundHTTPURL(webhookURL, "webhook_url")
	if execErr != nil {
		return Result{}, execErr
	}

	cronName, err := optionalStringField(payload, "cron_name")
	if err != nil {
		return Result{}, err
	}
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

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, validatedWebhookURL.String(), bytes.NewReader(bodyBytes))
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
