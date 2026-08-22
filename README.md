# XVPN Panel v1.0.0

XVPN Panel v1.0.0 是为 **XVPN Android + Mihomo Core** 重新整理的控制面板。

## 一键安装

推荐 Debian 12，使用 root 执行这一条即可：

```bash
apt update && apt install -y curl && bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-latest.sh)
```

脚本会自动下载最新版源码、检查完整性，然后进入正式安装流程；不需要手动 `git clone`、解压或切换目录。

安装过程中按提示配置域名和 HTTPS 即可。正式服务启动前会自动运行自检；节点解析、API、数据库或后台路径检查失败时会直接停止安装。

## 常用命令

```bash
xvpn                         # 打开管理菜单
xvpn status                  # 查看服务状态
xvpn logs                    # 查看实时日志
xvpn restart                 # 重启 Panel
xvpn update                  # 更新 Panel
xvpn password                # 重置管理员密码
xvpn domain                  # 域名 / HTTPS 管理
xvpn admin-path manage-xvpn  # 修改后台路径
xvpn uninstall               # 彻底卸载并清除 Panel
```

后台路径也可以直接在 **系统设置 → 后台访问路径** 修改。修改后旧路径直接返回 404，不会暴露新路径。

`xvpn uninstall` 会要求输入 `YES`，然后删除 Panel 程序、数据库、用户/节点数据、配置、备份、systemd 服务、Nginx Panel 站点和运行用户；不会卸载 Nginx / Certbot，也不会主动删除已有 Let's Encrypt 证书。

## Mihomo 架构

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

Panel 不再向 Android 下发 sing-box 配置，也不保留旧 raw-config API 兼容路径。后台 UI、用户/邀请码、国家分类排序、流量统计、Android 更新控制、备份和 Telegram 等产品逻辑继续保留。

## v1.0.0 节点协议

- VLESS / Reality / TLS / WS / gRPC
- VMess
- Trojan
- Shadowsocks（含 SIP002 / Base64）
- Hysteria2
- TUIC
- AnyTLS

节点导入时立即校验；批量导入只要有一条无效，整批不会写入数据库。数据库中保存的是加密后的 `xvpn.node.v1` 标准节点数据。

Android API Base path：`/api/v1`。Android 只消费 `profile` 中的 `xvpn.node.v1` 数据，详细契约见 `APP_API.md`。

旧版 Panel 源码只保存在 `legacy-panel` 分支，用于历史参考，不参与 v1.0.0 运行。
