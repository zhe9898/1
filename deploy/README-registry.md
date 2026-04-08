# ZEN70 私有镜像仓库与离线种子包

本文档说明私有 Docker Registry、镜像同步脚本和离线种子包导出/恢复流程。离线恢复统一走 `scripts/bootstrap.py --offline`，不再经过兼容 wrapper。

## 私有仓库

- 启动：`./deploy/registry-setup.sh`
- 验证：`docker ps | findstr zen70-registry` 或 `curl http://localhost:5000/v2/_catalog`
- `deploy/images.list` 中的镜像必须全部 digest pin

## 镜像同步

```bash
./deploy/pull-sync.sh
```

脚本会把 `deploy/images.list` 中的镜像拉取、重打 tag 并推送到私有仓库。

## 导出离线种子包

```bash
./deploy/export-seed.sh
```

输出物为 `zen70-seed.tar.gz`，其中包含：

- `git-repo/`
- `images/`
- `images.list`
- `bootstrap-offline.sh`

## 离线恢复

```bash
tar -xzf zen70-seed.tar.gz
cd zen70-seed
./bootstrap-offline.sh
```

离线脚本会：

1. 加载 `images/` 下的本地镜像
2. 进入 `git-repo`
3. 执行 `python3 scripts/bootstrap.py --offline`

整个流程只消费种子包内的仓库副本、配置和本地镜像，不访问外网。
