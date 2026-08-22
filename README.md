# XVPN Panel v1.0.0

XVPN Panel 是为 **XVPN Android + Mihomo Core** 重新整理的控制面板。

```text
Panel 节点导入 → xvpn.node.v1 → Android → Mihomo Profile → Mihomo Core
```

Panel 不再向 Android 下发 sing-box 配置。后台 UI、用户/邀请码、节点分类排序、流量统计、Android 更新控制、备份和 Telegram 等产品逻辑继续保留。

## 安装

在发布包目录执行：

```bash
bash install.sh
```

安装完成后：

```bash
xvpn
```

## 自定义后台路径

默认后台路径是：

```text
/admin/login
```

可改成例如：

```bash
xvpn admin-path manage-xvpn
```

修改后 Panel 会重启，新后台变成：

```text
/manage-xvpn/login
```

旧路径直接返回 404。

查看当前后台路径：

```bash
xvpn admin-path
```

## 卸载

```bash
xvpn uninstall
```

会要求输入 `YES` 确认，然后删除 Panel 程序、数据库、用户/节点数据、配置、备份、systemd 服务、Nginx Panel 站点和运行用户。

不会卸载 Nginx / Certbot，也不会主动删除已有 Let's Encrypt 证书。

## 节点协议

v1.0.0 标准节点模型支持：

- VLESS / Reality / TLS / WS / gRPC
- VMess
- Trojan
- Shadowsocks
- Hysteria2
- TUIC
- AnyTLS

Android 端只消费 `xvpn.node.v1`，不再依赖旧 sing-box 数据结构。
