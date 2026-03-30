// Package runnerexec provides the job execution layer for the ZEN70 Go runner.
//
// Each job kind is dispatched to a dedicated handler with:
// - Context-based timeout (derived from job lease)
// - Structured error classification (ExecError → FailureCategory)
// - Output size limiting
// - Graceful cancellation awareness
package runnerexec

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
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

// ── Executor ────────────────────────────────────────────────────────

// Executor dispatches and monitors job execution per kind.
type Executor struct {
	cfg Config
}

// New creates an Executor with sane defaults.
func New(cfg Config) *Executor {
	if cfg.DefaultTimeoutSeconds <= 0 {
		cfg.DefaultTimeoutSeconds = 300
	}
	if cfg.MaxOutputBytes <= 0 {
		cfg.MaxOutputBytes = 1 << 20 // 1 MB
	}
	return &Executor{cfg: cfg}
}

// Run executes a job with timeout enforcement and error classification.
// leaseSeconds is the remaining lease time; the executor will finish
// slightly before it to allow reporting headroom.
func (e *Executor) Run(ctx context.Context, kind string, payload map[string]any, leaseSeconds int) (Result, error) {
	timeout := e.effectiveTimeout(leaseSeconds)
	execCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	result, err := e.dispatch(execCtx, kind, payload)
	if err != nil {
		return result, classifyError(err, kind)
	}
	return e.truncateOutput(result), nil
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
		return runConnectorInvoke(ctx, payload)
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

// runConnectorInvoke handles connector dispatch jobs with payload validation.
func runConnectorInvoke(ctx context.Context, payload map[string]any) (Result, error) {
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

// ── Error classification ────────────────────────────────────────────

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
