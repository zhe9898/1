// PlacementSolver gRPC server.
//
// Usage:
//
//	placement-solver [--addr :50055] [--max-recv-mb 16]
//
// Environment variables (override flags):
//
//	PLACEMENT_SOLVER_ADDR      – listen address (default :50055)
//	PLACEMENT_SOLVER_MAX_RECV  – max gRPC recv message MB (default 16)
package main

import (
	"flag"
	"fmt"
	"log/slog"
	"net"
	"os"
	"os/signal"
	"syscall"

	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"

	pb "zen70/placement-solver/gen/placement"
	"zen70/placement-solver/internal/grpcserver"
)

func main() {
	addr := flag.String("addr", ":50055", "gRPC listen address")
	maxRecvMB := flag.Int("max-recv-mb", 16, "max gRPC receive message size in MB")
	flag.Parse()

	if v := os.Getenv("PLACEMENT_SOLVER_ADDR"); v != "" {
		*addr = v
	}

	lis, err := net.Listen("tcp", *addr)
	if err != nil {
		slog.Error("failed to listen", "addr", *addr, "err", err)
		os.Exit(1)
	}

	maxRecvBytes := *maxRecvMB * 1024 * 1024
	srv := grpc.NewServer(
		grpc.MaxRecvMsgSize(maxRecvBytes),
		grpc.MaxSendMsgSize(maxRecvBytes),
	)
	pb.RegisterPlacementSolverServer(srv, &grpcserver.Server{})
	reflection.Register(srv)

	slog.Info("placement-solver listening", "addr", *addr)

	// Graceful shutdown on SIGINT / SIGTERM
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-quit
		slog.Info("shutting down placement-solver")
		srv.GracefulStop()
	}()

	if err := srv.Serve(lis); err != nil {
		fmt.Fprintf(os.Stderr, "serve error: %v\n", err)
		os.Exit(1)
	}
}
