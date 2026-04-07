# ADR 0006: 网关与底层物理探针彻底解耦 (Gateway Decoupling via Pub/Sub)

- Status: Accepted
- Date: 2026-03-17
- Scope: 网关与底层物理探针彻底解耦 (Gateway Decoupling via Pub/Sub)

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 2. 决策选项
1. **方案 A**: 维持原样，继续在 FastAPI 网关内依赖 Docker Socket Proxy 发起控制。
2. **方案 B**: 采用隔离的消息队列（RabbitMQ / Kafka）进行异步解耦。
3. **方案 C**: 利用架构现有的 **Redis Pub/Sub** 原语作为事件总线分发控制指令。

## 3. 评估对比
### 方案 A (维持现状)
- **优势**: 无需改动代码。
- **劣势**: 违反 Zero-Trust 原则，网关一旦失陷即可控制全部底层容器。

### 方案 B (RabbitMQ/Kafka)
- **优势**: 专业的消息队列解耦，保证 QoS。
- **劣势**: 对于单节点家庭信标集群过于臃肿，违背法典 3.1（严禁引入非必要重型中间件）的轻量级诉求。

### 方案 C (Redis Pub/Sub)
- **优势**: 架构原生已包含高可用 Redis。完全复用了缓存和状态机基础设施。实现简单、实时性极高，真正做到了隔离。网关只需发广播，物理探针作为订阅者在外部监听执行。
- **劣势**: Pub/Sub 没有离线消息重放能力。但在软开关实时控制场景中，这并不是缺陷，前端超时会兜底。

## 4. 最终决定
采用 **方案 C**，正式在 Redis 中启用 `switch:events` 通道。
网关取消对 `docker.sock` 代理的任何直连和子进程调用权限。所有手动触发命令下发为 JSON 事件流交由 Redis。底层探针拉起守护线程并使用强制 `timeout=3.0` 消费队列，执行物理层面的降级熔断。

## 5. 影响范围
- **安全边界大幅提升**：Gateway 容器剥夺直接调用底层系统的能力，就算爆出 RCE 漏洞，也只能发布消息。
- **异步响应能力提升**：大幅度平滑了手动开关时的请求延迟（非阻塞）。
- **执行端闭环**：配合宿主机级或特权隔离级的探针订阅执行（如 `watchdog.py` 和 `topology_sentinel.py`），它们同样不依靠易失效的外部命令行，而是利用安全的本地 `tcp://docker-proxy:2375` HTTP API 控制 Docker 守护进程。
