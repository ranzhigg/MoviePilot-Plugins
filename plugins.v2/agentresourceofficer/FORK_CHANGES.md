# Fork 修改说明

源自 [liuyuexi1987/MoviePilot-Plugins](https://github.com/liuyuexi1987/MoviePilot-Plugins)，基线提交 `1b6bb3d2f7a4cf22850908cda04de1de2323431a`，原版 0.3.1，保留原作者和 GPL-3.0 许可证。

本 fork 0.3.2 仅调整飞书整理历史搜索的分词接口：优先使用 MP SDK 的 `cut`，其次使用旧版 MP 接口，最后兼容独立 `jieba`。接口均不可用或分词执行失败时继续用原始标题搜索。在支持官方分词接口的 MP 上，无需额外安装 Python jieba。

保留上游 plugins.v2 目录与功能，不包含个人配置、运行数据库或本地脚本。此修改不会自动替换已安装的其他仓库来源插件。
