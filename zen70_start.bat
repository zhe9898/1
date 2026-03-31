@echo off
chcp 65001 >nul
echo =========================================================
echo            ZEN70 V3.43 一键启动引擎 (Start Engine)
echo =========================================================

echo.
echo [1/4] 正在拉起底层基础网络与容器编排...
docker compose -p zen70 up -d --remove-orphans

echo.
echo [2/4] 环境预检与探测...
set PYTHONPATH=.
REM 法典 §1.2: REDIS_HOST 由 IaC 编译器写入 .env，禁止硬编码
REM 运行时从 .env 读取（docker compose 自动加载；此处仅为裸跑兜底）
for /f "tokens=1,* delims==" %%a in ('findstr /B "REDIS_HOST=" .env 2^>nul') do set REDIS_HOST=%%b
if not defined REDIS_HOST set REDIS_HOST=redis

echo.
echo [3/4] 安全探针与守望者监控网略已由容器集群接管运行...
REM (法典 1.1：一切皆容器) 物理探针已被迁移至 system.yaml 中的 sentinel 容器，禁止在此裸跑 Python

echo.
echo [4/4] 正在呼出主控台浏览器窗口...
start http://localhost/
start http://localhost

echo.
echo =========================================================
echo ✅ ZEN70 系统已全栈满血上线！
echo =========================================================
pause
