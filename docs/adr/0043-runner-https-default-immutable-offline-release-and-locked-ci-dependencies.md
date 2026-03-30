# ADR 0043: Runner HTTPS 默认值、不可变离线发行与锁定式 CI 依赖

## 状态
已采纳

## 背景
- `runner-agent` 过去默认使用 `http://127.0.0.1:8000`，并在每次机器调用时发送 `Authorization: Bearer <node_token>`。这在跨主机或误配到公网时会形成明文 Bearer 泄露面。
- `build_offline_v2_9.yml` 过去复用固定 `RELEASE_TAG=v2.9.1` 承载持续构建，导致 release 语义从“冻结版本”退化成“不断追加资产的容器”。
- CI 工作流过去直接 `pip install -r ...`，并升级 `pip`，会让同一提交在不同时间解析到不同 Python 依赖树。
- `scripts/bootstrap.py` 过去即使存在 `package-lock.json` 也使用 `npm install`，对前端依赖可复现性不利。

## 决策
1. `runner-agent` 默认网关地址改为 `https://127.0.0.1:8000`。
2. 非 loopback 网关地址强制要求 HTTPS；loopback 明文 HTTP 仅允许通过 `RUNNER_ALLOW_INSECURE_HTTP=true` 显式放行，且仅用于本机开发联调。
3. `runner-agent` 支持：
   - `GATEWAY_CA_FILE` 自定义 CA 文件
   - `GATEWAY_CERT_SHA256` 证书 SHA256 指纹 pin
   - 启动时校验 CA 文件为有效 PEM，指纹格式合法
4. 节点 bootstrap 回执明确提示：机器通道默认要求 HTTPS，本机开发才允许显式放开 HTTP。
5. 离线包发布改为“每次构建按 commit SHA 生成不可变 release tag”，固定版本号仅作为系列标识，不再作为反复追加资产的正式发行 tag。
6. 离线包上传跳过逻辑必须同时验证 ZIP 与 `.sha256` 资产都已存在；缺任一资产都不能跳过。
7. Python CI 与 Compliance 工作流统一改为安装 `backend/requirements-ci.lock`，并强制 `--require-hashes`。
8. Python CI 主版本统一到 `3.12`，与锁文件生成环境保持一致。
9. 本地引导脚本在存在 `package-lock.json` 时改用 `npm ci`；兼容包装层必须保留失败返回码与 stderr 可观测性。

## 影响

### 正向
- 默认机器通道不再把明文 Bearer 当成正常路径。
- 离线发行可以按 SHA 回溯，不再出现“同一 release tag 下资产不断漂移”的审计歧义。
- CI Python 依赖树由锁文件冻结，减少同 commit 不同时间的依赖解析漂移。
- 前端本地引导更接近 CI 依赖语义。

### 代价
- 使用本机明文 HTTP 调试 runner 时，需要显式设置 `RUNNER_ALLOW_INSECURE_HTTP=true`。
- 新增 `requirements-ci.lock` 后，Python 依赖升级需要同步更新锁文件。
- 离线发行数量会随 commit 增长，需要按系列或保留策略治理 release 数量。

## 落地文件
- `runner-agent/internal/config/config.go`
- `runner-agent/internal/api/client.go`
- `runner-agent/internal/service/service.go`
- `backend/api/nodes.py`
- `.github/workflows/build_offline_v2_9.yml`
- `.github/workflows/ci.yml`
- `.github/workflows/compliance.yml`
- `backend/requirements-ci.in`
- `backend/requirements-ci.lock`
- `scripts/bootstrap.py`
- `deploy/bootstrap.py`
- `tests/test_repo_hardening.py`

## 验证
- `go test ./...`
- `python -m pytest backend/tests/unit/test_auth_pin_lockout.py backend/tests/unit/test_control_plane_protocol_contracts.py tests/test_repo_hardening.py -q`
- `python -m pytest backend/tests/unit -q`
- `python -m pytest tests/test_compliance_sre.py tests/test_repo_hardening.py -q`
- `python scripts/compiler.py system.yaml -o . --dry-run`
