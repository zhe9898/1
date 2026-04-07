// Package device provides lightweight hardware auto-detection so the
// runner-agent can report a meaningful device_profile without requiring the
// operator to manually set RUNNER_DEVICE_PROFILE.
//
// Detection order (first match wins):
//  1. /proc/cpuinfo – "Model" or "Hardware" line (Raspberry Pi, embedded ARM)
//  2. /sys/class/dmi/id/product_name – DMI BIOS product name (x86 appliances)
//  3. Heuristic: arch × memory → best-fit profile name
package device

import (
	"os"
	"runtime"
	"strconv"
	"strings"
)

// DetectProfile returns the best-matching device profile name for the current
// host.  It never returns an empty string; "generic_edge" is the fallback.
//
// arch should be runtime.GOARCH (or RUNNER_ARCH).
// memoryMB is the node's reported memory; 0 means unknown.
func DetectProfile(arch string, memoryMB int) string {
	arch = strings.ToLower(strings.TrimSpace(arch))

	// 1. /proc/cpuinfo — reliable for Raspberry Pi and many embedded devices
	if name := detectFromCPUInfo(); name != "" {
		return name
	}

	// 2. DMI product name — works on most x86 bare-metal and VMs
	if name := detectFromDMI(); name != "" {
		return name
	}

	// 3. Heuristic fallback
	return heuristicProfile(arch, memoryMB)
}

// detectFromCPUInfo reads /proc/cpuinfo and matches known hardware strings.
func detectFromCPUInfo() string {
	data, err := os.ReadFile("/proc/cpuinfo")
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		lower := strings.ToLower(line)
		if !strings.HasPrefix(lower, "model") && !strings.HasPrefix(lower, "hardware") {
			continue
		}
		if strings.Contains(lower, "raspberry pi 5") {
			return "raspberry_pi_5"
		}
		if strings.Contains(lower, "raspberry pi 4") {
			return "raspberry_pi_4"
		}
		if strings.Contains(lower, "raspberry pi") {
			return "raspberry_pi_4" // Pi 3 and earlier map to the Pi 4 profile
		}
	}
	return ""
}

// detectFromDMI reads the DMI product name sysfs file available on most x86
// systems (Linux only; silently skipped on other platforms).
func detectFromDMI() string {
	if runtime.GOOS != "linux" {
		return ""
	}
	data, err := os.ReadFile("/sys/class/dmi/id/product_name")
	if err != nil {
		return ""
	}
	lower := strings.ToLower(strings.TrimSpace(string(data)))

	switch {
	// Soft routers and network appliances
	case strings.Contains(lower, "protectli"),
		strings.Contains(lower, "qotom"),
		strings.Contains(lower, "lanner"),
		strings.Contains(lower, "fitlet"),
		strings.Contains(lower, "router"),
		strings.Contains(lower, "firewall"):
		return "soft_router"

	// NAS appliances
	case strings.Contains(lower, "synology"),
		strings.Contains(lower, "qnap"),
		strings.Contains(lower, "terramaster"),
		strings.Contains(lower, "nas"):
		return "nas"

	// Mini PCs (common brand keywords)
	case strings.Contains(lower, "nuc"),
		strings.Contains(lower, "beelink"),
		strings.Contains(lower, "bmax"),
		strings.Contains(lower, "minisforum"),
		strings.Contains(lower, "mini pc"),
		strings.Contains(lower, "minipc"):
		return "mini_pc"

	// Cloud / hypervisor VMs
	case strings.Contains(lower, "standard pc"),
		strings.Contains(lower, "hvm domU"),
		strings.Contains(lower, "virtual machine"),
		strings.Contains(lower, "vmware virtual platform"),
		strings.Contains(lower, "kvm"):
		return "cloud_vm"
	}
	return ""
}

// heuristicProfile maps (arch, memoryMB) to a profile name using simple rules.
func heuristicProfile(arch string, memoryMB int) string {
	switch arch {
	case "arm", "arm64":
		switch {
		case memoryMB > 0 && memoryMB <= 1024:
			return "ip_camera"
		case memoryMB > 1024 && memoryMB <= 8192:
			return "raspberry_pi_4"
		default:
			return "generic_edge"
		}

	case "amd64", "386":
		switch {
		case memoryMB > 0 && memoryMB <= 4096:
			return "soft_router"
		case memoryMB > 4096 && memoryMB <= 32768:
			return "mini_pc"
		case memoryMB > 32768:
			return "cloud_vm"
		default:
			return "industrial_x86"
		}

	case "mips", "mipsle", "mips64", "mips64le":
		return "network_switch"
	}

	return "generic_edge"
}

// readMemoryMB attempts to read total physical memory from /proc/meminfo on
// Linux, returning 0 if unavailable.  Used by Load() when RUNNER_MEMORY_MB is
// not set.
func ReadMemoryMB() int {
	if runtime.GOOS != "linux" {
		return 0
	}
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0
	}
	for _, line := range strings.Split(string(data), "\n") {
		if !strings.HasPrefix(line, "MemTotal:") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 2 {
			break
		}
		kbVal, parseErr := strconv.Atoi(fields[1])
		if parseErr != nil {
			break
		}
		return kbVal / 1024
	}
	return 0
}
