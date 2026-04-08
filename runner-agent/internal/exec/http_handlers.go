package runnerexec

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

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

	endpoint, err := optionalStringField(payload, "endpoint")
	if err != nil {
		return Result{}, err
	}
	if endpoint != "" {
		return e.invokeConnectorHTTP(ctx, endpoint, connectorID, connectorKind, action, payload)
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
		Summary: fmt.Sprintf("connector.invoke: %s/%s -> %d", connectorID, action, resp.StatusCode),
		Output:  string(respBody),
	}, nil
}

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
		Summary: fmt.Sprintf("http.request: %s %s -> %d", method, urlStr, resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}

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
		Summary: fmt.Sprintf("alert.notify: %s -> %d", validatedURL.String(), resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}

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
		Summary: fmt.Sprintf("cron.trigger %s -> %d", cronName, resp.StatusCode),
		Output:  string(outBytes),
	}, nil
}
