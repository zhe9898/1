package parityfixtures

import (
	"encoding/json"
	"fmt"
	"os"

	pb "zen70/placement-solver/gen/placement"
)

type FixtureFile struct {
	Cases []Case `json:"cases"`
}

type Case struct {
	Name          string           `json:"name"`
	AcceptedKinds []string         `json:"accepted_kinds"`
	NodeTemplates []NodeTemplate   `json:"node_templates"`
	JobTemplates  []JobTemplate    `json:"job_templates"`
	Expectations  CaseExpectations `json:"expectations"`
}

type CaseExpectations struct {
	Result                string   `json:"result"`
	AssignedCount         int      `json:"assigned_count"`
	OnlyNodePrefixes      []string `json:"only_node_prefixes"`
	ForbiddenNodePrefixes []string `json:"forbidden_node_prefixes"`
}

type NodeTemplate struct {
	Count                  int      `json:"count"`
	IDPrefix               string   `json:"id_prefix"`
	IDStart                int      `json:"id_start"`
	IDPadWidth             int      `json:"id_pad_width"`
	Os                     string   `json:"os"`
	Arch                   string   `json:"arch"`
	Executor               string   `json:"executor"`
	ExecutorContract       string   `json:"executor_contract"`
	Zone                   string   `json:"zone"`
	Capabilities           []string `json:"capabilities"`
	AcceptedKinds          []string `json:"accepted_kinds"`
	SupportedWorkloadKinds []string `json:"supported_workload_kinds"`
	WorkerPools            []string `json:"worker_pools"`
	MaxConcurrency         int32    `json:"max_concurrency"`
	ActiveLeaseCount       int32    `json:"active_lease_count"`
	CpuCores               int32    `json:"cpu_cores"`
	MemoryMb               int32    `json:"memory_mb"`
	GpuVramMb              int32    `json:"gpu_vram_mb"`
	StorageMb              int32    `json:"storage_mb"`
	ReliabilityScore       float32  `json:"reliability_score"`
	EnrollmentStatus       string   `json:"enrollment_status"`
	Status                 string   `json:"status"`
	DrainStatus            string   `json:"drain_status"`
	NetworkLatencyMs       int32    `json:"network_latency_ms"`
	CachedDataKeys         []string `json:"cached_data_keys"`
	PowerCapacityWatts     int32    `json:"power_capacity_watts"`
	CurrentPowerWatts      int32    `json:"current_power_watts"`
	ThermalState           string   `json:"thermal_state"`
	CloudConnectivity      string   `json:"cloud_connectivity"`
}

type JobTemplate struct {
	Count                int      `json:"count"`
	IDPrefix             string   `json:"id_prefix"`
	IDStart              int      `json:"id_start"`
	IDPadWidth           int      `json:"id_pad_width"`
	Kind                 string   `json:"kind"`
	PriorityStart        int32    `json:"priority_start"`
	PriorityStep         int32    `json:"priority_step"`
	GangSize             int      `json:"gang_size"`
	GangPrefix           string   `json:"gang_prefix"`
	TenantID             string   `json:"tenant_id"`
	TargetOs             string   `json:"target_os"`
	TargetArch           string   `json:"target_arch"`
	TargetZone           string   `json:"target_zone"`
	TargetExecutor       string   `json:"target_executor"`
	RequiredCapabilities []string `json:"required_capabilities"`
	RequiredCpuCores     int32    `json:"required_cpu_cores"`
	RequiredMemoryMb     int32    `json:"required_memory_mb"`
	RequiredGpuVramMb    int32    `json:"required_gpu_vram_mb"`
	RequiredStorageMb    int32    `json:"required_storage_mb"`
	MaxNetworkLatencyMs  int32    `json:"max_network_latency_ms"`
	DataLocalityKey      string   `json:"data_locality_key"`
	PreferCachedData     bool     `json:"prefer_cached_data"`
	PowerBudgetWatts     int32    `json:"power_budget_watts"`
	ThermalSensitivity   string   `json:"thermal_sensitivity"`
	CloudFallbackEnabled bool     `json:"cloud_fallback_enabled"`
	QueueClass           string   `json:"queue_class"`
	WorkerPool           string   `json:"worker_pool"`
}

type ExpandedCase struct {
	Name         string
	Request      *pb.SolveRequest
	Expectations CaseExpectations
}

func Load(path string) (*FixtureFile, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}

	var fixtures FixtureFile
	if err := json.Unmarshal(content, &fixtures); err != nil {
		return nil, err
	}
	return &fixtures, nil
}

func Expand(path string) ([]ExpandedCase, error) {
	fixtures, err := Load(path)
	if err != nil {
		return nil, err
	}

	expanded := make([]ExpandedCase, 0, len(fixtures.Cases))
	for _, testCase := range fixtures.Cases {
		req, err := buildSolveRequest(testCase)
		if err != nil {
			return nil, fmt.Errorf("expand %s: %w", testCase.Name, err)
		}
		expanded = append(expanded, ExpandedCase{
			Name:         testCase.Name,
			Request:      req,
			Expectations: testCase.Expectations,
		})
	}
	return expanded, nil
}

func buildSolveRequest(testCase Case) (*pb.SolveRequest, error) {
	nodes, err := expandNodes(testCase.NodeTemplates)
	if err != nil {
		return nil, err
	}
	jobs, err := expandJobs(testCase.JobTemplates)
	if err != nil {
		return nil, err
	}
	return &pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: copyStrings(testCase.AcceptedKinds),
	}, nil
}

func expandNodes(templates []NodeTemplate) ([]*pb.NodeSpec, error) {
	var nodes []*pb.NodeSpec
	for _, template := range templates {
		if template.Count <= 0 {
			return nil, fmt.Errorf("node template %q must have count > 0", template.IDPrefix)
		}
		for offset := 0; offset < template.Count; offset++ {
			nodes = append(nodes, &pb.NodeSpec{
				NodeId:                 expandID(template.IDPrefix, template.IDStart+offset, template.IDPadWidth),
				Os:                     template.Os,
				Arch:                   template.Arch,
				Executor:               template.Executor,
				ExecutorContract:       template.ExecutorContract,
				Zone:                   template.Zone,
				Capabilities:           copyStrings(template.Capabilities),
				AcceptedKinds:          copyStrings(template.AcceptedKinds),
				SupportedWorkloadKinds: copyStrings(template.SupportedWorkloadKinds),
				WorkerPools:            copyStrings(template.WorkerPools),
				MaxConcurrency:         template.MaxConcurrency,
				ActiveLeaseCount:       template.ActiveLeaseCount,
				CpuCores:               template.CpuCores,
				MemoryMb:               template.MemoryMb,
				GpuVramMb:              template.GpuVramMb,
				StorageMb:              template.StorageMb,
				ReliabilityScore:       template.ReliabilityScore,
				EnrollmentStatus:       template.EnrollmentStatus,
				Status:                 template.Status,
				DrainStatus:            template.DrainStatus,
				NetworkLatencyMs:       template.NetworkLatencyMs,
				CachedDataKeys:         copyStrings(template.CachedDataKeys),
				PowerCapacityWatts:     template.PowerCapacityWatts,
				CurrentPowerWatts:      template.CurrentPowerWatts,
				ThermalState:           template.ThermalState,
				CloudConnectivity:      template.CloudConnectivity,
			})
		}
	}
	return nodes, nil
}

func expandJobs(templates []JobTemplate) ([]*pb.JobSpec, error) {
	var jobs []*pb.JobSpec
	for _, template := range templates {
		if template.Count <= 0 {
			return nil, fmt.Errorf("job template %q must have count > 0", template.IDPrefix)
		}
		for offset := 0; offset < template.Count; offset++ {
			job := &pb.JobSpec{
				JobId:                expandID(template.IDPrefix, template.IDStart+offset, template.IDPadWidth),
				Kind:                 template.Kind,
				Priority:             template.PriorityStart + int32(offset)*template.PriorityStep,
				TenantId:             template.TenantID,
				TargetOs:             template.TargetOs,
				TargetArch:           template.TargetArch,
				TargetZone:           template.TargetZone,
				TargetExecutor:       template.TargetExecutor,
				RequiredCapabilities: copyStrings(template.RequiredCapabilities),
				RequiredCpuCores:     template.RequiredCpuCores,
				RequiredMemoryMb:     template.RequiredMemoryMb,
				RequiredGpuVramMb:    template.RequiredGpuVramMb,
				RequiredStorageMb:    template.RequiredStorageMb,
				MaxNetworkLatencyMs:  template.MaxNetworkLatencyMs,
				DataLocalityKey:      template.DataLocalityKey,
				PreferCachedData:     template.PreferCachedData,
				PowerBudgetWatts:     template.PowerBudgetWatts,
				ThermalSensitivity:   template.ThermalSensitivity,
				CloudFallbackEnabled: template.CloudFallbackEnabled,
				QueueClass:           template.QueueClass,
				WorkerPool:           template.WorkerPool,
			}
			if template.GangSize > 0 {
				gangPrefix := template.GangPrefix
				if gangPrefix == "" {
					gangPrefix = template.IDPrefix + "gang-"
				}
				job.GangId = fmt.Sprintf("%s%d", gangPrefix, offset/template.GangSize)
			}
			jobs = append(jobs, job)
		}
	}
	return jobs, nil
}

func expandID(prefix string, index int, padWidth int) string {
	if padWidth > 0 {
		return fmt.Sprintf("%s%0*d", prefix, padWidth, index)
	}
	return fmt.Sprintf("%s%d", prefix, index)
}

func copyStrings(values []string) []string {
	if len(values) == 0 {
		return nil
	}
	copied := make([]string, len(values))
	copy(copied, values)
	return copied
}
