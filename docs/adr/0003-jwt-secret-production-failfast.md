# ADR 0003：生产环境 JWT 密钥缺失时立即失败

**状态**：通过

**日期**：2025-03-14

## 背景

.cursorrules 规定「绝不在代码里写死 JWT 密钥或数据库密码，必须由点火脚本生成并注入 .env」。

原实现：未配置环境变量时使用默认值 `"change-me-in-production"`，存在生产环境误用风险。

## 决策

- **开发环境**：未配置 `JWT_SECRET_CURRENT`/`JWT_SECRET` 时，允许使用 `"change-me-in-production"` 以便本地调试。
- **生产环境**：当 `ZEN70_ENV=production` 且未配置 JWT 密钥时，**模块加载时抛出 RuntimeError**，禁止启动。

## 理由

1. **安全优先**：生产环境使用默认密钥会导致严重安全隐患。
2. **快速失败**：在启动阶段即发现配置缺失，避免运行时出错。
3. **开发友好**：开发环境无需强制配置，降低本地使用成本。

## 实现

```python
_IS_PROD = os.getenv("ZEN70_ENV", "").lower() == "production"
_CURRENT = os.getenv("JWT_SECRET_CURRENT") or os.getenv("JWT_SECRET") or ("" if _IS_PROD else "change-me-in-production")
if _IS_PROD and not _CURRENT:
    raise RuntimeError("JWT_SECRET_CURRENT or JWT_SECRET must be set in production")
```

## 后果

- 生产部署时必须设置 `ZEN70_ENV=production` 且由点火脚本注入 JWT 密钥。
- 点火脚本需在生成 `.env` 时包含 `JWT_SECRET_CURRENT`。
