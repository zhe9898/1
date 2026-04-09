package solver_test

import (
	"fmt"
	"testing"
	"time"

	pb "zen70/placement-solver/gen/placement"
	"zen70/placement-solver/internal/solver"
)

func makeNode(id string, maxConc int32) *pb.NodeSpec {
	return &pb.NodeSpec{
		NodeId:             id,
		Os:                 "linux",
		Arch:               "amd64",
		Executor:           "docker",
		Zone:               fmt.Sprintf("z%d", len(id)%4),
		Capabilities:       []string{"shell", "docker"},
		AcceptedKinds:      []string{"shell.exec"},
		WorkerPools:        []string{"batch"},
		MaxConcurrency:     maxConc,
		ActiveLeaseCount:   0,
		CpuCores:           16,
		MemoryMb:           32768,
		ReliabilityScore:   0.95,
		EnrollmentStatus:   "active",
		Status:             "online",
		DrainStatus:        "active",
		NetworkLatencyMs:   10,
		PowerCapacityWatts: 200,
		CurrentPowerWatts:  80,
		ThermalState:       "normal",
		CloudConnectivity:  "online",
	}
}

func makeJob(id, kind string, priority int32) *pb.JobSpec {
	return &pb.JobSpec{
		JobId:    id,
		Kind:     kind,
		Priority: priority,
	}
}

// TestSolve_HomogeneousBatch verifies 100% placement of a large uniform batch.
func TestSolve_HomogeneousBatch(t *testing.T) {
	const nNodes, nJobs = 1000, 10000
	nodes := make([]*pb.NodeSpec, nNodes)
	for i := range nodes {
		nodes[i] = makeNode(fmt.Sprintf("n%d", i), 16)
	}
	jobs := make([]*pb.JobSpec, nJobs)
	for i := range jobs {
		jobs[i] = makeJob(fmt.Sprintf("j%d", i), "shell.exec", int32(50+i%50))
	}

	req := &pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: []string{"shell.exec"},
	}

	start := time.Now()
	res := solver.Solve(req)
	elapsed := time.Since(start)

	if len(res.Assignments) != nJobs {
		t.Errorf("expected %d assignments, got %d", nJobs, len(res.Assignments))
	}
	t.Logf("homogeneous 1k×10k: %d assignments in %v", len(res.Assignments), elapsed)
	if elapsed > 50*time.Millisecond {
		t.Errorf("solve time %v exceeds 50ms budget", elapsed)
	}
}

// TestSolve_HeterogeneousBatch verifies correct placement when jobs have
// 10 distinct routing profiles (simulating a mixed smart-home workload).
func TestSolve_HeterogeneousBatch(t *testing.T) {
	const nNodes = 1000
	// Nodes support all 10 kinds
	allKinds := []string{
		"light.toggle", "thermostat.set", "sensor.query",
		"camera.snapshot", "lock.control", "fan.speed",
		"sprinkler.run", "alarm.trigger", "ota.update", "shell.exec",
	}
	nodes := make([]*pb.NodeSpec, nNodes)
	for i := range nodes {
		n := makeNode(fmt.Sprintf("n%d", i), 16)
		n.AcceptedKinds = allKinds
		nodes[i] = n
	}

	const nJobs = 10000
	jobs := make([]*pb.JobSpec, nJobs)
	for i := range jobs {
		kind := allKinds[i%len(allKinds)]
		jobs[i] = makeJob(fmt.Sprintf("j%d", i), kind, int32(50+i%50))
	}

	req := &pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: allKinds,
	}

	start := time.Now()
	res := solver.Solve(req)
	elapsed := time.Since(start)

	if len(res.Assignments) != nJobs {
		t.Errorf("expected %d assignments, got %d", nJobs, len(res.Assignments))
	}
	t.Logf("heterogeneous 1k×10k (10 kinds): %d assignments in %v", len(res.Assignments), elapsed)
	if elapsed > 100*time.Millisecond {
		t.Errorf("solve time %v exceeds 100ms budget", elapsed)
	}
}

// TestSolve_NoCapacity verifies graceful handling when nodes are full.
func TestSolve_NoCapacity(t *testing.T) {
	nodes := []*pb.NodeSpec{makeNode("n0", 0)} // zero capacity
	jobs := []*pb.JobSpec{makeJob("j0", "shell.exec", 50)}
	res := solver.Solve(&pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: []string{"shell.exec"},
	})
	if len(res.Assignments) != 0 {
		t.Errorf("expected 0 assignments for zero-capacity node, got %d", len(res.Assignments))
	}
}

// TestSolve_DrainedNode verifies draining nodes are excluded.
func TestSolve_DrainedNode(t *testing.T) {
	n := makeNode("n0", 16)
	n.DrainStatus = "draining"
	res := solver.Solve(&pb.SolveRequest{
		Jobs:          []*pb.JobSpec{makeJob("j0", "shell.exec", 50)},
		Nodes:         []*pb.NodeSpec{n},
		AcceptedKinds: []string{"shell.exec"},
	})
	if len(res.Assignments) != 0 {
		t.Errorf("expected 0 assignments for draining node, got %d", len(res.Assignments))
	}
}

// TestSolve_ApprovedNodesAreEligible verifies backend-approved nodes are
// treated as live by the Go fast path. The backend control plane persists
// "approved", while older standalone tests used "active".
func TestSolve_ApprovedNodesAreEligible(t *testing.T) {
	n := makeNode("n0", 16)
	n.EnrollmentStatus = "approved"
	res := solver.Solve(&pb.SolveRequest{
		Jobs:          []*pb.JobSpec{makeJob("j0", "shell.exec", 50)},
		Nodes:         []*pb.NodeSpec{n},
		AcceptedKinds: []string{"shell.exec"},
	})
	if len(res.Assignments) != 1 {
		t.Fatalf("expected 1 assignment for approved node, got %d", len(res.Assignments))
	}
	if got := res.Assignments["j0"]; got != "n0" {
		t.Fatalf("expected j0 -> n0, got %q", got)
	}
}

// TestSolve_CapacityNotExceeded verifies the shared capacity map prevents
// over-commit when multiple routing groups share the same node pool.
func TestSolve_CapacityNotExceeded(t *testing.T) {
	// 2 nodes with concurrency 5 each → total 10 slots
	nodes := []*pb.NodeSpec{
		makeNode("n0", 5),
		makeNode("n1", 5),
	}
	for _, n := range nodes {
		n.AcceptedKinds = []string{"shell.exec", "light.toggle"}
	}

	// 10 jobs: 5 of each kind → should fill all 10 slots exactly
	jobs := make([]*pb.JobSpec, 10)
	for i := range jobs {
		kind := "shell.exec"
		if i >= 5 {
			kind = "light.toggle"
		}
		jobs[i] = makeJob(fmt.Sprintf("j%d", i), kind, 50)
	}

	res := solver.Solve(&pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: []string{"shell.exec", "light.toggle"},
	})

	if len(res.Assignments) != 10 {
		t.Errorf("expected 10 assignments, got %d", len(res.Assignments))
	}

	// Count per-node usage and verify no node exceeds capacity
	usage := make(map[string]int)
	for _, nid := range res.Assignments {
		usage[nid]++
	}
	for _, n := range nodes {
		if usage[n.NodeId] > int(n.MaxConcurrency) {
			t.Errorf("node %s over-committed: %d > %d", n.NodeId, usage[n.NodeId], n.MaxConcurrency)
		}
	}
}

// BenchmarkSolve_1k10k measures pure solver throughput.
func BenchmarkSolve_1k10k(b *testing.B) {
	const nNodes, nJobs = 1000, 10000
	nodes := make([]*pb.NodeSpec, nNodes)
	for i := range nodes {
		nodes[i] = makeNode(fmt.Sprintf("n%d", i), 16)
	}
	jobs := make([]*pb.JobSpec, nJobs)
	for i := range jobs {
		jobs[i] = makeJob(fmt.Sprintf("j%d", i), "shell.exec", int32(50+i%50))
	}
	req := &pb.SolveRequest{
		Jobs:          jobs,
		Nodes:         nodes,
		AcceptedKinds: []string{"shell.exec"},
	}

	b.ResetTimer()
	for range b.N {
		solver.Solve(req)
	}
}
