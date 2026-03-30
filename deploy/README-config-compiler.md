# [任务名称] ZEN70 配置编译器 (config-compiler.py)

> **⚠️ 本文件是离线分发包的编译器薄壳。**
> **唯一事实来源 (Canonical Compiler)：[`scripts/compiler.py`](../scripts/compiler.py)（ADR 0011 §3.1）**
> 功能演进必须先落地 `scripts/compiler.py`，再同步至 `deploy/config-compiler.py`。
> 严禁在此文件上进行独立功能开发，否则将产生双编译器脑裂。

## 目标

实现 IaC 唯一事实来源的配置编译流水线：从 `config/system.yaml` 或 `config/conf.d/*.yml` 读取并合并配置树，经版本迁移后，用 Jinja2 渲染 `deploy/templates/` 下的模板，输出 `.env` 和 `docker-compose.yml`，并内置版本迁移脚本（检测 system.yaml 的 config_version 并自动升级）。

## 技术要点

- **配置加载**：优先读取 `config/system.yaml`；若存在 `config/conf.d/`，则按文件名排序加载所有 `*.yml` / `*.yaml`（排除 `*.local.yml`），通过递归 `deep_merge` 合并为单一配置树。
- **路径规范**：全程使用 `pathlib.Path`，禁止 `os.path`。
- **类型与规范**：Python 3.11+，全函数 Type Hints；错误码统一为 `ZEN-COMPILE-xxx`。
- **Jinja2 渲染**：模板目录 `deploy/templates/`，上下文传入合并后的 `config`；默认输出 `.env`、`docker-compose.yml` 到项目根目录。
- **版本迁移**：配置项 `config_version`（整数）；若缺失或小于当前支持版本，依次执行 0→1 等迁移（补全 project/network/database/redis/storage/services/observability 默认结构），并输出变更说明；迁移不修改入参（深拷贝后处理）。
- **敏感信息**：不在配置中写明文密码，模板中使用 `${POSTGRES_PASSWORD}` 等占位，由点火脚本注入 .env。

## 输入

- **前置条件**：Python 3.11+，已安装 `PyYAML`、`Jinja2`（见 `deploy/requirements.txt`）。
- **配置文件**（二选一或同时使用）：
  - `config/system.yaml`：主配置；
  - `config/conf.d/*.yml`：碎片化配置，按文件名排序合并。
- **模板**：`deploy/templates/.env.j2`、`deploy/templates/docker-compose.yml.j2`。

## 输出

- **生成文件**（默认在项目根目录）：
  - `.env`：环境变量，供 docker-compose 与网关使用；
  - `docker-compose.yml`：v3.8+ 编排文件（restart: unless-stopped、健康检查、资源限制等）。
- **控制台**：迁移变更说明（若有）；成功时打印已生成文件路径。

## 完成度检查

- [ ] **依赖**：`pip install -r deploy/requirements.txt` 无报错。
- [ ] **仅主配置**：存在 `config/system.yaml` 时，执行  
  `python deploy/config-compiler.py`  
  应在项目根生成 `.env`、`docker-compose.yml`，且内容包含 `ZEN70_PROJECT`、`postgres`/`redis`/`gateway` 服务。
- [ ] **碎片合并**：在 `config/conf.d/` 下新增 `01-override.yml`，覆盖或补充 `project.name`，再次运行编译器，生成的 `.env` 中 `ZEN70_PROJECT` 应为覆盖后的值。
- [ ] **版本迁移**：将 `config/system.yaml` 中 `config_version` 改为 `0` 或删除该键，运行编译器，控制台应出现“从版本 0 迁移至 1”的变更说明，且生成文件正常。
- [ ] **干跑**：`python deploy/config-compiler.py --dry-run` 不写入文件；`--skip-migrate` 跳过迁移仍能生成文件。
- [ ] **错误处理**：删除或清空 `config/system.yaml` 且 `conf.d/` 无有效 yml 时，应退出码 1 并输出 `ZEN-COMPILE-002`。

## 代码示例

完整实现见仓库内以下文件：

- **编译器**：`deploy/config-compiler.py`（含 `load_config`、`deep_merge`、`migrate_config`、`render_templates`、`main` 及 CLI）。
- **依赖**：`deploy/requirements.txt`（PyYAML、Jinja2）。
- **默认主配置**：`config/system.yaml`（含 `config_version: 1` 及 project/network/database/redis/storage/services/observability）。
- **碎片示例**：`config/conf.d/00-base.yml`。
- **模板**：`deploy/templates/.env.j2`、`deploy/templates/docker-compose.yml.j2`。

运行命令示例：

```bash
# 默认：使用 config/、deploy/templates/，输出到项目根
python deploy/config-compiler.py

# 指定目录与干跑
python deploy/config-compiler.py --config-dir ./config --output-dir ./out --dry-run

# 跳过迁移
python deploy/config-compiler.py --skip-migrate
```

以上满足 ZEN70 架构对 IaC、pathlib、Type Hints、无硬编码与敏感信息占位的要求，并与 .cursorrules 任务响应格式一致。
