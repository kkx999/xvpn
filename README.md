# XVPN Panel

> 当前正式版本：`v1.2.1`。

邀请制私人 VPN 管理面板：管理员在网页维护节点、用户和邀请码，Android App 用户注册登录后直接使用全部启用节点。

> GitHub 项目描述：邀请制私人 VPN 管理面板，支持节点管理、用户/邀请码、Android API、自动备份、Telegram、HTTPS 与一键部署。

## 主要功能

- 节点导入、重命名、国家分类与两级排序
- 邀请码注册、可用次数、用户停用与密码管理
- 管理员用户名 / 密码均可在后台修改
- Android API：注册、登录、改密码、Bootstrap、节点下发、用户流量上报；管理员账户也可登录 App
- Android App 更新控制：Panel 默认读取 `kkx999/XVPN-Android` Latest Release（后台可自定义 owner/repo），校验 APK / SHA-256，并下发更新策略
- 明暗主题、iOS 风格开关、网页化管理
- 首页今日流量概览：TOP 5 用户 / 节点排行；用户今日 / 本月 / 累计流量后台汇总与分页明细
- 手动/自动备份、上传恢复、Telegram 异地备份与可清除运行记录
- 域名 / Let's Encrypt HTTPS / Cloudflare 场景
- `xvpn` 中文管理菜单

## 一键部署 / 升级最新版

Debian / Ubuntu，使用 root：

```bash
apt update && apt install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh)
```

全新安装会询问是否配置域名；已有安装会自动进入升级模式并保留数据。

## 指定版本

指定版本需要已经创建对应 GitHub Release，并包含正式 ZIP 与 `SHA256SUMS.txt`：

```bash
apt update && apt install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh) --version v1.2.1
```

或安装后直接执行：

```bash
xvpn update v1.2.1
```

## 管理命令

```bash
xvpn
xvpn check
xvpn update
xvpn update v1.2.1
xvpn domain
xvpn status
xvpn restart
xvpn logs
xvpn password
xvpn version
```

更新默认只使用 Latest Release 正式资产，不再用 `main` 分支源码覆盖生产环境。Android 接口见 `APP_API.md` / `APP_API_OPENAPI.yaml`，GitHub 更新说明见 `GITHUB_UPDATE.md`。


## Android App 更新策略补充

- 最低允许运行版本由历史 GitHub Release 下拉选择；管理员无需手工记忆 versionCode。
- 历史列表同时标记正式版/测试版，只有可识别 versionCode 的完整 APK Release 可选择。
