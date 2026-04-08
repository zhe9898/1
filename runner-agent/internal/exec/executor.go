// Package runnerexec provides the job execution layer for the ZEN70 Go runner.
package runnerexec

import (
	"context"
	"net/http"
	"sync"
	"time"
)

const (
	// DefaultJobTimeoutSeconds is the fallback job timeout when the lease
	// does not specify one.
	DefaultJobTimeoutSeconds = 300

	// DefaultMaxOutputBytes is the output truncation limit.
	DefaultMaxOutputBytes = 1 << 20

	// DefaultHTTPClientTimeout is the timeout for outbound HTTP requests made
	// by HTTP-backed job kinds.
	DefaultHTTPClientTimeout = 30 * time.Second
)

// ExecError carries structured failure classification that maps to the Python
// FailureCategory enum so the gateway can make smart retry decisions.
type ExecError struct {
	Message  string
	Category string
	Details  map[string]any
}

func (e *ExecError) Error() string { return e.Message }

// Result holds job execution output.
type Result struct {
	Summary string
	Output  string
}

// Config holds executor tunables.
type Config struct {
	DefaultTimeoutSeconds int
	MaxOutputBytes        int
}

type activeJob struct {
	JobID  string
	Kind   string
	Start  time.Time
	Cancel context.CancelFunc
}

// ActiveJobInfo is the read-only view returned by ActiveJobs.
type ActiveJobInfo struct {
	JobID   string
	Kind    string
	Running time.Duration
}

// Executor dispatches and monitors job execution per kind.
type Executor struct {
	cfg          Config
	httpClient   *http.Client
	kindHandlers *kindHandlerRegistry

	mu         sync.Mutex
	activeJobs map[string]*activeJob
}

// New creates an Executor with sane defaults.
func New(cfg Config, httpClient *http.Client) *Executor {
	if cfg.DefaultTimeoutSeconds <= 0 {
		cfg.DefaultTimeoutSeconds = DefaultJobTimeoutSeconds
	}
	if cfg.MaxOutputBytes <= 0 {
		cfg.MaxOutputBytes = DefaultMaxOutputBytes
	}
	if httpClient == nil {
		httpClient = &http.Client{Timeout: DefaultHTTPClientTimeout}
	}

	executor := &Executor{
		cfg:        cfg,
		httpClient: httpClient,
		activeJobs: make(map[string]*activeJob),
	}
	executor.kindHandlers = buildBuiltInKindHandlerRegistry(executor)
	return executor
}

// Run executes a job with timeout enforcement and error classification.
func (e *Executor) Run(ctx context.Context, kind string, payload map[string]any, leaseSeconds int) (Result, error) {
	return e.RunJob(ctx, "", kind, payload, leaseSeconds)
}

// RunJob executes a job with tracking by jobID for cancel and active job
// reporting support.
func (e *Executor) RunJob(
	ctx context.Context,
	jobID string,
	kind string,
	payload map[string]any,
	leaseSeconds int,
) (Result, error) {
	timeout := e.effectiveTimeout(leaseSeconds)
	execCtx, cancel := context.WithTimeout(ctx, timeout)

	if jobID != "" {
		e.mu.Lock()
		e.activeJobs[jobID] = &activeJob{
			JobID:  jobID,
			Kind:   kind,
			Start:  time.Now(),
			Cancel: cancel,
		}
		e.mu.Unlock()
	}

	defer func() {
		cancel()
		if jobID != "" {
			e.mu.Lock()
			delete(e.activeJobs, jobID)
			e.mu.Unlock()
		}
	}()

	result, err := e.dispatch(execCtx, kind, payload)
	if err != nil {
		return result, classifyError(err, kind)
	}
	return e.truncateOutput(result), nil
}

// Cancel cancels a running job by its ID.
func (e *Executor) Cancel(jobID string) bool {
	e.mu.Lock()
	aj, ok := e.activeJobs[jobID]
	e.mu.Unlock()
	if !ok {
		return false
	}
	aj.Cancel()
	return true
}

// ActiveJobs returns a snapshot of all currently executing jobs.
func (e *Executor) ActiveJobs() []ActiveJobInfo {
	e.mu.Lock()
	defer e.mu.Unlock()

	now := time.Now()
	info := make([]ActiveJobInfo, 0, len(e.activeJobs))
	for _, aj := range e.activeJobs {
		info = append(info, ActiveJobInfo{
			JobID:   aj.JobID,
			Kind:    aj.Kind,
			Running: now.Sub(aj.Start),
		})
	}
	return info
}

// ActiveJobCount returns the number of currently executing jobs.
func (e *Executor) ActiveJobCount() int {
	e.mu.Lock()
	defer e.mu.Unlock()
	return len(e.activeJobs)
}

// RecoverOrphanedJobs clears any in-memory active jobs after restart.
func (e *Executor) RecoverOrphanedJobs() int {
	e.mu.Lock()
	defer e.mu.Unlock()

	count := len(e.activeJobs)
	e.activeJobs = make(map[string]*activeJob)
	return count
}

func (e *Executor) effectiveTimeout(leaseSeconds int) time.Duration {
	if leaseSeconds > 10 {
		return time.Duration(leaseSeconds-5) * time.Second
	}
	if leaseSeconds > 0 {
		return time.Duration(leaseSeconds) * time.Second
	}
	return time.Duration(e.cfg.DefaultTimeoutSeconds) * time.Second
}

func (e *Executor) truncateOutput(r Result) Result {
	if len(r.Output) > e.cfg.MaxOutputBytes {
		r.Output = r.Output[:e.cfg.MaxOutputBytes]
	}
	return r
}
