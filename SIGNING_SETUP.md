# XVPN Android 固定签名配置

XVPN 从第一个可安装测试版开始就使用同一套 Release Keystore。后续 dev / RC / 正式版都必须继续使用它，否则 Android 会拒绝覆盖安装。

在 GitHub 仓库 `Settings → Secrets and variables → Actions` 中创建以下四个 Repository secrets：

- `XVPN_KEYSTORE_BASE64`
- `XVPN_KEYSTORE_PASSWORD`
- `XVPN_KEY_ALIAS`
- `XVPN_KEY_PASSWORD`

具体值在单独提供的 **XVPN-Android-signing-private-v1.zip** 中。

> 私有签名包绝对不要上传 GitHub，不要提交到仓库，也不要发给其他人。丢失 Keystore 后，已安装的 XVPN 无法再由新的签名包覆盖升级。

## 覆盖安装锁定规则

- 正式 Application ID 永久固定：`com.xvpn.android`
- 每个新 APK 的 `versionCode` 必须严格递增
- 所有 Release APK 使用同一个 `xvpn-release.jks`
- Debug 使用 `com.xvpn.android.debug`，不会覆盖正式测试/正式 APK，防止调试签名污染升级链

当前：

- `versionName = 1.0.1`
- `versionCode = 10018`

## 当前 Release 证书指纹（公开校验信息）

```text
SHA-256: BB:42:AE:AF:12:1C:6F:74:72:C4:E4:BF:84:8E:1D:82:AD:0E:48:4A:D4:EF:6B:22:42:C1:A9:D9:B7:F1:FE:7B
```

后续每个正式测试 APK / RC / 正式版都应由同一证书签名。
