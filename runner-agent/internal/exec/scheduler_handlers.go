package runnerexec

import (
	"context"
	"encoding/json"
	"fmt"
	"time"
)

func runCronTick(ctx context.Context, payload map[string]any) (Result, error) {
	scheduleID, err := requiredStringField(payload, "schedule_id")
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

	cronExpr, err := optionalStringField(payload, "cron_expression")
	if err != nil {
		return Result{}, err
	}
	actionPayload, err := optionalObjectField(payload, "action_payload")
	if err != nil {
		return Result{}, err
	}

	start := time.Now()
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
	actionResult["duration_seconds"] = time.Since(start).Seconds()

	outBytes, _ := json.Marshal(actionResult)
	return Result{
		Summary: fmt.Sprintf("cron.tick %s/%s triggered", scheduleID, action),
		Output:  string(outBytes),
	}, nil
}
