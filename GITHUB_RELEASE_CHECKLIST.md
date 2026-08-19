# GitHub 正式发布检查清单

正式发布时按以下顺序操作，避免仓库源码与 Release 资产不一致：

1. 先把本目录完整同步到 GitHub `main`，不要只改 `VERSION`。
2. 确认 `main` 中至少存在：`app/traffic.py`、`app/event_log.py`，首页模板包含“今日流量概览”，品牌标识为 `X`。
3. 本地执行 `bash release-check.sh`。
4. 执行 `bash build-release.sh` 生成正式资产。
5. 创建与 `VERSION` 一致的 Release Tag，例如 `v1.2.1`。
6. Release 必须上传：`xvpn-panel-v1.2.1.zip` 与 `SHA256SUMS.txt`。
7. 发布后执行 `xvpn check`；如检测到新版本，执行 `xvpn update`。

更新器只使用正式 Release 资产，不再安装 `main` 分支源码。即使 `main` 后续进入开发状态，也不会被 `xvpn update` 当作生产版本部署。
