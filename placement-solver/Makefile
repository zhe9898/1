# placement-solver Makefile
#
# Requires: Go ≥ 1.24, protoc, protoc-gen-go, protoc-gen-go-grpc
# Install plugins: go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
#                  go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest

GOPATH   := $(shell go env GOPATH)
PROTO_SRC := proto/placement.proto
GEN_DIR   := gen/placement

.PHONY: all proto build test bench run clean

all: proto build test

proto:
	@echo "Generating Go gRPC stubs…"
	protoc \
		--plugin=protoc-gen-go=$(GOPATH)/bin/protoc-gen-go \
		--plugin=protoc-gen-go-grpc=$(GOPATH)/bin/protoc-gen-go-grpc \
		--go_out=gen --go_opt=paths=source_relative \
		--go-grpc_out=gen --go-grpc_opt=paths=source_relative \
		--proto_path=proto \
		$(PROTO_SRC)
	@mv gen/placement.pb.go gen/placement/ 2>/dev/null || true
	@mv gen/placement_grpc.pb.go gen/placement/ 2>/dev/null || true
	@echo "Generating Python gRPC stubs…"
	python -m grpc_tools.protoc \
		-I proto \
		--python_out=../backend/core/gen_grpc \
		--grpc_python_out=../backend/core/gen_grpc \
		$(PROTO_SRC)
	@# Fix relative import in generated Python stub
	@sed -i 's/^import placement_pb2 as placement__pb2$$/from backend.core.gen_grpc import placement_pb2 as placement__pb2/' \
		../backend/core/gen_grpc/placement_pb2_grpc.py

build:
	go build ./...

test:
	go test ./... -count=1

bench:
	go test ./internal/solver/ -bench=. -benchtime=5s -benchmem

run:
	go run ./cmd/server/

clean:
	rm -f $(GEN_DIR)/placement.pb.go $(GEN_DIR)/placement_grpc.pb.go
