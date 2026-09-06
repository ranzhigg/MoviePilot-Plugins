# 115网盘STRM助手

## 核心优势

> [!NOTE]
> 深度融合 MoviePilot 现有优势，构建极致115网盘自动化媒体库实现方案，释放无与伦比的播放性能

#### **灵活高效的 STRM 文件生成引擎**
- 监控 MoviePilot 整理事件生成
- 全量同步网盘生成
- 增量同步网盘生成
- 生活事件监控生成
#### **风驰电掣的极致运行速度**
- **自动化资源入库**：一键触发 115 分享资源的自动转存与整理流程，化繁为简，极致高效
- **闪电级生成速度**：性能卓越，经过极致优化，可在 20 秒内 完成超过 10 万个 STRM 文件的生成任务
- **毫秒级播放响应**：播放请求通过 302 重定向技术实现毫秒级响应，点击瞬间即可开始加载播放，提供如本地文件般的流畅体验
#### **多元化的全网络资源获取**
- 阿里云盘资源秒传获取
#### **稳定可靠的增强服务套件**
- 显著提升 MoviePilot 115 上传稳定性与速度
- 全面提升 MoviePilot 115 资源整理效率与稳定性
- 共享离线下载和分享链接
#### **专业透明的问题反馈模式**
- 提供基于 Sentry 的自动化错误报告系统和开放的 Github Issue 跟踪，确保每一个问题都能被及时记录、快速响应和高效解决
#### **独家实验性功能**
- 单资源多设备同时播放

#### **Plex App 播放支持（已集成）**
- 不依赖 Emby：MoviePilot 读取 STRM 首行的 115 302 地址，并用 `ffprobe` 探测真实媒体流。
- 通过 Plex MediaInfo Helper 将视频、音频和字幕流信息安全写入 Plex 本地数据库，帮助 Plex App 正确识别 STRM 媒体并进行直播放。
- 支持播放停止 Webhook：电影补全当前条目，剧集可按配置预取后续集数；也支持按媒体库手动全量补全。
- 只负责 Plex App 所需的媒体信息补全，不包含 Plex Web 反向代理，也不会改写 STRM 文件内容。

-------

## Plex App 播放支持

### 工作流程

```text
Plex App 播放/停止
        ↓（Webhook，可选）
P115StrmHelper 获取 Plex 条目与 STRM 路径
        ↓
按路径映射读取 STRM，并用 ffprobe 跟随 115 302 探测媒体流
        ↓
Plex MediaInfo Helper 写入 Plex 本地数据库
        ↓
Plex App 重新识别媒体流并支持直播放
```

### 前置条件

1. MoviePilot 容器能够访问 Plex API 地址和媒体库对应的 STRM 文件。
2. 在 Plex 所在机器部署 [Plex MediaInfo Helper](../../plex-mediainfo-helper/README.md)，并确认 `/health`、`/dbinfo` 正常。
3. 准备 Plex Token 与 Helper Token。Token 只填写在 MoviePilot 配置中，不要提交到仓库或文档。

### P115StrmHelper 配置

在「Plex App 播放」中填写以下项目：

| 配置项 | 说明 | 示例 |
| --- | --- | --- |
| 启用 Plex App 媒体补全 | 开启 Plex App 补全链路 | 开启 |
| Plex 直连地址 | MoviePilot 可访问的 Plex API 根地址 | `http://10.0.200.10:32400` |
| Plex Token | Plex 的 `X-Plex-Token` | 不公开 |
| Plex MediaInfo Helper 地址 | Helper 的 HTTP 地址 | `http://host.docker.internal:9001` |
| Helper Token | 对应 Helper 的 `PTH_TOKEN` | 不公开 |
| Plex 路径 → MoviePilot 路径映射 | Plex 返回路径到 MP 容器路径的映射 | `/Volumes/data=/media` |
| Plex 媒体库 key | 需要补全的库，逗号分隔 | `39,40,44` |
| 启用播放停止 Webhook | 播放结束后自动补全 | 开启 |

`Plex 直连地址` 是 Plex API 地址，不是 STRM 文件里的 MoviePilot 地址；例如 `http://y.yiya.love:3000` 是 STRM 访问地址，不能填到 Plex API 地址中。

建议初始使用以下安全参数：

- 仅补全缺少媒体流信息的项目：开启
- 覆盖旧媒体流：开启
- `ffprobe` 超时：40 秒
- 探测并发数：3
- Webhook 去重窗口：300 秒
- 剧集预取：5 集

### Webhook

开启后，将 Plex Webhook 指向当前 MoviePilot 的 P115StrmHelper 接口：

```text
http://<MoviePilot地址>/api/v1/plugin/P115StrmHelper/plex_app/webhook
```

如 MoviePilot 的外部访问入口需要 API Key，请按现有部署方式附加认证参数。配置完成后，先用「检查 Helper」，再用「读取 Plex 媒体库」确认 section key，最后执行一次「立即补全」验证写库结果。

### 使用边界

- 该功能针对 Plex App 的 STRM 媒体流识别与直播放支持。
- Plex Web 浏览器和 PC 客户端是否直播放仍取决于 Plex 自身的转码、网络和客户端能力；本功能不提供 Plex Web 反向代理。
- Helper 直接写入 Plex 数据库，Plex 升级后应重新执行 `/dbinfo` 检查并先备份数据库。

## 配置解析

### STRM 同步配置

#### 1. 全量同步

> [!NOTE]
> 基于网盘目录树，批量拉取数据生成STRM
> 目前生成最高效率 1.2w/s（Rust 模式开启情况下）

1. **覆盖模式**：【从不；总是】
- **从不**：当文件存在时，直接跳过
- **总是**：当文件存在时，覆盖原有内容
2. **清理失效STRM文件**：此功能包含三个设置项，用于同步时自动清理无效文件
- **清理失效STRM文件**【开关】：开启后将自动清理网盘不存在但本地存在的`STRM`文件
- **清理无效STRM目录**【开关】：开启后将自动删除清理STRM文件后的本地空目录（开启依赖于`清理失效STRM文件`）
- **清理无效STRM文件关联的媒体文件**【开关】：开启后将自动删除清理STRM文件后残留的媒体元数据（开启依赖于`清理失效STRM文件`）
3. **定期全量同步**：此功能包含两个设置项
- **定期全量同步开关**【开关】：开启后将依据`运行全量同步周期`定期执行全量同步
- **运行全量同步周期**【Cron】：执行周期，五位`Cron`表达式

-------

## 许可证

此项目根据 GNU General Public License v3.0 许可证进行许可，详见[`LICENSE`](https://github.com/DDSRem-Dev/MoviePilot-Plugins/blob/main/LICENSE) 文件。

#### 附加条款

- 请勿将插件程序用于商业用途。
- 请勿将插件程序用于任何违反法律法规的行为。
- 本仓库所有脚本均基于官方API制作，使用请自行承担数据损失但不限于此的风险。
- 本仓库所有脚本仅供学习交流，使用本仓库脚本进行违法操作产生的法律责任由操作者自行承担。

#### 免责声明

使用此项目则意味着你接受以上规定和 GNU General Public License v3.0 许可证。

-------

## 隐私政策

- 插件程序内包含可选的Sentry分析组件，详见[Sentry Privacy Policy](https://sentry.io/privacy/)。
- 插件程序将在必要时上传错误信息及运行环境信息。
- 插件程序将记录程序运行重要节点并保存追踪数据至少72小时。
