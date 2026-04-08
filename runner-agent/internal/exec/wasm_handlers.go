package runnerexec

import (
	"context"
	"encoding/json"
	"fmt"
)

func runWasmRun(ctx context.Context, payload map[string]any) (Result, error) {
	moduleURI, err := requiredStringField(payload, "module_uri")
	if err != nil {
		return Result{}, err
	}
	validatedModuleURI, execErr := validateManagedURI(moduleURI, "module_uri", []string{"https"}, ".wasm")
	if execErr != nil {
		return Result{}, execErr
	}
	moduleURI = validatedModuleURI.String()

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	function, err := optionalStringField(payload, "function")
	if err != nil {
		return Result{}, err
	}
	if function == "" {
		function = "_start"
	}

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
			Message:  "WASM runtime not yet linked - install wazero for production execution",
			Category: "execution_error",
			Details:  map[string]any{"module": moduleURI, "function": function},
		}
}
