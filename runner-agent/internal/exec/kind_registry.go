package runnerexec

import (
	"context"
	"fmt"
)

type kindHandler func(context.Context, map[string]any) (Result, error)

type kindHandlerRegistry struct {
	handlers map[string]kindHandler
}

func newKindHandlerRegistry() *kindHandlerRegistry {
	return &kindHandlerRegistry{handlers: make(map[string]kindHandler)}
}

func (r *kindHandlerRegistry) mustRegister(kind string, handler kindHandler) {
	if kind == "" {
		panic("runnerexec: empty kind registration")
	}
	if handler == nil {
		panic(fmt.Sprintf("runnerexec: nil handler for kind %q", kind))
	}
	if _, exists := r.handlers[kind]; exists {
		panic(fmt.Sprintf("runnerexec: duplicate handler registration for kind %q", kind))
	}
	r.handlers[kind] = handler
}

func (r *kindHandlerRegistry) lookup(kind string) (kindHandler, bool) {
	handler, ok := r.handlers[kind]
	return handler, ok
}

func buildBuiltInKindHandlerRegistry(e *Executor) *kindHandlerRegistry {
	registry := newKindHandlerRegistry()

	registry.mustRegister("noop", runNoop)
	registry.mustRegister("shell.exec", runScript)
	registry.mustRegister("connector.invoke", e.runConnectorInvoke)
	registry.mustRegister("http.request", e.runHTTPRequest)
	registry.mustRegister("script.run", runScript)
	registry.mustRegister("docker.exec", runDockerExec)
	registry.mustRegister("cron.trigger", e.runCronTrigger)
	registry.mustRegister("alert.notify", e.runAlertNotify)
	registry.mustRegister("healthcheck", e.runHealthcheck)
	registry.mustRegister("file.transfer", runFileTransfer)
	registry.mustRegister("container.run", runContainerRun)
	registry.mustRegister("cron.tick", runCronTick)
	registry.mustRegister("data.sync", runDataSync)
	registry.mustRegister("wasm.run", runWasmRun)

	return registry
}

func (e *Executor) dispatch(ctx context.Context, kind string, payload map[string]any) (Result, error) {
	handler, ok := e.kindHandlers.lookup(kind)
	if !ok {
		return Result{}, &ExecError{
			Message:  fmt.Sprintf("unsupported job kind: %s", kind),
			Category: "invalid_payload",
		}
	}
	return handler(ctx, payload)
}
