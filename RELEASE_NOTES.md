# XVPN Panel v1.2.1

本次正式版新增 Android App 更新控制中心，并继续收口更新策略与发布安全。

## 主要更新

- 新增公开 `GET /api/v1/app/update`，Android 优先通过 Panel 获取更新信息。
- Android APK 继续托管在独立 GitHub Release，Panel 不保存 APK 文件。
- Android Release 仓库可在后台自定义，默认 `kkx999/XVPN-Android`。
- 自动校验 APK 与 `SHA256SUMS.txt`，并解析 versionName / versionCode / Release Notes。
- 新增“最低允许运行版本”历史 Release 下拉，不再需要手工填写 versionCode。
- 历史正式版与测试版均展示；无法识别 versionCode 的版本禁用。
- 高于当前 Latest Release 的历史测试版本仍展示但不可设为最低版本，避免客户端进入无法满足的强制更新循环。
- 支持强制更新、最低版本策略、立即同步、Latest/历史 Release 缓存和 GitHub 临时异常时的陈旧缓存标记。
- 修复缓存同步失败后状态被错误恢复为“正常”的逻辑问题，并对连续失败进行缓存节流。
- 切换 Android 仓库时自动清除旧仓库缓存并重置最低版本策略。
- Panel 自身更新继续严格使用 GitHub Latest Release + `SHA256SUMS.txt`，不从 main 分支安装生产代码。

兼容：从 XVPN Panel v1.2.0 原地升级并保留现有数据库、节点、用户、邀请码、流量、备份、Telegram、域名与管理员配置。
