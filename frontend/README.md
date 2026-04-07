# ZEN70 前端

Vue 3 + TypeScript + Vite + PWA，协议驱动 UI，能力矩阵与 SSE 实时更新。

## 快速开始

```bash
npm install
npm run dev
```

访问 http://localhost:5173。API 通过 Vite 代理到 `http://localhost:8000`。

## 校验步骤（任务 2.1 检查点）

1. **开发**：`npm run dev` → 打开 http://localhost:5173，Console 无红错，Network 有 `/api/v1/capabilities`。
2. **构建**：`npm run build` → 确认 `dist/manifest.json`、`dist/sw.js` 或 workbox 文件存在。
3. **预览**：`npm run preview` → Application → Manifest / Service Workers 已注册。
4. **SSE**：`redis-cli publish hardware:events '{"path":"/mnt/media","state":"offline"}'` → 卡片变灰。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_API_BASE_URL` | `/api` | 后端 API 基地址 |

## 优化要点

- API 基地址统一于 `src/utils/api.ts`，SSE 使用 `API.events()` 完整 URL。
- Dexie 操作包一层 `safe()` 防止 IndexedDB 不可用导致崩溃。
- SSE 自动重连采用指数退避（2s 起，上限 30s）。
