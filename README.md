# XVPN Android

XVPN 私人 VPN Android 客户端。

## 当前版本

`v1.0.1`（`versionCode 10018`）

1.0.1 在 1.0.0 基线之上修复已连接状态下节点优选的误判：当前节点用真实隧道健康检查，候选节点仅显示入口连通性，不会将两种不同口径的延迟混合排序后自动切换。Application ID、固定签名链和 sing-box/libbox 1.13.19 数据面保持不变。

变更摘要见 [RELEASE_NOTES.md](RELEASE_NOTES.md)，逐项验收见 [QA_CHECKLIST.md](QA_CHECKLIST.md)。

## 1.0.1 主要变化

- 智能分流内置版本固定、带 SHA-256 校验的 SagerNet `geosite-geolocation-cn` 与 `geoip-cn` 二进制规则；私网、本地域名与中国域名/IP 直连，其余流量走当前节点。全局代理仍保留局域网直连，所有公网流量走节点。
- 不照搬 Quantumult X 专有 rewrite 或广告规则；采用“本地优先、国内直连、最终代理”的可维护分流顺序，并只拒绝网页 UDP/443，使不支持 QUIC 的节点能立即回落 TCP/TLS，其他 UDP 流量不受影响。
- TUN 使用 IPv4 安全路径、MTU 1400、`mixed` 栈、`strict_route` 与 DNS 劫持，避免节点没有可用 IPv6 出口时出现 AAAA 卡死或运营商 IPv6 旁路。国内域名使用明确直连的 AliDNS DoH，其他域名使用经代理的 Google DoH；节点域名由独立加密引导解析器建立连接，避免 DNS/代理依赖回环。
- 支持 VLESS、Trojan、VMess、Shadowsocks、Hysteria2、TUIC 与 AnyTLS；VLESS / Trojan / VMess 同时处理常见 TLS、REALITY、uTLS 与 V2Ray transport 分享参数。Panel 仍可保存其他协议，但 App 会明确提示当前不支持。
- 断开状态可自动优选；已连接时可切换智能分流 / 全局代理、打开节点列表、手动换节点，并使用“连接检测”验证当前隧道。首次连接与热重载不仅执行 libbox `checkConfig`，还会通过当前 TUN 发起真实 HTTPS 健康检查；新配置无法解析 DNS 或访问代理出口时自动回滚原节点与原模式，不会发布虚假“已连接”。
- TCP 候选节点使用 Android 物理网络解析域名和受保护 Socket 测试入口；在部分 OEM 上绑定底层网络失败时会安全回退为仅 protect 的 Socket，避免测速回到当前 TUN。当前正在连接的任意协议节点均通过真实 HTTP 隧道实测；候选结果明确标记为入口连通性，不拿它与隧道延迟混合比较。
- 前台通知使用系统原生、克制的两层卡片：折叠显示节点与分流模式，展开显示实时上下行、连接时长和“安全断开”。Android 13+ 首次连接请求通知权限，“我的”页面提供通知设置入口。
- 连接球、自动优选、节点结果、底部卡片与主题切换统一为短促的分层动效；浅色和深色分别调整冰晶高光、轮廓与对比度，并遵循系统“移除动画”设置。
- “我的 → 连接诊断”可复测当前 DNS、分流、节点协议与代理出口，显示脱敏后的检测站点及响应时间。
- 默认 Panel 为 `https://xvpn.666101.xyz`。升级只迁移历史内置地址 `https://xx.666101.xyz` 与 `https://xvpn666101.xyz`，用户手动保存的自定义 Panel 地址保持不变。
- 当前版本明确关闭 Android 系统 Always-on 能力，避免系统在没有安全持久化节点凭据时伪启动；普通前台 VPN、后台保持和系统通知不受影响。
- Panel 更新策略优先；Panel 临时超时、DNS/TLS 或 5xx 故障时回退 GitHub Release，Panel 明确暂停更新时仍遵守服务端策略。

## 当前测试边界

- CPU：1.0.1 仅包含 `arm64-v8a`，用于当前 arm64 Android 16 真机及同架构设备。
- 系统：Android 8.0+（minSdk 26），target / compileSdk 36。
- 流量：`TrafficStats` 是 App/Core UID 的代理侧统计，适合产品展示与 Panel 汇总，不是防篡改计费依据。
- 仍需真机验证各节点协议、Wi-Fi / 蜂窝切换、长时间锁屏、分流覆盖、DNS 检测以及各厂商通知栏样式。源码版本号为 1.0.1 不等于 APK 已通过这些设备项目，正式 Release 应保存完整验收记录。

## GitHub Actions 编译

工作流：`.github/workflows/build-apk.yml`

1. 按 [SIGNING_SETUP.md](SIGNING_SETUP.md) 配置四个固定 Release 签名 Secrets。
2. 将本目录内容上传到 GitHub 仓库根目录。
3. 打开 `Actions` 运行 `Build XVPN Android`，或 push 到 `main`。
4. 下载 Artifact `XVPN-v1.0.1`，其中包含 APK 与 `SHA256SUMS.txt`。

工作流会核对应用 ID、版本、libbox AAR、内置分流规则校验值、固定签名证书、APK 包名与 `arm64-v8a` ABI。后续 APK 必须继续使用当前固定签名并递增 `versionCode`。

构建基线：

- Release Application ID：`com.xvpn.android`
- Debug Application ID：`com.xvpn.android.debug`
- Java：17
- Android Gradle Plugin：8.13.2
- Gradle：8.13
- Build Tools：35.0.0

## Panel API

客户端按 Panel v1.2.1 使用 `/api/v1` Bearer Token API，包括注册、登录、bootstrap、节点、用户状态、流量上报、更新检查、修改密码与退出。登录 Token 使用 Android Keystore AES/GCM 加密保存；节点配置只交给本机 Core 生成器，不在 UI 中展示或提供复制入口。

## 第三方内核、规则与许可证

1.0.1 内置 sing-box/libbox 1.13.19 arm64 组件，以及固定版本的 SagerNet sing-geosite / sing-geoip 规则文件，受 GPL-3.0-or-later 与对应上游声明约束。来源、提交与 SHA-256 见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，许可证文本位于 `licenses/`。
