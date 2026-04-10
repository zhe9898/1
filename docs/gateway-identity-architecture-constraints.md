# Gateway Identity 架构约束

**约束级别**: MUST (强制)
**适用范围**: 所有业务能力、业务服务、Pack 扩展

---

## 核心原则

**所有业务能力都必须挂接在 Gateway Identity 之下。**

1. **业务域不得自行建立独立主认证体系**
2. **业务服务只消费 Gateway 下发的身份声明、租户上下文和授权范围**
3. **业务服务在本域内执行资源级授权**

---

## 架构约束

### 1. 唯一认证中心

**Gateway 是唯一认证中心**，负责：
- 用户身份验证（WebAuthn, PIN, Password）
- JWT Token 签发
- Token 刷新和撤销
- 身份声明下发

**禁止行为**:
- ❌ 业务服务自建用户表
- ❌ 业务服务自建认证接口
- ❌ 业务服务自签发 Token
- ❌ 业务服务绕过 Gateway 直接验证密码

### 2. 身份声明传递

**Gateway 下发的身份声明** (JWT Payload):
```json
{
  "sub": "user-uuid",
  "username": "alice",
  "role": "user|admin|superadmin",
  "tenant_id": "default",
  "ai_route_preference": "auto",
  "exp": 1234567890,
  "iat": 1234567890,
  "jti": "token-uuid"
}
```

**业务服务消费方式**:
```python
from backend.control_plane.adapters.deps import get_current_user

async def my_business_endpoint(
    current_user: dict[str, str] = Depends(get_current_user),
):
    user_id = current_user["sub"]
    username = current_user["username"]
    role = current_user["role"]
    tenant_id = current_user["tenant_id"]

    # 基于身份声明执行业务逻辑
    ...
```

**禁止行为**:
- ❌ 业务服务自行解析 JWT
- ❌ 业务服务自行验证 JWT 签名
- ❌ 业务服务绕过 `get_current_user()` 依赖

### 3. 租户上下文传递

**Gateway 负责租户隔离**:
- 从 JWT 提取 `tenant_id`
- 通过 `get_tenant_db()` 设置 RLS 上下文
- 数据库级别强制隔离

**业务服务消费方式**:
```python
from backend.control_plane.adapters.deps import get_tenant_db

async def my_business_endpoint(
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # db 已自动绑定租户上下文
    # 所有查询自动过滤 tenant_id
    result = await db.execute(select(MyModel))
    ...
```

**禁止行为**:
- ❌ 业务服务自行管理租户上下文
- ❌ 业务服务绕过 `get_tenant_db()` 依赖
- ❌ 业务服务手动拼接 `tenant_id` 过滤条件

### 4. 授权范围传递

**当前实现**: 基于角色的授权 (RBAC)
- `role: "user"` - 普通用户
- `role: "admin"` - 管理员
- `role: "superadmin"` - 超级管理员

**业务服务消费方式**:
```python
from backend.control_plane.adapters.deps import get_current_admin

async def admin_only_endpoint(
    current_user: dict[str, str] = Depends(get_current_admin),
):
    # 自动验证 admin 权限
    # 非 admin 返回 403
    ...
```

**资源级授权** (业务服务职责):
```python
async def update_resource(
    resource_id: str,
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # 1. Gateway 已验证身份和租户
    # 2. 业务服务验证资源所有权
    resource = await db.get(Resource, resource_id)
    if resource.owner_id != current_user["sub"]:
        raise zen("ZEN-AUTH-403", "Not resource owner", status_code=403)

    # 3. 执行业务逻辑
    ...
```

---

## 实现检查清单

### Gateway 侧 (已实现 ✅)

- ✅ JWT 签发 (`backend/core/jwt.py`)
- ✅ JWT 验证 (`backend/control_plane/adapters/deps.py::get_current_user`)
- ✅ 身份声明下发 (sub, username, role, tenant_id)
- ✅ 租户上下文绑定 (`get_tenant_db()`)
- ✅ 角色验证 (`get_current_admin()`, `require_admin_role()`)
- ✅ Token 刷新机制 (自动刷新过半生命周期的 Token)
- ✅ Token 撤销机制 (Redis blacklist)

### 业务服务侧 (需要验证)

- ⚠️ 所有业务端点使用 `Depends(get_current_user)`
- ⚠️ 所有业务端点使用 `Depends(get_tenant_db)`
- ⚠️ 没有业务服务自建认证接口
- ⚠️ 没有业务服务自行解析 JWT
- ⚠️ 资源级授权在业务服务内部实现

---

## 扩展指南

### 新增业务 Pack

**正确做法** ✅:
```python
# backend/control_plane/adapters/my_business.py
from backend.control_plane.adapters.deps import get_current_user, get_tenant_db

router = APIRouter(prefix="/api/v1/my-business")

@router.get("/resources")
async def list_resources(
    current_user: dict[str, str] = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
):
    # 1. Gateway 已验证身份
    # 2. Gateway 已绑定租户上下文
    # 3. 直接执行业务逻辑
    result = await db.execute(select(MyResource))
    return result.scalars().all()
```

**错误做法** ❌:
```python
# ❌ 错误：自建认证接口
@router.post("/login")
async def my_business_login(username: str, password: str):
    # 违反约束：业务域不得自建认证体系
    ...

# ❌ 错误：自行解析 JWT
@router.get("/resources")
async def list_resources(authorization: str = Header()):
    token = authorization.replace("Bearer ", "")
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])
    # 违反约束：应该使用 get_current_user()
    ...

# ❌ 错误：自行管理租户上下文
@router.get("/resources")
async def list_resources(
    tenant_id: str,
    db: AsyncSession = Depends(get_db),
):
    await db.execute(text("SET LOCAL zen70.current_tenant = :tenant"), {"tenant": tenant_id})
    # 违反约束：应该使用 get_tenant_db()
    ...
```

### 新增 Connector Kind

**Connector 不需要认证**，因为它们是系统内部调用：
```python
# backend/control_plane/adapters/connectors.py
@router.post("/{id}/invoke")
async def invoke_connector(
    id: str,
    payload: ConnectorInvokeRequest,
    current_user: dict[str, str] = Depends(get_current_user),  # ✅ 仍需验证调用者身份
    db: AsyncSession = Depends(get_tenant_db),
):
    # Connector 配置已绑定租户
    # Connector 调用不需要额外认证
    ...
```

### 新增 Job Kind

**Job 执行不需要认证**，因为它们是异步任务：
```python
# runner-agent 执行 Job
# 不需要 JWT，使用 node_token 认证
# Job payload 中可以包含 created_by 字段记录创建者
```

---

## 违规检测

### 自动检测规则

1. **禁止自建认证接口**:
   - 检查路由中是否有 `/login`, `/register`, `/auth` 等端点
   - 检查是否有密码验证逻辑

2. **禁止自行解析 JWT**:
   - 检查代码中是否有 `jwt.decode()` 调用
   - 检查是否有 `Authorization` header 手动解析

3. **禁止绕过依赖注入**:
   - 检查业务端点是否都使用 `Depends(get_current_user)`
   - 检查业务端点是否都使用 `Depends(get_tenant_db)`

### 人工审查清单

- [ ] 所有业务路由都在 `backend/control_plane/adapters/` 下
- [ ] 所有业务路由都使用 `get_current_user` 依赖
- [ ] 所有业务路由都使用 `get_tenant_db` 依赖
- [ ] 没有业务服务自建 User 表
- [ ] 没有业务服务自建认证接口
- [ ] 资源级授权在业务服务内部实现

---

## 安全考虑

### JWT Secret 管理

- ✅ 生产环境强制配置 `JWT_SECRET_CURRENT`
- ✅ 支持 Secret 轮换 (`JWT_SECRET_PREVIOUS`)
- ✅ Secret 最小长度 32 字节
- ✅ 开发环境使用默认 Secret（仅限开发）

### Token 生命周期

- ✅ 默认 15 分钟过期
- ✅ 自动刷新（过半生命周期）
- ✅ 支持撤销（Redis blacklist）
- ✅ 支持 JTI 去重

### 租户隔离

- ✅ RLS 强制隔离（13 张表）
- ✅ 生产环境强制启用 RLS
- ✅ 租户上下文自动绑定
- ✅ 数据库级别防止跨租户访问

---

## 总结

**Gateway Identity 是唯一认证中心**，所有业务能力必须：
1. 使用 `get_current_user()` 获取身份声明
2. 使用 `get_tenant_db()` 获取租户上下文
3. 在业务服务内部执行资源级授权
4. 不得自建认证体系
5. 不得绕过 Gateway 依赖注入

**违反约束的代码将被拒绝合并。**
