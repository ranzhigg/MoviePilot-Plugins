#!/usr/bin/env bash
set -euo pipefail
report="$RUNNER_TEMP/upstream-sync-report.md"
printf '# 上游同步清单\n\n' > "$report"
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git fetch --no-tags https://github.com/DDSRem-Dev/MoviePilot-Plugins.git main
upstream=$(git rev-parse FETCH_HEAD)
base=$(git merge-base HEAD "$upstream")
echo "上游提交：$upstream" >> "$report"
git log --format='- %h %s' HEAD.."$upstream" >> "$report"
if git merge-base --is-ancestor "$upstream" HEAD; then
  echo '没有上游更新。' >> "$report"
  echo 'changed=false' >> "$GITHUB_OUTPUT"
  exit 0
fi
# 双方修改同一文件时交给人工处理，避免自动合并悄悄改变定制行为
git diff --name-only "$base" HEAD | sort > "$RUNNER_TEMP/fork-paths"
git diff --name-only "$base" "$upstream" | sort > "$RUNNER_TEMP/upstream-paths"
comm -12 "$RUNNER_TEMP/fork-paths" "$RUNNER_TEMP/upstream-paths" > "$RUNNER_TEMP/overlap"
if [ -s "$RUNNER_TEMP/overlap" ]; then
  echo '需要人工审阅：上游与 fork 同时修改以下文件，未推送：' >> "$report"
  cat "$RUNNER_TEMP/overlap" >> "$report"
  exit 1
fi
if ! git merge --no-ff --no-edit "$upstream"; then
  echo '合并冲突，未推送：' >> "$report"
  git diff --name-only --diff-filter=U >> "$report"
  git merge --abort
  exit 1
fi
git diff --check HEAD^ HEAD
echo 'changed=true' >> "$GITHUB_OUTPUT"
echo '合并完成，等待验证。' >> "$report"
