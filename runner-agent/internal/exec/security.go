package runnerexec

import (
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
)

var blockedOutboundHosts = map[string]struct{}{
	"localhost":                {},
	"localhost.localdomain":    {},
	"metadata":                 {},
	"metadata.google.internal": {},
}

func validateOutboundHTTPURL(raw string, fieldName string) (*url.URL, *ExecError) {
	trimmed := strings.TrimSpace(raw)
	parsed, err := url.Parse(trimmed)
	if err != nil || parsed.Scheme == "" || parsed.Hostname() == "" {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must be a valid HTTP or HTTPS URL", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	if parsed.Scheme != "http" && parsed.Scheme != "https" {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must use http or https", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	if parsed.User != nil {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must not embed credentials", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	host := strings.TrimSuffix(strings.ToLower(parsed.Hostname()), ".")
	if _, blocked := blockedOutboundHosts[host]; blocked || strings.HasSuffix(host, ".localhost") {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must not target loopback or metadata hosts", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw, "host": host},
		}
	}
	if ip := net.ParseIP(host); ip != nil && !isPublicIP(ip) {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must not target private, loopback, or link-local IP ranges", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw, "host": host},
		}
	}
	return parsed, nil
}

func validateManagedURI(raw string, fieldName string, allowedSchemes []string, requireSuffix string) (*url.URL, *ExecError) {
	trimmed := strings.TrimSpace(raw)
	parsed, err := url.Parse(trimmed)
	if err != nil || parsed.Scheme == "" || parsed.Hostname() == "" {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must be a valid URI with an approved scheme", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	scheme := strings.ToLower(parsed.Scheme)
	allowed := make(map[string]struct{}, len(allowedSchemes))
	for _, candidate := range allowedSchemes {
		normalized := strings.ToLower(strings.TrimSpace(candidate))
		if normalized != "" {
			allowed[normalized] = struct{}{}
		}
	}
	if _, ok := allowed[scheme]; !ok {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must use one of: %s", fieldName, strings.Join(allowedSchemes, ", ")),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw, "scheme": scheme},
		}
	}
	if parsed.User != nil {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must not embed credentials", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	if scheme == "http" || scheme == "https" {
		validated, execErr := validateOutboundHTTPURL(trimmed, fieldName)
		if execErr != nil {
			return nil, execErr
		}
		parsed = validated
	}
	if requireSuffix != "" && !strings.HasSuffix(strings.ToLower(parsed.Path), strings.ToLower(requireSuffix)) {
		return nil, &ExecError{
			Message:  fmt.Sprintf("%s must end with %s", fieldName, requireSuffix),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw, "suffix": requireSuffix},
		}
	}
	return parsed, nil
}

func isPublicIP(ip net.IP) bool {
	return !(ip.IsPrivate() ||
		ip.IsLoopback() ||
		ip.IsLinkLocalMulticast() ||
		ip.IsLinkLocalUnicast() ||
		ip.IsInterfaceLocalMulticast() ||
		ip.IsMulticast() ||
		ip.IsUnspecified())
}

func allowedFileTransferRoots() ([]string, *ExecError) {
	configured := strings.TrimSpace(os.Getenv("ZEN70_FILE_TRANSFER_ROOTS"))
	rootSet := make([]string, 0, 4)
	addRoot := func(candidate string) *ExecError {
		trimmed := strings.TrimSpace(candidate)
		if trimmed == "" {
			return nil
		}
		absRoot, err := filepath.Abs(trimmed)
		if err != nil {
			return &ExecError{
				Message:  fmt.Sprintf("invalid configured file transfer root: %v", err),
				Category: "invalid_payload",
				Details:  map[string]any{"root": candidate},
			}
		}
		rootSet = append(rootSet, filepath.Clean(absRoot))
		return nil
	}
	if configured != "" {
		for _, part := range strings.Split(configured, string(os.PathListSeparator)) {
			if execErr := addRoot(part); execErr != nil {
				return nil, execErr
			}
		}
	} else {
		cwd, err := os.Getwd()
		if err != nil {
			return nil, &ExecError{
				Message:  fmt.Sprintf("failed to resolve working directory: %v", err),
				Category: "invalid_payload",
			}
		}
		if execErr := addRoot(cwd); execErr != nil {
			return nil, execErr
		}
		if execErr := addRoot(os.TempDir()); execErr != nil {
			return nil, execErr
		}
	}
	return rootSet, nil
}

func validateFileTransferPath(raw string, roots []string, fieldName string) (string, *ExecError) {
	trimmed := strings.TrimSpace(raw)
	lowerTrimmed := strings.ToLower(trimmed)
	if trimmed == "" {
		return "", &ExecError{
			Message:  fmt.Sprintf("%s must not be empty", fieldName),
			Category: "invalid_payload",
		}
	}
	if strings.Contains(trimmed, "://") || strings.HasPrefix(lowerTrimmed, "file:") {
		return "", &ExecError{
			Message:  fmt.Sprintf("%s must be a local filesystem path, not a URI", fieldName),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	absPath, err := filepath.Abs(trimmed)
	if err != nil {
		return "", &ExecError{
			Message:  fmt.Sprintf("invalid %s path: %v", fieldName, err),
			Category: "invalid_payload",
			Details:  map[string]any{fieldName: raw},
		}
	}
	cleanAbsPath := filepath.Clean(absPath)
	for _, root := range roots {
		if pathWithinRoot(cleanAbsPath, root) {
			return cleanAbsPath, nil
		}
	}
	return "", &ExecError{
		Message:  fmt.Sprintf("%s is outside the allowed file transfer roots", fieldName),
		Category: "invalid_payload",
		Details:  map[string]any{fieldName: cleanAbsPath, "allowed_roots": roots},
	}
}

func pathWithinRoot(candidate string, root string) bool {
	rel, err := filepath.Rel(root, candidate)
	if err != nil {
		return false
	}
	if rel == "." {
		return true
	}
	return rel != ".." && !strings.HasPrefix(rel, ".."+string(os.PathSeparator))
}
