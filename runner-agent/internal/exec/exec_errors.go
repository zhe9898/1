package runnerexec

import (
	"context"
	"errors"
	"fmt"
)

func classifyError(err error, kind string) error {
	if err == nil {
		return nil
	}

	var execErr *ExecError
	if errors.As(err, &execErr) {
		return err
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

func classifyExitCode(code int) string {
	switch {
	case code == 126 || code == 127:
		return "invalid_payload"
	case code == 137:
		return "resource_exhausted"
	case code >= 128:
		return "transient"
	default:
		return "execution_error"
	}
}

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
