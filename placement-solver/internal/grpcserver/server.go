// Package grpcserver wires the PlacementSolver gRPC service.
package grpcserver

import (
	"context"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	pb "zen70/placement-solver/gen/placement"
	"zen70/placement-solver/internal/solver"
)

// Server implements pb.PlacementSolverServer.
type Server struct {
	pb.UnimplementedPlacementSolverServer
}

func (s *Server) Solve(_ context.Context, req *pb.SolveRequest) (*pb.SolveResponse, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "nil request")
	}

	start := time.Now()
	result := solver.Solve(req)
	elapsed := time.Since(start)

	return &pb.SolveResponse{
		Assignments:   result.Assignments,
		FeasiblePairs: result.FeasiblePairs,
		Result:        result.Result,
		ElapsedUs:     elapsed.Microseconds(),
	}, nil
}

func (s *Server) Health(_ context.Context, _ *pb.HealthRequest) (*pb.HealthResponse, error) {
	return &pb.HealthResponse{Status: "ok"}, nil
}
