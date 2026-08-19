# GitHub 部署 / 更新

固定仓库：`kkx999/xvpn`

## 更新机制

- `xvpn check` 只检查 GitHub **Latest Release**，不再读取 `main/VERSION`。
- `xvpn update` 只安装 Latest Release 中的正式 `xvpn-panel-v版本.zip`。
- 正式更新必须同时存在 `SHA256SUMS.txt`，校验失败或缺失时直接停止。
- `main` 分支不再作为 Panel 正式代码包的更新源，避免开发源码覆盖生产环境。
- 安装后的 `/opt/xvpn-panel/install-online.sh` 会作为本地更新器保留；旧版安装没有本地更新器时才使用 `main` 上的兼容入口。

## 最新版一键部署 / 升级

```bash
apt update && apt install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh)
```

安装器会解析 Latest Release、下载正式 ZIP、强制验证 SHA-256，然后运行包内 `install.sh`。已有 Panel 自动进入原地升级模式并保留数据。

## 指定版本 / 重装当前版本

对应版本必须已经创建 GitHub Release，并包含正式 ZIP 与 `SHA256SUMS.txt`：

```bash
apt update && apt install -y curl ca-certificates && \
bash <(curl -fsSL https://raw.githubusercontent.com/kkx999/xvpn/main/install-online.sh) --version v1.2.1
```

安装后也可以直接执行：

```bash
xvpn update v1.2.1
```

指定与当前相同的版本会重新安装，可用于修复代码文件；只有明确降级到旧版本时才会要求再次确认。

## 常用命令

```bash
xvpn                # 管理菜单
xvpn check           # 检查 Latest Release
xvpn update          # 直接更新到最新正式版本
xvpn update v1.2.1   # 安装或重装指定正式版本
xvpn domain          # 域名 / HTTPS
```

## 发布一个版本

```bash
bash build-release.sh
```

把 `dist/` 中的 `xvpn-panel-v版本.zip` 和 `SHA256SUMS.txt` 上传到同名 GitHub Release。正式 Release 附件缺一不可。
