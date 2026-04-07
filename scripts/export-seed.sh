#!/bin/bash
# ZEN70 离线种子提取包装脚本
# 自动调用 Python 导出脚本

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/..:${PYTHONPATH}"

python3 "${SCRIPT_DIR}/export_seed.py" "$@"
