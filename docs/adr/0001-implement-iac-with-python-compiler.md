# ADR 0001: 采用 Python 编写自有 IaC 编译器

- Status: Accepted
- Date: 2026-03-15
- Scope: 采用 Python 编写自有 IaC 编译器

> Source of truth: code and tests override ADR text. See ADR 0052 when documentation and implementation diverge.

## 2. 决策选项
1. **采用 Helm (Kubernetes 体系)**: 业界最成熟的做法，借助 values.yaml 进行高度模板化注入。
2. **采用 Ansible**: 自动化运维标准工具，具备良好的模块库。
3. **采用定制 Python 编译器 (`compiler.py` + `bootstrap.py`)**: 仅依赖 Python3 标配库和 Docker SDK 进行直接的 `docker-compose.yml` 渲染。

## 3. 评估对比

### Helm (K8s生态)
- **优势**: 声明式完美，生态工具多，横向扩展天然支持。
- **劣势**: 违背 ZEN70 设计哲学。K8s 控制平面太重，无法在低配异构家用节点（如仅有 2GB 显存的 N100 小主机）上实现轻量级冷启动；而且 iOS 级别的 PWA 设备不易于同 K8s 内部 CNI 进行隧道打通。

### Ansible
- **优势**: 幂等性强，声明语言完善。
- **劣势**: 第三方依赖重。不仅需要管理 SSH 信任，还需要在断网时准备全套运行依赖包；对 Windows / Mac 开发端移植性支持差，容易打破法典跨平台无缝迁移要求。

### 定制 Python 编译器
- **优势**: 绝对轻量级。Python 具备极强的跨平台特性。`compiler.py` 内部基于 `Jinja2`，学习成本低，易于在本地（Windows / macOS）即刻挂载调试，也能打包随 U 盘离线分发。
- **劣势**: 缺少内建的分布式状态回滚机理（这部分交由 GitOps 承担）。

## 4. 最终决定
决定全面采用 **方案 3 (Python 定制编译器)**，舍弃 K8s 复杂架构。依靠 Python 字典解包、防腐注入与 Jinja2 实现 IaC 的 100% 渲染下发。

## 5. 影响范围
- 后续开发的新系统组件模块必须由 Python 脚本 `compiler.py` 进行逻辑汇聚，不再手工书写冗长的 `docker-compose.yml`。
- 本地开发者如果只修改底层服务，需通过 `python scripts/compiler.py` 刷新容器编排档案。
