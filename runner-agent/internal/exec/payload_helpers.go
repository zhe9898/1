package runnerexec

import "fmt"

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
