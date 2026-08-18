# XVPN Panel

邀请制私人 VPN 管理面板：管理员在网页维护节点、用户和邀请码，Android App 用户注册登录后直接使用全部启用节点。

> GitHub 项目描述：邀请制私人 VPN 管理面板，支持节点管理、用户/邀请码、Android API、自动备份、Telegram、HTTPS 与一键部署。

## 主要功能

- 节点导入、重命名、国家分类与两级排序
- 邀请码注册、可用次数、用户停用与密码管理
- Android API：注册、登录、改密码、Bootstrap、节点下发
- 明暗主题、iOS 风格开关、网页化管理
- 手动/自动备份、上传恢复、Telegram 异地备份
- 域名 / Let's Encrypt HTTPS / Cloudflare 场景
- `vpn` 中文管理菜单

## 一键部署 / 升级最新版

Debian / Ubuntu，使用 root：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh)
```

全新安装会询问是否配置域名；已有安装会自动进入升级模式并保留数据。

## 指定版本

指定版本需要已发布为 GitHub Release 或 Tag：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh) --version v1.0.0
```

或安装后：

```bash
vpn update v1.0.0
```

## 管理命令

```bash
vpn
vpn check
vpn update
vpn domain
vpn status
vpn restart
vpn logs
vpn password
```

Android 接口见 `APP_API.md` / `APP_API_OPENAPI.yaml`，GitHub 更新说明见 `GITHUB_UPDATE.md`。
