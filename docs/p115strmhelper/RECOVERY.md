# P115StrmHelper 定制版恢复

本 fork 的 P115StrmHelper 定制版固定使用 `2.8.87`，仓库为：

`https://github.com/ranzhigg/MoviePilot-Plugins`

## 为什么要固定版本

`2.8.85` 与上游版本同号时，MoviePilot 可能重新加载上游源码，导致指定路径离线任务只提交到 115，却没有进入 STRM 直存队列。`2.8.86` 包含持久化离线任务 hash 的修复：容器重载或重建后，未完成任务仍可继续轮询。`2.8.87` 将重复离线任务处理为幂等结果，避免误报失败或重复入队。

## 部署

把 `scripts/restore_p115strmhelper.py` 和 `scripts/restore_p115strmhelper.sh` 放到容器持久化目录：

```text
/config/agent/scripts/restore_p115strmhelper.py
/config/agent/scripts/restore_p115strmhelper.sh
```

执行恢复脚本后，它会校验 fork 是否是可用候选源、固定安装 `2.8.87`，并检查运行目录包含直存队列和 STRM 触发代码。脚本只读取 `/config/app.env` 中已有的 `API_TOKEN`，不会把凭据写入仓库或输出日志。

## 恢复后的检查

```text
运行版本：2.8.87
直存队列：offline_direct_path_queue
直存路径：整理/瑟瑟（或插件配置中的目标路径）
```

不要用旧的 `p115_source.py` 或同号上游安装包覆盖恢复脚本。插件数据中的直存队列属于 MoviePilot 持久化数据，不放在插件源码包内，重建时应保留 `/config` 数据卷。
