package runnerexec

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func runFileTransfer(_ context.Context, payload map[string]any) (Result, error) {
	src, err := requiredStringField(payload, "src")
	if err != nil {
		return Result{}, err
	}
	dst, err := requiredStringField(payload, "dst")
	if err != nil {
		return Result{}, err
	}

	roots, execErr := allowedFileTransferRoots()
	if execErr != nil {
		return Result{}, execErr
	}

	absSrc, execErr := validateFileTransferPath(src, roots, "src")
	if execErr != nil {
		return Result{}, execErr
	}
	absDst, execErr := validateFileTransferPath(dst, roots, "dst")
	if execErr != nil {
		return Result{}, execErr
	}

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

	srcFile, err := os.Open(absSrc)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to open source: %v", err),
			Category: "transient",
			Details:  map[string]any{"src": absSrc},
		}
	}
	defer srcFile.Close()

	dstFile, err := os.Create(absDst)
	if err != nil {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("failed to create destination: %v", err),
			Category: "transient",
			Details:  map[string]any{"dst": absDst},
		}
	}

	hasher := sha256.New()
	writer := io.MultiWriter(dstFile, hasher)
	start := time.Now()
	bytesWritten, err := io.Copy(writer, srcFile)
	durationMs := float64(time.Since(start).Microseconds()) / 1000.0

	if closeErr := dstFile.Close(); closeErr != nil && err == nil {
		err = closeErr
	}

	if err != nil {
		_ = os.Remove(absDst)
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("file copy failed: %v", err),
			Category: "transient",
			Details:  map[string]any{"src": absSrc, "dst": absDst, "bytes_written": bytesWritten},
		}
	}

	actualHash := hex.EncodeToString(hasher.Sum(nil))

	expected, err := optionalStringField(payload, "verify_sha256")
	if err != nil {
		return Result{}, err
	}
	if expected != "" && actualHash != expected {
		_ = os.Remove(absDst)
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("SHA-256 mismatch: expected %s, got %s", expected, actualHash),
			Category: "execution_error",
			Details:  map[string]any{"expected": expected, "actual": actualHash},
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
		Summary: fmt.Sprintf("file.transfer: %s -> %s (%d bytes, %.1fms)", absSrc, absDst, bytesWritten, durationMs),
		Output:  string(outBytes),
	}, nil
}

func runDataSync(ctx context.Context, payload map[string]any) (Result, error) {
	sourceURI, err := requiredStringField(payload, "source_uri")
	if err != nil {
		return Result{}, err
	}
	validatedSourceURI, execErr := validateManagedURI(sourceURI, "source_uri", []string{"rsync"}, "")
	if execErr != nil {
		return Result{}, execErr
	}

	destURI, err := requiredStringField(payload, "dest_uri")
	if err != nil {
		return Result{}, err
	}
	validatedDestURI, execErr := validateManagedURI(destURI, "dest_uri", []string{"rsync"}, "")
	if execErr != nil {
		return Result{}, execErr
	}

	sourceURI = validatedSourceURI.String()
	destURI = validatedDestURI.String()

	select {
	case <-ctx.Done():
		return Result{}, ctx.Err()
	default:
	}

	direction, err := optionalStringField(payload, "direction")
	if err != nil {
		return Result{}, err
	}
	if direction == "" {
		direction = "push"
	}

	args := []string{"-avz", "--progress"}

	if bw, ok := payload["bandwidth_limit_kbps"].(float64); ok && bw > 0 {
		args = append(args, fmt.Sprintf("--bwlimit=%d", int(bw)))
	}

	filters, err := optionalStringListField(payload, "filters")
	if err != nil {
		return Result{}, err
	}
	for _, filter := range filters {
		args = append(args, "--include="+filter)
	}

	switch direction {
	case "push", "pull":
		args = append(args, sourceURI, destURI)
	default:
		args = append(args, sourceURI, destURI)
	}

	cmd := exec.CommandContext(ctx, "rsync", args...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	start := time.Now()
	err = cmd.Run()
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
				Summary: fmt.Sprintf("data.sync %s->%s: FAILED", sourceURI, destURI),
				Output:  string(outBytes),
			}, &ExecError{
				Message:  fmt.Sprintf("data.sync failed: %v", err),
				Category: "transient",
				Details:  map[string]any{"source": sourceURI, "dest": destURI, "stderr": stderr.String()},
			}
	}

	outBytes, _ := json.Marshal(output)
	return Result{
		Summary: fmt.Sprintf("data.sync %s->%s completed (%.1fs)", sourceURI, destURI, durationS),
		Output:  string(outBytes),
	}, nil
}
