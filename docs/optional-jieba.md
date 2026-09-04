# 可选 jieba 依赖恢复

[`scripts/ensure_jieba.sh`](../scripts/ensure_jieba.sh) 保存 Agent影视助手飞书搜索所需的可选 `jieba` 依赖维护逻辑，适用于使用 `/opt/venv` 和 `uv` 的 MoviePilot 容器。

脚本检测 `agentresourceofficer/feishu_channel.py` 是否导入 `jieba`：没有使用方则跳过，已能导入则保留现有版本，缺失时安装 `jieba==0.42.1` 并验证。并发调用通过文件锁合并，安装或验证失败返回非零状态。

## 部署

将仓库中的脚本复制到容器持久化目录，例如 `/config/agent/scripts/ensure_jieba.sh`，在容器内执行：

```bash
bash /config/agent/scripts/ensure_jieba.sh
```

容器重建可能丢失虚拟环境里额外安装的依赖，届时再次运行脚本。若已有恢复脚本，可在其中调用上述命令并保留失败状态。

本 fork 的 115 插件也支持通过 `P115STRM_SELF_HEAL_SCRIPT` 调用恢复脚本。已有该配置时，在原恢复脚本中添加调用即可；没有其他恢复需求时，可将它设置为此脚本的容器内绝对路径。该钩子只在 115 插件初始化时触发，脚本需提前部署到对应路径。

## 范围

这是独立运维脚本，不作为 115 或 STRM逐条通知的强制依赖，也不会随插件市场安装包自动部署。115 插件自身使用 MP 提供的分词接口。脚本不包含账号、Cookie、Token 或个人运行配置，不执行媒体删除、插件重装或核心补丁修改。
