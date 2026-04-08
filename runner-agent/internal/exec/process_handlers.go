package runnerexec

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

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
		Summary: "script.run exited 0",
		Output:  string(outBytes),
	}, nil
}

func runDockerExec(ctx context.Context, payload map[string]any) (Result, error) {
	container, err := requiredStringField(payload, "container")
	if err != nil {
		return Result{}, err
	}

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

func runContainerRun(ctx context.Context, payload map[string]any) (Result, error) {
	image, err := requiredStringField(payload, "image")
	if err != nil {
		return Result{}, err
	}

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	args := []string{"run", "--rm"}

	wd, err := optionalStringField(payload, "working_dir")
	if err != nil {
		return Result{}, err
	}
	if wd != "" {
		args = append(args, "-w", wd)
	}

	envMap, err := optionalObjectField(payload, "env")
	if err != nil {
		return Result{}, err
	}
	if envMap != nil {
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

	cmdList, err := optionalStringListField(payload, "command")
	if err != nil {
		return Result{}, err
	}
	if len(cmdList) > 0 {
		args = append(args, cmdList...)
	}

	cmd := exec.CommandContext(ctx, "docker", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err = cmd.Run()
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
