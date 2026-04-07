package main

import (
	"context"
	"log"
	"os/signal"
	"syscall"

	"zen70/runner-agent/internal/config"
	"zen70/runner-agent/internal/service"
)

func main() {
	cfg := config.Load()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	svc := service.New(cfg)
	if err := svc.Run(ctx); err != nil {
		log.Fatalf("runner-agent stopped: %v", err)
	}
}
