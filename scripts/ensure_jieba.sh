#!/bin/bash
# 为 Agent影视助手的飞书搜索维护可选分词依赖
set -euo pipefail

consumer=/app/app/plugins/agentresourceofficer/feishu_channel.py
python=/opt/venv/bin/python3

if [ ! -f "$consumer" ] || ! grep -q 'import jieba' "$consumer"; then
  echo "[jieba] 未发现使用方，跳过"
  exit 0
fi

mkdir -p /config/temp
exec 9>/config/temp/ensure_jieba.lock
flock -n 9 || exit 0

if "$python" -c 'import jieba' >/dev/null 2>&1; then
  echo "[jieba] 插件分词依赖已安装"
else
  uv pip install 'jieba==0.42.1' --python "$python"
  "$python" -c 'import jieba' >/dev/null 2>&1
  echo "[jieba] 安装并验证完成"
fi
