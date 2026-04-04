"""
iac_core — ZEN70 IaC 编译器核心库。

ADR 0011: 统一 scripts/compiler.py 与 deploy/config-compiler.py 的核心逻辑。
两个 CLI 壳共享此库的 load/merge/migrate/lint/secrets/render 能力。

模块清单:
- loader:    system.yaml 加载 + conf.d 碎片合并 + 服务/网络/卷预处理
- migrator:  配置版本链式迁移 (v1→v2→...)
- lint:      三层 Schema 校验 (FAIL / SECURITY / WARN)
- secrets:   密钥幂等生成/加载/轮转
- renderer:  Jinja2 模板渲染 + YAML 序列化工具
- models:    TypedDict 结构定义 (ADR 0009 合规)
"""

from __future__ import annotations
