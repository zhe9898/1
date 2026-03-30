# ADR 0002：探针通过 Docker CLI 与 DOCKER_HOST 寻址 Docker

**状态**：通过

**日期**：2025-03-14

## 背景

.cursorrules 规定「所有业务容器与探针调用 Docker 时，必须通过读取 DOCKER_HOST 环境变量进行 TCP 寻址；禁止直接挂载 sock」。

## 决策

探针通过 **subprocess 调用 `docker` CLI**，不挂载 Docker Socket。Docker 寻址由 **DOCKER_HOST** 环境变量控制。

## 理由

1. **符合寻址规则**：`docker` 命令会自动读取 `DOCKER_HOST`，无需在代码中显式处理。
2. **环境继承**：`subprocess.run(["docker", "pause", ...])` 继承进程环境，容器编排中设置 `DOCKER_HOST=tcp://...` 即可生效。
3. **实现简单**：无需引入 Docker SDK，保持探针依赖精简。
4. **与规范一致**：禁止挂载 `/var/run/docker.sock`，强制通过 DOCKER_HOST 使用 TCP 或 Unix 套接字。

## 备选方案

- **Docker SDK（docker-py）**：需额外依赖，增加探针包体积；当前 CLI 已满足需求，不采纳。
- **直接挂载 sock**：违反红线，不采纳。

## 后果

- 编排中探针服务必须配置 `DOCKER_HOST`（或依赖宿主机默认）。
- 探针文档需说明：通过环境变量配置 Docker 寻址，禁止直接挂载 Docker Socket。
