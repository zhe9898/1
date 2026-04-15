package jobs

import (
	"context"
	"errors"
	"log"
	"sync"
	"time"

	"zen70/runner-agent/internal/api"
	"zen70/runner-agent/internal/config"
	runnerexec "zen70/runner-agent/internal/exec"
)

const (
	defaultPullFailureBackoff = time.Second
	maxPullFailureBackoff     = 30 * time.Second
	reportingTimeout          = 15 * time.Second
	defaultMinLeaseRenewal    = 5 * time.Second
)

type leasedJob struct {
	mu  sync.RWMutex
	job api.Job
}

func newLeasedJob(job api.Job) *leasedJob {
	return &leasedJob{job: job}
}

func (j *leasedJob) snapshot() api.Job {
	j.mu.RLock()
	defer j.mu.RUnlock()
	return j.job
}

func (j *leasedJob) applyRenewedLease(renewed api.Job) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if renewed.LeaseToken != "" {
		j.job.LeaseToken = renewed.LeaseToken
	}
	if renewed.Attempt > 0 {
		j.job.Attempt = renewed.Attempt
	}
	if renewed.LeasedUntil != "" {
		j.job.LeasedUntil = renewed.LeasedUntil
	}
	if renewed.LeaseSeconds > 0 {
		j.job.LeaseSeconds = renewed.LeaseSeconds
	}
	if renewed.NodeID != "" {
		j.job.NodeID = renewed.NodeID
	}
	if renewed.Status != "" {
		j.job.Status = renewed.Status
	}
}

// Loop pulls and executes jobs until ctx is cancelled.
// Jobs are executed concurrently up to cfg.MaxConcurrency using a worker pool.
func Loop(ctx context.Context, cfg config.Config, client *api.Client, executor *runnerexec.Executor) error {
	ticker := time.NewTicker(cfg.PullInterval)
	defer ticker.Stop()

	concurrency := max(1, cfg.MaxConcurrency)
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup
	pullFailureBackoff := defaultPullFailureBackoff

	for {
		jobs, err := client.PullJobs(ctx, api.PullRequest{
			TenantID:      cfg.TenantID,
			NodeID:        cfg.NodeID,
			Limit:         concurrency,
			AcceptedKinds: cfg.EffectiveAcceptedKinds(),
		})
		if err != nil {
			log.Printf("job pull failed: %v", err)
			if !waitForDuration(ctx, pullFailureBackoff) {
				wg.Wait()
				return ctx.Err()
			}
			pullFailureBackoff = minDuration(pullFailureBackoff*2, maxPullFailureBackoff)
			continue
		}
		pullFailureBackoff = defaultPullFailureBackoff

		for _, job := range jobs {
			select {
			case sem <- struct{}{}:
			case <-ctx.Done():
				wg.Wait()
				return ctx.Err()
			}
			wg.Add(1)
			go func(j api.Job) {
				defer wg.Done()
				defer func() { <-sem }()
				executeAndReport(ctx, cfg, client, executor, j)
			}(job)
		}

		select {
		case <-ctx.Done():
			wg.Wait()
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

// executeAndReport handles a single job: lease renewal, execution, and result/failure reporting.
func executeAndReport(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	executor *runnerexec.Executor,
	job api.Job,
) {
	liveJob := newLeasedJob(job)
	jobCtx, cancelExecution := context.WithCancel(ctx)
	renewDone := startLeaseRenewal(jobCtx, cfg, client, liveJob)

	reportProgress(jobCtx, cfg, client, liveJob.snapshot(), 5, "runner accepted lease", "runner execution started")

	result, execErr := executor.RunJob(jobCtx, job.JobID, job.Kind, job.Payload, job.LeaseSeconds)
	cancelExecution()
	<-renewDone

	reportCtx, cancelReporting := reportingContext(ctx)
	defer cancelReporting()

	if execErr != nil {
		reportFailure(reportCtx, cfg, client, liveJob.snapshot(), execErr)
		return
	}

	reportProgress(reportCtx, cfg, client, liveJob.snapshot(), 100, "runner finished execution", "runner execution finished")
	reportResult(reportCtx, cfg, client, liveJob.snapshot(), result)
}

// startLeaseRenewal spawns a goroutine that renews the job lease periodically.
// Returns a channel that is closed when the goroutine exits.
func startLeaseRenewal(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job *leasedJob,
) <-chan struct{} {
	return startLeaseRenewalWithMinInterval(ctx, cfg, client, job, defaultMinLeaseRenewal)
}

func startLeaseRenewalWithMinInterval(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job *leasedJob,
	minInterval time.Duration,
) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		jobSnapshot := job.snapshot()
		renewEvery := leaseRenewalIntervalWithMinInterval(jobSnapshot.LeaseSeconds, minInterval)
		maxBackoff := maxLeaseRenewalBackoff(renewEvery, jobSnapshot.LeaseSeconds)
		ticker := time.NewTicker(renewEvery)
		defer ticker.Stop()

		const maxConsecutiveFailures = 3
		consecutiveFailures := 0
		backoffDuration := time.Second

		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				jobSnapshot = job.snapshot()
				renewedJob, err := client.RenewLease(ctx, jobSnapshot.JobID, api.JobRenewRequest{
					TenantID:      cfg.TenantID,
					NodeID:        cfg.NodeID,
					LeaseToken:    jobSnapshot.LeaseToken,
					Attempt:       jobSnapshot.Attempt,
					ExtendSeconds: jobSnapshot.LeaseSeconds,
					Log:           "runner lease keepalive",
				})
				if err != nil {
					consecutiveFailures++
					log.Printf(
						"renew lease failed (attempt %d/%d) job=%s token=%s: %v",
						consecutiveFailures,
						maxConsecutiveFailures,
						jobSnapshot.JobID,
						jobSnapshot.LeaseToken,
						err,
					)
					if consecutiveFailures >= maxConsecutiveFailures {
						log.Printf("lease renewal failed %d times, abandoning job %s", maxConsecutiveFailures, jobSnapshot.JobID)
						return
					}
					if !waitForDuration(ctx, backoffDuration) {
						return
					}
					backoffDuration = minDuration(backoffDuration*2, maxBackoff)
				} else {
					job.applyRenewedLease(renewedJob)
					consecutiveFailures = 0
					backoffDuration = time.Second
				}
			}
		}
	}()
	return done
}

// reportProgress sends a progress update; logs but does not fail the job on error.
func reportProgress(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job api.Job,
	progress int,
	message string,
	logMsg string,
) {
	if err := client.SendProgress(ctx, job.JobID, api.JobProgressRequest{
		TenantID:   cfg.TenantID,
		NodeID:     cfg.NodeID,
		LeaseToken: job.LeaseToken,
		Attempt:    job.Attempt,
		Progress:   progress,
		Message:    message,
		Log:        logMsg,
	}); err != nil {
		log.Printf("send progress failed: %v", err)
	}
}

// reportFailure classifies the execution error and sends a structured failure report.
func reportFailure(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job api.Job,
	execErr error,
) {
	var category *string
	var details map[string]any

	var execError *runnerexec.ExecError
	if errors.As(execErr, &execError) {
		cat := execError.Category
		category = &cat
		details = execError.Details
	}

	if err := client.SendFailure(ctx, job.JobID, api.JobFailRequest{
		TenantID:        cfg.TenantID,
		NodeID:          cfg.NodeID,
		LeaseToken:      job.LeaseToken,
		Attempt:         job.Attempt,
		Error:           execErr.Error(),
		FailureCategory: category,
		ErrorDetails:    details,
		Log:             "runner execution failed",
	}); err != nil {
		log.Printf("send failure failed: %v", err)
	}
}

// reportResult sends the successful execution result to the gateway.
func reportResult(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job api.Job,
	result runnerexec.Result,
) {
	if err := client.SendResult(ctx, job.JobID, api.JobResultRequest{
		TenantID:   cfg.TenantID,
		NodeID:     cfg.NodeID,
		LeaseToken: job.LeaseToken,
		Attempt:    job.Attempt,
		Result: map[string]any{
			"summary": result.Summary,
			"output":  result.Output,
		},
		Log: "runner execution completed",
	}); err != nil {
		log.Printf("send result failed: %v", err)
	}
}

// ── Helpers ─────────────────────────────────────────────────────────

func max(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func minDuration(a time.Duration, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}

func maxDuration(a time.Duration, b time.Duration) time.Duration {
	if a > b {
		return a
	}
	return b
}

func waitForDuration(ctx context.Context, d time.Duration) bool {
	if d <= 0 {
		return true
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func reportingContext(parent context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.WithoutCancel(parent), reportingTimeout)
}

func leaseRenewalInterval(leaseSeconds int) time.Duration {
	return leaseRenewalIntervalWithMinInterval(leaseSeconds, defaultMinLeaseRenewal)
}

func leaseRenewalIntervalWithMinInterval(leaseSeconds int, minInterval time.Duration) time.Duration {
	halfLease := time.Duration(max(1, leaseSeconds/2)) * time.Second
	return maxDuration(minInterval, halfLease)
}

func maxLeaseRenewalBackoff(renewEvery time.Duration, leaseSeconds int) time.Duration {
	maxBackoff := minDuration(renewEvery-time.Second, 30*time.Second)
	if leaseSeconds > 0 {
		leaseCap := time.Duration(max(1, leaseSeconds-1)) * time.Second
		maxBackoff = minDuration(maxBackoff, leaseCap)
	}
	if maxBackoff < time.Second {
		return time.Second
	}
	return maxBackoff
}
