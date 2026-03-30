FROM golang:1.24-alpine@sha256:8bee1901f1e530bfb4a7850aa7a479d17ae3a18beb6e09064ed54cfd245b7191 AS builder
WORKDIR /src

COPY runner-agent/go.mod ./runner-agent/go.mod
COPY runner-agent/cmd ./runner-agent/cmd
COPY runner-agent/internal ./runner-agent/internal

WORKDIR /src/runner-agent
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/runner-agent ./cmd/runner-agent

FROM alpine:3.20@sha256:a4f4213abb84c497377b8544c81b3564f313746700372ec4fe84653e4fb03805 AS runtime
RUN addgroup -S app && adduser -S -G app app
USER app
WORKDIR /app

COPY --from=builder /out/runner-agent /usr/local/bin/runner-agent

ENTRYPOINT ["/usr/local/bin/runner-agent"]
