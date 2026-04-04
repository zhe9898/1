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

// Loop pulls and executes jobs until ctx is cancelled.
// Jobs are executed concurrently up to cfg.MaxConcurrency using a worker pool.
func Loop(ctx context.Context, cfg config.Config, client *api.Client, executor *runnerexec.Executor) error {
	ticker := time.NewTicker(cfg.PullInterval)
	defer ticker.Stop()

	concurrency := max(1, cfg.MaxConcurrency)
	sem := make(chan struct{}, concurrency)
	var wg sync.WaitGroup

	for {
		jobs, err := client.PullJobs(ctx, api.PullRequest{
			TenantID:      cfg.TenantID,
			NodeID:        cfg.NodeID,
			Limit:         concurrency,
			AcceptedKinds: cfg.EffectiveAcceptedKinds(),
		})
		if err != nil {
			log.Printf("job pull failed: %v", err)
		}

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
	jobCtx, cancel := context.WithCancel(ctx)
	renewDone := startLeaseRenewal(jobCtx, cfg, client, job)

	reportProgress(jobCtx, cfg, client, job, 5, "runner accepted lease", "runner execution started")

	result, execErr := executor.RunJob(jobCtx, job.JobID, job.Kind, job.Payload, job.LeaseSeconds)
	cancel()
	<-renewDone

	if execErr != nil {
		reportFailure(ctx, cfg, client, job, execErr)
		return
	}

	reportProgress(ctx, cfg, client, job, 100, "runner finished execution", "runner execution finished")
	reportResult(ctx, cfg, client, job, result)
}

// startLeaseRenewal spawns a goroutine that renews the job lease periodically.
// Returns a channel that is closed when the goroutine exits.
func startLeaseRenewal(
	ctx context.Context,
	cfg config.Config,
	client *api.Client,
	job api.Job,
) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		renewEvery := time.Duration(max(5, job.LeaseSeconds/2)) * time.Second
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
				if err := client.RenewLease(ctx, job.JobID, api.JobRenewRequest{
					TenantID:      cfg.TenantID,
					NodeID:        cfg.NodeID,
					LeaseToken:    job.LeaseToken,
					Attempt:       job.Attempt,
					ExtendSeconds: job.LeaseSeconds,
					Log:           "runner lease keepalive",
				}); err != nil {
					consecutiveFailures++
					log.Printf("renew lease failed (attempt %d/%d): %v", consecutiveFailures, maxConsecutiveFailures, err)
					if consecutiveFailures >= maxConsecutiveFailures {
						log.Printf("lease renewal failed %d times, abandoning job %s", maxConsecutiveFailures, job.JobID)
						return
					}
					time.Sleep(backoffDuration)
					backoffDuration = minDuration(backoffDuration*2, 30*time.Second)
				} else {
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
