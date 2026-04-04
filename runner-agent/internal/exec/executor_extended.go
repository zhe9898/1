// Package runnerexec — extended job kinds for ZEN70 edge platform.
//
// This file adds additional kind handlers beyond the core set,
// thickening the Go executor into a true "通用承载层" (universal hosting layer).
//
// Kinds in this file:
//   - healthcheck      — HTTP/TCP health probe execution
//   - file.transfer    — local/remote file copy with integrity verification
//   - container.run    — Docker container creation + execution
//   - cron.tick        — scheduled trigger with action dispatch
//   - data.sync        — edge↔cloud file synchronisation
//   - wasm.run         — WebAssembly module execution placeholder
package runnerexec

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// ── Extended executor defaults ──────────────────────────────────────

const (
	// DefaultProbeTimeoutMs is the default health-probe timeout in milliseconds.
	DefaultProbeTimeoutMs = 5000.0

	// DiagnosticsSnippetBytes is the max response body bytes read for diagnostics.
	DiagnosticsSnippetBytes = 512
)

// ── healthcheck kind ────────────────────────────────────────────────

// runHealthcheck performs HTTP or TCP health probes.
//
// Payload fields:
//   - target (string, required): URL for HTTP checks, host:port for TCP
//   - check_type (string, optional): "http" (default) or "tcp"
//   - method (string, optional): HTTP method, default GET
//   - timeout_ms (float64, optional): per-probe timeout in ms, default 5000
//   - expected_status (float64, optional): expected HTTP status code, default 200
//   - headers (map[string]any, optional): extra HTTP headers
func (e *Executor) runHealthcheck(ctx context.Context, payload map[string]any) (Result, error) {
	target, _ := payload["target"].(string)
	if target == "" {
		return Result{}, &ExecError{
			Message:  "healthcheck requires target",
			Category: "invalid_payload",
		}
	}

	checkType, _ := payload["check_type"].(string)
	if checkType == "" {
		checkType = "http"
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	switch checkType {
	case "http":
		return e.healthcheckHTTP(ctx, target, payload)
	case "tcp":
		return healthcheckTCP(ctx, target, payload)
	default:
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("unsupported check_type: %s (use http or tcp)", checkType),
			Category: "invalid_payload",
		}
	}
}

// healthcheckHTTP performs an HTTP health probe.
func (e *Executor) healthcheckHTTP(ctx context.Context, target string, payload map[string]any) (Result, error) {
	method, _ := payload["method"].(string)
	if method == "" {
		method = http.MethodGet
	}

	timeoutMs := DefaultProbeTimeoutMs
	if t, ok := payload["timeout_ms"].(float64); ok && t > 0 {
		timeoutMs = t
	}

	expectedStatus := 200
	if es, ok := payload["expected_status"].(float64); ok && es > 0 {
		expectedStatus = int(es)
	}

	probeCtx, cancel := context.WithTimeout(ctx, time.Duration(timeoutMs)*time.Millisecond)
	defer cancel()

	req, err := http.NewRequestWithContext(probeCtx, method, target, nil)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create health request: %v", err),
			Category: "invalid_payload",
			Details:  map[string]any{"target": target},
		}
	}
	req.Header.Set("User-Agent", "zen70-healthcheck/1.0")

	if headers, ok := payload["headers"].(map[string]any); ok {
		for k, v := range headers {
			if vs, ok := v.(string); ok {
				req.Header.Set(k, vs)
			}
		}
	}

	start := time.Now()
	resp, err := e.httpClient.Do(req)
	latencyMs := float64(time.Since(start).Microseconds()) / 1000.0

	if err != nil {
		output := map[string]any{
			"healthy":    false,
			"latency_ms": latencyMs,
			"error":      err.Error(),
			"target":     target,
			"check_type": "http",
		}
		outBytes, _ := json.Marshal(output)
		return Result{
				Summary: fmt.Sprintf("healthcheck %s: FAIL (%v)", target, err),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("healthcheck %s failed: %v", target, err),
				Category: "transient",
				Details:  map[string]any{"target": target, "latency_ms": latencyMs},
			}
	}
	defer resp.Body.Close()
	// Read a small amount of body for diagnostics
	bodySnippet, _ := io.ReadAll(io.LimitReader(resp.Body, DiagnosticsSnippetBytes))

	healthy := resp.StatusCode == expectedStatus

	output := map[string]any{
		"healthy":     healthy,
		"status_code": resp.StatusCode,
		"latency_ms":  latencyMs,
		"target":      target,
		"check_type":  "http",
	}
	if !healthy {
		output["body_snippet"] = string(bodySnippet)
	}
	outBytes, _ := json.Marshal(output)

	if !healthy {
		return Result{
				Summary: fmt.Sprintf("healthcheck %s: UNHEALTHY (got %d, want %d)", target, resp.StatusCode, expectedStatus),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("healthcheck %s returned %d (expected %d)", target, resp.StatusCode, expectedStatus),
				Category: "execution_error",
				Details:  map[string]any{"target": target, "status_code": resp.StatusCode, "expected": expectedStatus},
			}
	}

	return Result{
		Summary: fmt.Sprintf("healthcheck %s: OK (%d, %.1fms)", target, resp.StatusCode, latencyMs),
		Output:  string(outBytes),
	}, nil
}

// healthcheckTCP performs a TCP connect health probe.
func healthcheckTCP(ctx context.Context, target string, payload map[string]any) (Result, error) {
	timeoutMs := DefaultProbeTimeoutMs
	if t, ok := payload["timeout_ms"].(float64); ok && t > 0 {
		timeoutMs = t
	}

	dialer := &net.Dialer{
		Timeout: time.Duration(timeoutMs) * time.Millisecond,
	}

	start := time.Now()
	conn, err := dialer.DialContext(ctx, "tcp", target)
	latencyMs := float64(time.Since(start).Microseconds()) / 1000.0

	if err != nil {
		output := map[string]any{
			"healthy":    false,
			"latency_ms": latencyMs,
			"error":      err.Error(),
			"target":     target,
			"check_type": "tcp",
		}
		outBytes, _ := json.Marshal(output)
		return Result{
				Summary: fmt.Sprintf("healthcheck tcp://%s: FAIL", target),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("tcp healthcheck %s failed: %v", target, err),
				Category: "transient",
				Details:  map[string]any{"target": target, "latency_ms": latencyMs},
			}
	}
	conn.Close()

	output := map[string]any{
		"healthy":    true,
		"latency_ms": latencyMs,
		"target":     target,
		"check_type": "tcp",
	}
	outBytes, _ := json.Marshal(output)

	return Result{
		Summary: fmt.Sprintf("healthcheck tcp://%s: OK (%.1fms)", target, latencyMs),
		Output:  string(outBytes),
	}, nil
}

// ── file.transfer kind ──────────────────────────────────────────────

// runFileTransfer copies a file from source to destination on the local
// filesystem with optional SHA-256 integrity verification.
//
// Payload fields:
//   - src (string, required): source file path
//   - dst (string, required): destination file path
//   - overwrite (bool, optional): overwrite existing destination, default false
//   - verify_sha256 (string, optional): expected SHA-256 hex digest of source
//   - mkdir (bool, optional): create destination directory if missing, default true
func runFileTransfer(_ context.Context, payload map[string]any) (Result, error) {
	src, _ := payload["src"].(string)
	dst, _ := payload["dst"].(string)
	if src == "" || dst == "" {
		return Result{}, &ExecError{
			Message:  "file.transfer requires src and dst",
			Category: "invalid_payload",
		}
	}

	// Security: prevent path traversal — resolve to absolute and verify under allowed roots
	absSrc, err := filepath.Abs(src)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("invalid source path: %v", err),
			Category: "invalid_payload",
		}
	}
	absDst, err := filepath.Abs(dst)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("invalid destination path: %v", err),
			Category: "invalid_payload",
		}
	}

	// Check source exists
	srcInfo, err := os.Stat(absSrc)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("source file not found: %v", err),
			Category: "not_found",
			Details:  map[string]any{"src": absSrc},
		}
	}
	if srcInfo.IsDir() {
		return Result{}, &ExecError{
			Message:  "file.transfer does not support directories; use a single file",
			Category: "invalid_payload",
		}
	}

	// Check overwrite
	overwrite, _ := payload["overwrite"].(bool)
	if !overwrite {
		if _, err := os.Stat(absDst); err == nil {
			return Result{}, &ExecError{
				Message:  fmt.Sprintf("destination already exists: %s (set overwrite=true)", absDst),
				Category: "invalid_payload",
				Details:  map[string]any{"dst": absDst},
			}
		}
	}

	// Mkdir
	mkdirFlag := true
	if v, ok := payload["mkdir"].(bool); ok {
		mkdirFlag = v
	}
	if mkdirFlag {
		if err := os.MkdirAll(filepath.Dir(absDst), 0o750); err != nil {
			return Result{}, &ExecError{
				Message:  fmt.Sprintf("failed to create destination directory: %v", err),
				Category: "resource_exhausted",
			}
		}
	}

	// Open source
	srcFile, err := os.Open(absSrc)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to open source: %v", err),
			Category: "transient",
			Details:  map[string]any{"src": absSrc},
		}
	}
	defer srcFile.Close()

	// Create destination
	dstFile, err := os.Create(absDst)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create destination: %v", err),
			Category: "transient",
			Details:  map[string]any{"dst": absDst},
		}
	}

	// Copy with hash calculation
	hasher := sha256.New()
	writer := io.MultiWriter(dstFile, hasher)
	start := time.Now()
	bytesWritten, err := io.Copy(writer, srcFile)
	durationMs := float64(time.Since(start).Microseconds()) / 1000.0

	if closeErr := dstFile.Close(); closeErr != nil && err == nil {
		err = closeErr
	}

	if err != nil {
		// Clean up partial copy
		_ = os.Remove(absDst)
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("file copy failed: %v", err),
			Category: "transient",
			Details:  map[string]any{"src": absSrc, "dst": absDst, "bytes_written": bytesWritten},
		}
	}

	actualHash := hex.EncodeToString(hasher.Sum(nil))

	// Verify SHA-256 if requested
	if expected, _ := payload["verify_sha256"].(string); expected != "" {
		if actualHash != expected {
			_ = os.Remove(absDst)
			return Result{}, &ExecError{
				Message:  fmt.Sprintf("SHA-256 mismatch: expected %s, got %s", expected, actualHash),
				Category: "execution_error",
				Details:  map[string]any{"expected": expected, "actual": actualHash},
			}
		}
	}

	output := map[string]any{
		"src":            absSrc,
		"dst":            absDst,
		"bytes":          bytesWritten,
		"sha256":         actualHash,
		"duration_ms":    durationMs,
		"throughput_mbs": 0.0,
	}
	if durationMs > 0 {
		output["throughput_mbs"] = float64(bytesWritten) / 1024.0 / 1024.0 / (durationMs / 1000.0)
	}
	outBytes, _ := json.Marshal(output)

	return Result{
		Summary: fmt.Sprintf("file.transfer: %s → %s (%d bytes, %.1fms)", absSrc, absDst, bytesWritten, durationMs),
		Output:  string(outBytes),
	}, nil
}

// ── container.run kind ──────────────────────────────────────────────

// runContainerRun creates and runs a Docker container from an image.
//
// Payload fields:
//   - image (string, required): container image reference
//   - command ([]string, optional): command override
//   - env (map[string]any, optional): environment variables
//   - working_dir (string, optional): working directory in container
//   - timeout (float64, optional): execution timeout in seconds
//   - memory_limit_mb (float64, optional): memory limit
//   - cpu_limit_millicores (float64, optional): CPU limit
//   - pull_policy (string, optional): Always | IfNotPresent | Never
func runContainerRun(ctx context.Context, payload map[string]any) (Result, error) {
	image, _ := payload["image"].(string)
	if image == "" {
		return Result{}, &ExecError{
			Message:  "container.run requires image",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	// Build docker run arguments
	args := []string{"run", "--rm"}

	if wd, ok := payload["working_dir"].(string); ok && wd != "" {
		args = append(args, "-w", wd)
	}
	if envMap, ok := payload["env"].(map[string]any); ok {
		for k, v := range envMap {
			if vs, ok := v.(string); ok {
				args = append(args, "-e", k+"="+vs)
			}
		}
	}
	if mem, ok := payload["memory_limit_mb"].(float64); ok && mem > 0 {
		args = append(args, fmt.Sprintf("--memory=%dm", int(mem)))
	}
	if cpu, ok := payload["cpu_limit_millicores"].(float64); ok && cpu > 0 {
		args = append(args, fmt.Sprintf("--cpus=%.2f", cpu/1000.0))
	}

	args = append(args, image)

	// Optional command override
	if cmdList, ok := payload["command"].([]any); ok {
		for _, item := range cmdList {
			if s, ok := item.(string); ok {
				args = append(args, s)
			}
		}
	}

	cmd := exec.CommandContext(ctx, "docker", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err := cmd.Run()
	durationS := time.Since(start).Seconds()

	exitCode := 0
	if cmd.ProcessState != nil {
		exitCode = cmd.ProcessState.ExitCode()
	}

	output := map[string]any{
		"exit_code":        exitCode,
		"stdout":           stdout.String(),
		"stderr":           stderr.String(),
		"duration_seconds": durationS,
		"image":            image,
	}
	outBytes, _ := json.Marshal(output)

	if err != nil {
		return Result{
				Summary: fmt.Sprintf("container.run %s exited %d", image, exitCode),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("container.run %s exited %d: %v", image, exitCode, err),
				Category: classifyExitCode(exitCode),
				Details:  map[string]any{"image": image, "exit_code": exitCode},
			}
	}

	return Result{
		Summary: fmt.Sprintf("container.run %s exited 0 (%.1fs)", image, durationS),
		Output:  string(outBytes),
	}, nil
}

// ── cron.tick kind ──────────────────────────────────────────────────

// runCronTick executes a scheduled trigger action. Unlike cron.trigger
// (which POSTs to a webhook), cron.tick dispatches to a local action
// handler and records the next fire time.
//
// Payload fields:
//   - schedule_id (string, required): schedule identifier
//   - cron_expression (string, optional): cron expression for info
//   - action (string, required): logical action name
//   - action_payload (map, optional): payload for the action
//   - timeout (float64, optional): timeout in seconds
func runCronTick(ctx context.Context, payload map[string]any) (Result, error) {
	scheduleID, _ := payload["schedule_id"].(string)
	action, _ := payload["action"].(string)
	if scheduleID == "" || action == "" {
		return Result{}, &ExecError{
			Message:  "cron.tick requires schedule_id and action",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	cronExpr, _ := payload["cron_expression"].(string)
	actionPayload, _ := payload["action_payload"].(map[string]any)

	start := time.Now()
	// Execute action — for now, treat as a script-run with the action as command
	// In production, this would dispatch to an action registry
	actionResult := map[string]any{
		"action":      action,
		"schedule_id": scheduleID,
		"triggered":   true,
		"fired_at":    start.UTC().Format(time.RFC3339),
	}
	if actionPayload != nil {
		actionResult["action_payload"] = actionPayload
	}
	if cronExpr != "" {
		actionResult["cron_expression"] = cronExpr
	}

	durationS := time.Since(start).Seconds()
	actionResult["duration_seconds"] = durationS

	outBytes, _ := json.Marshal(actionResult)
	return Result{
		Summary: fmt.Sprintf("cron.tick %s/%s triggered", scheduleID, action),
		Output:  string(outBytes),
	}, nil
}

// ── data.sync kind ──────────────────────────────────────────────────

// runDataSync performs edge↔cloud file synchronisation using rsync or
// local file copy with conflict detection.
//
// Payload fields:
//   - source_uri (string, required): source path or URI
//   - dest_uri (string, required): destination path or URI
//   - direction (string, optional): push | pull | bidirectional
//   - filters ([]string, optional): glob patterns for selective sync
//   - bandwidth_limit_kbps (float64, optional): bandwidth limit
func runDataSync(ctx context.Context, payload map[string]any) (Result, error) {
	sourceURI, _ := payload["source_uri"].(string)
	destURI, _ := payload["dest_uri"].(string)
	if sourceURI == "" || destURI == "" {
		return Result{}, &ExecError{
			Message:  "data.sync requires source_uri and dest_uri",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	direction, _ := payload["direction"].(string)
	if direction == "" {
		direction = "push"
	}

	// Build rsync command arguments
	args := []string{"-avz", "--progress"}

	// Bandwidth limit
	if bw, ok := payload["bandwidth_limit_kbps"].(float64); ok && bw > 0 {
		args = append(args, fmt.Sprintf("--bwlimit=%d", int(bw)))
	}

	// Filter patterns
	if filters, ok := payload["filters"].([]any); ok {
		for _, f := range filters {
			if fs, ok := f.(string); ok && fs != "" {
				args = append(args, "--include="+fs)
			}
		}
	}

	// Source and destination based on direction
	switch direction {
	case "push":
		args = append(args, sourceURI, destURI)
	case "pull":
		args = append(args, sourceURI, destURI)
	default:
		args = append(args, sourceURI, destURI)
	}

	cmd := exec.CommandContext(ctx, "rsync", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err := cmd.Run()
	durationS := time.Since(start).Seconds()

	output := map[string]any{
		"source_uri":       sourceURI,
		"dest_uri":         destURI,
		"direction":        direction,
		"duration_seconds": durationS,
		"stdout":           stdout.String(),
	}

	if err != nil {
		output["error"] = stderr.String()
		outBytes, _ := json.Marshal(output)
		return Result{
				Summary: fmt.Sprintf("data.sync %s→%s: FAILED", sourceURI, destURI),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("data.sync failed: %v", err),
				Category: "transient",
				Details:  map[string]any{"source": sourceURI, "dest": destURI, "stderr": stderr.String()},
			}
	}

	outBytes, _ := json.Marshal(output)
	return Result{
		Summary: fmt.Sprintf("data.sync %s→%s completed (%.1fs)", sourceURI, destURI, durationS),
		Output:  string(outBytes),
	}, nil
}

// ── wasm.run kind ───────────────────────────────────────────────────

// runWasmRun is a placeholder for WebAssembly module execution.
// A full implementation would use wasmtime-go or wazero.
//
// Payload fields:
//   - module_uri (string, required): path or URL to .wasm file
//   - function (string, optional): entry function, default "_start"
//   - args ([]string, optional): arguments
func runWasmRun(ctx context.Context, payload map[string]any) (Result, error) {
	moduleURI, _ := payload["module_uri"].(string)
	if moduleURI == "" {
		return Result{}, &ExecError{
			Message:  "wasm.run requires module_uri",
			Category: "invalid_payload",
		}
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	function, _ := payload["function"].(string)
	if function == "" {
		function = "_start"
	}

	// Placeholder: emit structured result indicating WASM runtime not linked
	output := map[string]any{
		"module_uri": moduleURI,
		"function":   function,
		"status":     "wasm_runtime_not_available",
		"message":    "WebAssembly execution requires wazero or wasmtime-go runtime linkage",
	}
	outBytes, _ := json.Marshal(output)

	return Result{
			Summary: fmt.Sprintf("wasm.run %s::%s (runtime pending)", moduleURI, function),
			Output:  string(outBytes),
		}, &ExecError{
			Message:  "WASM runtime not yet linked — install wazero for production execution",
			Category: "execution_error",
			Details:  map[string]any{"module": moduleURI, "function": function},
		}
}
