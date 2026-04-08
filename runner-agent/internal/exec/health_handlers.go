package runnerexec

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"time"
)

const (
	DefaultProbeTimeoutMs   = 5000.0
	DiagnosticsSnippetBytes = 512
)

func (e *Executor) runHealthcheck(ctx context.Context, payload map[string]any) (Result, error) {
	target, err := requiredStringField(payload, "target")
	if err != nil {
		return Result{}, err
	}

	checkType, err := optionalStringField(payload, "check_type")
	if err != nil {
		return Result{}, err
	}
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

func (e *Executor) healthcheckHTTP(ctx context.Context, target string, payload map[string]any) (Result, error) {
	method, err := optionalStringField(payload, "method")
	if err != nil {
		return Result{}, err
	}
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
