# GitHub 部署 / 更新

固定仓库：`kkx999/xvpn`

## 最新版一键部署 / 升级

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh)
```

已有 Panel 会自动按升级模式运行并保留数据。

## 指定版本

需要对应版本已经存在 GitHub Release 或 Tag：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh) --version v1.0.0
```

安装后也可以：

```bash
vpn update v1.0.0
```

指定低于当前版本时会明确提示这是降级，并要求再次确认。

## 常用命令

```bash
vpn                 # 管理菜单
vpn check           # 检查 main 是否有新版本
vpn update          # 部署 main 最新版本
vpn update v1.0.0   # 指定 Release / Tag
vpn domain          # 域名 / HTTPS
```

## 发布一个版本

```bash
bash build-release.sh
```

把 `dist/` 里的 ZIP 和 `SHA256SUMS.txt` 上传到同名 GitHub Release（Tag 例如 `v1.0.0`）。
