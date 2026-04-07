# 节点机器鉴权实施矩阵

## 决策

- 方案：`2a`，在 `nodes` 单表承载节点凭证
- 机器鉴权 Header：`Authorization: Bearer <node_token>`
- token 存储：仅存 bcrypt hash，DB 中绝不落明文
- 控制面归属：管理员负责节点凭证的 provision / rotate / revoke

## 数据模型

| 字段 | 位置 | 用途 |
| --- | --- | --- |
| `auth_token_hash` | `backend.models.node.Node` | 保存节点 token 的 bcrypt 哈希 |
| `auth_token_version` | `backend.models.node.Node` | 轮换时单调递增的凭证版本 |
| `enrollment_status` | `backend.models.node.Node` | 节点 enrollment 生命周期：`pending`、`active`、`revoked` |

## API 矩阵

| Endpoint | 鉴权 | 用途 |
| --- | --- | --- |
| `POST /api/v1/nodes` | 管理员 JWT | 创建节点记录并签发一次性 token |
| `POST /api/v1/nodes/{id}/token` | 管理员 JWT | 轮换节点 token、版本递增，并强制重新 enrollment |
| `POST /api/v1/nodes/{id}/revoke` | 管理员 JWT | 吊销节点 token，阻断后续机器流量 |
| `POST /api/v1/nodes/register` | 节点 bearer token | 激活已 provision 节点并上报运行时合同 |
| `POST /api/v1/nodes/heartbeat` | 节点 bearer token | 刷新节点存活状态和运行时事实 |
| `POST /api/v1/jobs/pull` | 节点 bearer token | 只允许 `active` enrollment 节点领取任务 |
| `POST /api/v1/jobs/{id}/result` | 节点 bearer token | 只接受当前 lease owner 的成功终态回调 |
| `POST /api/v1/jobs/{id}/fail` | 节点 bearer token | 只接受当前 lease owner 的失败终态回调 |

## Runner 合同

| 项目 | 内容 |
| --- | --- |
| 必需环境变量 | `RUNNER_NODE_ID`、`NODE_TOKEN` |
| 兼容环境变量 | `ZEN70_NODE_TOKEN` 仍作为别名被接受 |
| Header 规则 | Runner 在所有机器到机器 POST 上统一发送 `Authorization: Bearer <node_token>` |
| 日志规则 | token 绝不允许写入日志或回调 payload |

## Enrollment 状态机

1. 管理员 provision 节点 -> 落一行 `pending` enrollment，并签发一次性 `node_token`
   provision 响应同时返回后端签发的 `bootstrap_commands` 和 `bootstrap_notes`
2. Runner 在本地保存 `RUNNER_NODE_ID` + `NODE_TOKEN` 后启动
3. `POST /api/v1/nodes/register` 携带匹配 token 后，将节点切到 `active`
4. `active` 节点可以 heartbeat、pull jobs、回传结果
5. 管理员 rotate 会把节点退回 `pending/offline`；revoke 会把节点打到 `revoked`

## 上线顺序

1. 先应用 DB schema hardening，补齐节点凭证字段
2. 在暴露公网执行通道前，为所有存量 runner 完成 token provision
3. 部署带机器 token 强校验的 gateway 路由
4. 滚动更新支持 `NODE_TOKEN` 的 `runner-agent`
5. 对所有走过不安全链路的节点凭证执行吊销并重发
