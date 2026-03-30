# ZEN70 私有镜像仓库与离线种子包

本文档说明私有 Docker Registry v2 的部署、镜像同步脚本的使用，以及离线种子包导出与恢复流程，实现供应链自主与“一根 U 盘、一条命令、全量重铸”的离线自救能力。

## 一、私有镜像仓库 (Registry v2)

### 1.1 作用

- 作为所有核心镜像的**单一来源**，切断对 Docker Hub 的直接依赖。
- 内网部署，极寒断网环境下仍可从本地仓库拉取镜像完成部署。

### 1.2 一键启动

```bash
./deploy/registry-setup.sh
```

- 默认容器名：`zen70-registry`，端口：`5000`，数据卷：`zen70-registry_data`。
- 可通过环境变量覆盖：
  - `ZEN70_REGISTRY_CONTAINER`：容器名
  - `ZEN70_REGISTRY_PORT`：宿主机端口
  - `ZEN70_REGISTRY_VOLUME`：数据卷名

### 1.3 验证

```bash
docker ps | grep zen70-registry
curl http://localhost:5000/v2/_catalog
```

- 未同步前返回 `{"repositories":[]}`；同步后列出已推送的镜像名。

### 1.4 安全说明

- 当前为**无认证模式**，仅适合内网或开发环境。
- 生产或对外暴露时，请为 Registry 配置 HTTP 认证或前置 HTTPS/认证代理。

---

## 二、镜像同步 (pull-sync.sh)

### 2.1 镜像列表

- 文件：`deploy/images.list`
- 每行一个镜像（可含仓库与标签），如 `postgres:15-alpine`、`jellyfin/jellyfin:10.8.13`。
- 空行与 `#` 开头行会被忽略。

### 2.2 执行同步

1. 确保私有仓库已启动：`./deploy/registry-setup.sh`
2. 执行同步：

```bash
./deploy/pull-sync.sh
```

- 从 Docker Hub（或镜像原名所在仓库）拉取 `images.list` 中的镜像。
- 打 tag 为 `localhost:5000/<原镜像名>` 并推送到私有仓库。
- 日志追加到 `deploy/pull-sync.log`；任一步骤失败会输出错误并退出。

### 2.3 自定义仓库地址

```bash
export ZEN70_REGISTRY=192.168.1.100:5000
./deploy/pull-sync.sh
```

---

## 三、离线种子包 (export-seed.sh)

### 3.1 作用

- 将**当前 Git 仓库（完整克隆）**与**核心镜像**打包为一个 `.tar.gz` 文件。
- 便于 U 盘携带，在无外网环境下实现“一根 U 盘、一条命令、全量重铸”。

### 3.2 生成种子包

```bash
./deploy/export-seed.sh
```

- 在项目根目录生成 `zen70-seed.tar.gz`。
- 包内结构示例：
  - `zen70-seed/git-repo/`：完整克隆的仓库（含 `config/`、`deploy/` 等）
  - `zen70-seed/images/`：各镜像的 `docker save` 产物（`.tar`）
  - `zen70-seed/images.list`：镜像列表副本
  - `zen70-seed/bootstrap-offline.sh`：离线点火脚本

### 3.3 在无网络环境恢复

1. 将 `zen70-seed.tar.gz` 拷贝到目标机器（如 U 盘）。
2. 解压：
   ```bash
   tar -xzf zen70-seed.tar.gz
   cd zen70-seed
   ```
3. 执行离线点火（需已安装 Docker 与 Python 3）：
   ```bash
   ./bootstrap-offline.sh
   ```
   - 脚本会：加载 `images/` 下所有镜像 → 进入 `git-repo` → 执行 `python3 deploy/bootstrap.py --offline`。
   - 即使用本地 `config/` 与已加载镜像完成部署，不访问外网。

---

## 四、Git 多源与仓库结构

### 4.1 配置仓库建议结构

- `config/`：`system.yaml` 或 `conf.d/*.yml`
- `deploy/`：模板、config-compiler.py、bootstrap.py、registry-setup.sh、pull-sync.sh、export-seed.sh、images.list 等
- `docs/`：架构与运维文档

### 4.2 多源拉取

- `bootstrap.py` 已支持多源 Git 拉取（主源失败后切换备用源）。
- 主/备源在脚本内或通过 `--config-source` 指定；离线时使用 `--offline` 跳过拉取。

---

## 五、完成度自检

| 检查项 | 命令/步骤 |
|--------|------------|
| 私有仓库可用 | `./deploy/registry-setup.sh` 后 `docker ps \| grep zen70-registry`，`curl http://localhost:5000/v2/_catalog` 返回 JSON |
| 镜像同步 | 准备 `deploy/images.list`，执行 `./deploy/pull-sync.sh`，再次 `curl .../_catalog` 可见已同步镜像 |
| 离线种子包 | 执行 `./deploy/export-seed.sh`，得到 `zen70-seed.tar.gz`；解压后含 `git-repo/`、`images/`、`bootstrap-offline.sh` |
| 离线恢复 | 解压种子包后运行 `./bootstrap-offline.sh`（目标机需 Docker + Python），应能完成离线部署 |

所有脚本均包含错误处理，关键步骤失败时会输出错误信息并退出（非零状态码）。
