# ZEN70 Canonical Compiler

本仓库只保留一个受支持的 IaC 编译入口：[scripts/compiler.py](../scripts/compiler.py)。

## 原则

- `system.yaml` 是唯一正式配置入口。
- `scripts/compiler.py` 是唯一正式编译入口。
- `scripts/templates/` 是唯一模板事实源。
- `deploy/` 目录可以保留离线分发和运维脚本，但不能再承载第二套编译器实现。

## 常用命令

```bash
python scripts/compiler.py system.yaml -o .
python scripts/compiler.py system.yaml -o . --dry-run
python scripts/compiler.py system.yaml -o ./out --skip-migrate
```

## 产物

- `.env`
- `docker-compose.yml`
- `render-manifest.json`

这些产物必须与 `system.yaml`、OpenAPI 导出和 pack/profile 合同保持一致。
