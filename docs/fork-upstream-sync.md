# Fork 上游同步

每天北京时间 23:00（UTC 15:00）由 GitHub Actions 检查上游；GitHub 定时任务可能延迟。也可在 Actions → Fork upstream sync 手动运行。

使用普通合并，保留 fork 提交；双方改动同一文件时暂停人工审阅，不采用 ours/theirs 或强制覆盖。合并、校验或推送失败时工作流失败，生成日志、30 天报告附件，并创建或更新失败 Issue。每次运行的 Summary 展示上游提交清单及结果。失败不自动回滚已有提交，不修改本地 MP。

推送使用 GITHUB_TOKEN，合并推送不会自动触发其他 push 工作流或插件发布；插件发布及本地安装需要另行执行。验证仅覆盖工作流列出的检查，不保证所有外部服务或第三方插件兼容。

## GitHub 自带失败邮件

仓库代码无法修改账号的邮件通知偏好。在 GitHub Settings → Notifications → System → Actions，启用 Email 并选择仅失败通知；确保该仓库通知路由使用已验证的 `ranzhigg@gmail.com`。首次用自己的账号手动运行本工作流，并检查失败邮件偏好。未开启账号通知前，工作流无法保证邮件送达，也不能从仓库设置强制收件人。

不需要 SMTP 密钥。失败 Issue 和 Actions 日志始终作为反馈入口，修复后可手动重跑；失败 Issue 由人工确认后关闭。
