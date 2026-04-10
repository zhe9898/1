# 统一身份与控制面网关内核 - 优化建议

**定位**: Gateway Kernel = 统一身份 + 控制面
**当前成熟度**: 70% (基础网关内核)
**目标成熟度**: 100% (完整网关内核)

---

## 核心差距分析

### 当前状态 (70%)
- ✅ 基础认证 (WebAuthn, PIN, Password, Node Token)
- ✅ 基础授权 (Role-based: user/admin/superadmin)
- ✅ 基础控制面 (节点、任务、连接器管理)
- ✅ 租户隔离 (RLS 强制隔离 13 张表)
- ✅ 身份统一 (Gateway Identity 唯一认证中心)

### 目标状态 (100%)
- ✅ 完整身份管理 (生命周期、细粒度权限、审计)
- ✅ 完整控制面 (审批、配额、告警、编排)
- ✅ 清晰内核边界 (能力注册、Pack 契约、API 版本化)

---

## 优化建议（按优先级）

### P0 - 必须补齐（影响内核定位）

#### 1. 审计日志系统 ⭐⭐⭐⭐⭐

**问题**: 无法追踪"谁在何时对什么资源做了什么"

**影响**:
- 安全合规缺失
- 无法追溯问题
- 无法审计权限变更

**解决方案**:
```python
class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # login, create_job, update_node
    resource_type: Mapped[str] = mapped_column(String(64), index=True)  # user, job, node
    resource_id: Mapped[str | None] = mapped_column(String(128))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    result: Mapped[str] = mapped_column(String(32))  # success/failure
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)

# API
GET /api/v1/audit-logs?user_id=xxx&action=login&start_date=xxx
```

**工作量**: 3-5 天

---

#### 2. 用户状态管理 ⭐⭐⭐⭐⭐

**问题**: 无法禁用/删除用户，无法管理用户生命周期

**影响**:
- 离职员工账号无法禁用
- 恶意用户无法封禁
- 无法实现账号冻结

**解决方案**:
```python
class User(Base):
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    # active/suspended/deleted
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime)
    suspended_by: Mapped[str | None] = mapped_column(String(128))
    suspended_reason: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

# API
POST /api/v1/users/{id}/suspend
POST /api/v1/users/{id}/activate
DELETE /api/v1/users/{id}  # 软删除

# 中间件
async def get_current_user(...):
    user = await get_user_from_jwt(...)
    if user.status != "active":
        raise zen("ZEN-AUTH-403", "User account is suspended")
    ...
```

**工作量**: 2-3 天

---

#### 3. 细粒度权限模型 (Scopes) ⭐⭐⭐⭐⭐

**问题**: 只有角色授权，无法实现资源级权限控制

**影响**:
- 无法实现"只读用户"
- 无法实现"只能管理自己的任务"
- 业务服务无法基于权限执行授权

**解决方案**:
```python
class Permission(Base):
    __tablename__ = "permissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    scope: Mapped[str] = mapped_column(String(128), index=True)
    # Scopes: read:jobs, write:jobs, delete:jobs, admin:jobs
    #         read:nodes, write:nodes, admin:nodes
    #         read:connectors, write:connectors, admin:connectors
    resource_type: Mapped[str | None] = mapped_column(String(64))
    resource_id: Mapped[str | None] = mapped_column(String(128))
    granted_by: Mapped[str] = mapped_column(String(128))
    granted_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)

# JWT 增强
{
  "sub": "user-uuid",
  "username": "alice",
  "role": "user",
  "tenant_id": "default",
  "scopes": ["read:jobs", "write:jobs", "read:nodes"],  # 新增
  "session_id": "session-uuid",  # 新增
  "exp": 1234567890
}

# 业务服务使用
from backend.control_plane.adapters.deps import require_scope

@router.post("/jobs")
async def create_job(
    current_user: dict = Depends(require_scope("write:jobs")),
):
    ...
```

**工作量**: 5-7 天

---

### P1 - 强烈建议（提升内核能力）

#### 4. 会话管理 ⭐⭐⭐⭐

**问题**: 用户无法查看/管理自己的活跃会话

**影响**:
- 无法踢出可疑会话
- 无法限制并发登录
- 无法查看登录设备

**解决方案**:
```python
class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    device: Mapped[str | None] = mapped_column(String(255))
    ip_address: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)

# API
GET /api/v1/sessions  # 查看我的会话
DELETE /api/v1/sessions/{id}  # 踢出会话
```

**工作量**: 3-4 天

---

#### 5. 节点审批流程 ⭐⭐⭐⭐

**问题**: 节点注册后立即激活，无审批流程

**影响**:
- 恶意节点可以直接注册
- 无法控制节点准入
- 无法实现节点白名单

**解决方案**:
```python
class Node(Base):
    enrollment_status: str  # pending → approved → active → revoked
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime)
    approval_reason: Mapped[str | None] = mapped_column(String(255))

# API
POST /api/v1/nodes/{id}/approve
POST /api/v1/nodes/{id}/reject
GET /api/v1/nodes?enrollment_status=pending
```

**工作量**: 2-3 天

---

#### 6. 资源配额系统 ⭐⭐⭐⭐

**问题**: 租户可以无限创建节点/任务，无配额限制

**影响**:
- 资源滥用
- 无法实现多租户公平性
- 无法实现商业化计费

**解决方案**:
```python
class Quota(Base):
    __tablename__ = "quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    max_nodes: Mapped[int] = mapped_column(Integer, default=10)
    max_jobs_per_hour: Mapped[int] = mapped_column(Integer, default=1000)
    max_connectors: Mapped[int] = mapped_column(Integer, default=50)
    used_nodes: Mapped[int] = mapped_column(Integer, default=0)
    used_jobs_this_hour: Mapped[int] = mapped_column(Integer, default=0)
    used_connectors: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

# 中间件
async def check_quota(tenant_id: str, resource_type: str):
    quota = await get_quota(tenant_id)
    if quota.used_nodes >= quota.max_nodes:
        raise zen("ZEN-QUOTA-4290", "Node quota exceeded")
```

**工作量**: 3-4 天

---

#### 7. 监控告警 ⭐⭐⭐

**问题**: 无告警系统，节点离线/任务失败无通知

**影响**:
- 故障无法及时发现
- 无法主动运维
- 依赖人工巡检

**解决方案**:
```python
class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(128))
    condition: Mapped[dict] = mapped_column(JSON)
    # {"metric": "node_offline", "threshold": 1, "duration": 300}
    action: Mapped[dict] = mapped_column(JSON)
    # {"type": "webhook", "url": "https://..."}
    enabled: Mapped[bool] = mapped_column(default=True)

class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_id: Mapped[int] = mapped_column(Integer, index=True)
    severity: Mapped[str] = mapped_column(String(32))  # info/warning/error/critical
    message: Mapped[str] = mapped_column(Text)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
```

**工作量**: 5-7 天

---

### P2 - 长期优化（增强扩展性）

#### 8. 任务编排 (DAG) ⭐⭐⭐

**问题**: 只能创建单个任务，无法编排多步骤工作流

**工作量**: 10-15 天

#### 9. 身份联邦 (OAuth2/OIDC) ⭐⭐

**问题**: 无法集成第三方身份提供商

**工作量**: 10-15 天

#### 10. 内核能力注册表 ⭐⭐

**问题**: 内核能力不可发现

**工作量**: 3-5 天

---

## 实施路线图

### 第一阶段 (2 周) - 身份体系增强
- [ ] 审计日志系统 (3-5 天)
- [ ] 用户状态管理 (2-3 天)
- [ ] 细粒度权限模型 (5-7 天)

### 第二阶段 (2 周) - 控制面增强
- [ ] 会话管理 (3-4 天)
- [ ] 节点审批流程 (2-3 天)
- [ ] 资源配额系统 (3-4 天)
- [ ] 监控告警 (5-7 天)

### 第三阶段 (按需) - 长期优化
- [ ] 任务编排 (DAG)
- [ ] 身份联邦 (OAuth2/OIDC)
- [ ] 内核能力注册表

---

## 总结

**关键差距** (P0):
1. 审计日志 - 安全合规必需
2. 用户状态管理 - 生命周期管理必需
3. 细粒度权限 - 资源级授权必需

**强烈建议** (P1):
4. 会话管理 - 安全性提升
5. 节点审批 - 准入控制
6. 资源配额 - 多租户公平性
7. 监控告警 - 主动运维

**建议**: 优先实施 P0 项目（3 项，约 2 周），使网关内核达到生产级标准。
