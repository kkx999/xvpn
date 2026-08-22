# XVPN Panel v1.0.0

XVPN Panel v1.0.0 是为 **XVPN Android + Mihomo Core** 重新整理的控制面板。

```text
Panel 导入节点
    ↓
校验并标准化为 xvpn.node.v1
    ↓
Android API
    ↓
XVPN Android
    ↓
Mihomo Profile / YAML
    ↓
Mihomo Core
```

Panel 不再向 Android 下发 sing-box 配置，也不再保留旧的 raw-config API 兼容路径。后台 UI、用户/邀请码、国家分类排序、流量统计、Android 更新控制、备份和 Telegram 等产品逻辑继续保留。

## v1.0.0 节点协议

- VLESS / Reality / TLS / WS / gRPC
- VMess
- Trojan
- Shadowsocks（含 SIP002 / Base64）
- Hysteria2
- TUIC
- AnyTLS

节点导入时立即校验；批量导入只要有一条无效，整批不会写入数据库。数据库中保存的是加密后的 `xvpn.node.v1` 标准节点数据。

## 安装

在 XVPN Panel v1.0.0 发布包目录执行：

```bash
bash install.sh
```

安装器会在启动正式服务之前自动运行 `selftest.py`，检查：

- Python / Flask 基础加载
- Mihomo 节点解析和必填字段
- 用户注册与登录 API
- `/api/v1/nodes`
- `/api/v1/app/bootstrap`
- 流量上报增量统计
- 默认与自定义后台路径
- 修改后台路径后旧路径返回 404

任何自检失败都会停止安装，不会启动该版本。

安装完成后运行：

```bash
xvpn
```

## 自定义后台路径

默认入口：

```text
/admin/login
```

可以直接在 **系统设置 → 后台访问路径** 修改，也可以通过 SSH：

```bash
xvpn admin-path manage-xvpn
```

新入口：

```text
/manage-xvpn/login
```

修改后旧路径直接返回 404，不重定向到新路径。

查看当前后台路径：

```bash
xvpn admin-path
```

## 卸载

```bash
xvpn uninstall
```

需要输入 `YES` 确认。会删除：

- Panel 程序
- 数据库、用户和节点数据
- Panel 配置与备份
- systemd 服务和定时器
- XVPN Panel Nginx 站点
- `xvpn-panel` 运行用户

不会卸载 Nginx / Certbot，也不会主动删除已有 Let's Encrypt 证书。

## 正式版在线安装 / 更新

正式 Release 发布后可以使用 `install-online.sh` / `xvpn update`。在线安装器要求 Release 同时提供：

- `xvpn-panel-v1.0.0.zip`
- `SHA256SUMS.txt`

下载后会先校验 SHA-256 和 ZIP 完整性，再交给 `install.sh` 执行内置自检与安装。

## Android API

Base path：`/api/v1`

Android 只消费 `profile` 中的 `xvpn.node.v1` 数据，不接收旧 sing-box JSON。详细契约见 `APP_API.md`。

旧版 Panel 源码只保存在 `legacy-panel` 分支，用于历史参考，不参与 v1.0.0 运行。
