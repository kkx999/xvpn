# XVPN Android v1.0.1 测试清单

## 源码与构建门槛

- [x] Application ID = `com.xvpn.android`
- [x] `versionName = 1.0.1`，`versionCode = 10018`
- [x] 仅打包 `arm64-v8a/libbox.so`，libbox 版本固定为 1.13.19
- [x] libbox AAR 与两份本地 SRS 规则均有固定 SHA-256，GitHub Actions 会在编译前核对
- [x] 每次首次连接和已连接热切换都会先执行 libbox `checkConfig`
- [x] 7 种协议 × 智能 / 全局共 14 份配置有 JVM 生成测试，并校验关键 TUN / DNS / 分流结构
- [ ] 14 份新配置在 GitHub 构建后分别通过真机 libbox `checkConfig` 与真实节点握手
- [x] 源码中无 `BlurMaskFilter` / 软件层发光；动画关闭时保留静态状态与操作能力
- [ ] Gradle 8.13 + AGP 8.13.2 干净执行 `lintRelease` 与 `assembleRelease` 通过
- [ ] GitHub APK 通过包名 / 版本、zipalign、APK Signature Scheme v2、单 DEX、规则资产与 `arm64-v8a` ABI 结构检查
- [ ] GitHub APK 的固定证书摘要检查通过

> 真机安装包必须由仓库固定 Release 签名工作流生成；不要使用旧 RC1 的本地 APK 代替本次构建。

## 安装、浅色与深色

- [ ] GitHub Actions `Build XVPN Android` 完整成功
- [ ] `XVPN-v1.0.1.apk` 证书 SHA-256 与 `SIGNING_CERT_SHA256.txt` 一致
- [ ] 可从 RC1 覆盖安装，登录、主题、已选节点与分流偏好保留
- [ ] 历史内置 `https://xx.666101.xyz` 与 `https://xvpn666101.xyz` 自动迁移到 `https://xvpn.666101.xyz`，自定义 Panel 地址不被改写
- [ ] 浅色 Launcher 图标尺寸和呼吸区正确，无深蓝底
- [ ] 浅色首页、连接球、Header、卡片与底部弹层无毛刺、色带或过亮蓝边
- [ ] 深色首页、连接球、Header、卡片与底部弹层不灰蒙，细边、文字与高光对比清楚
- [ ] 底部卡片入场 / 退场自然；连续打开“节点列表 → 节点详情”不重叠、不闪屏
- [ ] 系统动画设为关闭后，连接、切换节点和关闭弹层仍可正常操作

## 真实 Core 与通知

- [ ] 首次连接依次出现通知权限（Android 13+）与 VPN 授权；任一拒绝都不伪造连接
- [ ] 授权后先显示“正在连接”，仅在内核启动且 HTTPS 健康检查成功后显示“已连接”
- [ ] 通知折叠态显示节点与模式；展开态显示连接时长、上下行与“安全断开”
- [ ] 通知在浅色 / 深色系统以及锁屏上清楚、克制，不出现大图或刺眼色块
- [ ] 从通知点击可返回 App；“安全断开”可关闭隧道和系统 VPN 图标
- [ ] App 后台、划回桌面、锁屏 30 分钟后连接保持，通知仍能反映状态
- [ ] Wi-Fi ↔ 蜂窝切换后可恢复，物理网络检测不选择 XVPN 自身 TUN

## 分流与热切换

- [ ] 智能分流下局域网可访问，中国站点 / App 可用，境外站点走节点
- [ ] 重点复测 dev5.10 中“部分能开、部分打不开”的网站与 App，并记录域名和时间
- [ ] 全局代理下所有公网 IPv4 走当前节点，局域网仍可访问
- [ ] 已连接时智能分流 ↔ 全局代理双向切换成功，不要求先断开
- [ ] 已连接时能打开节点列表、查看详情、手动切换节点
- [ ] 断开状态下自动优选能逐项反馈候选入口延迟，并只在选出结果后执行一次热切换
- [ ] 已连接时“连接检测”对当前节点显示真实 HTTPS 隧道结果；不得将该结果误报为入口不可达
- [ ] 已连接时其他节点仅显示入口连通性；不得把它与当前隧道延迟混合排序后自动切换
- [ ] 连接状态下候选入口测速绕过当前 TUN；显式绑定底层网络失败时仅 protect 的安全回退仍能测试，且不会回流进当前 VPN
- [ ] Hysteria2 / TUIC 作为当前连接节点时显示真实隧道结果；未连接候选节点不伪造 TCP 延迟
- [ ] 故意填入无效目标节点 / 配置，热切换失败后仍保持原节点与原模式，UI 和通知同步回滚
- [ ] 故意使用“端口可达但无法联网”的节点，健康检查拒绝假连接；热切换自动回滚
- [ ] “我的 → 连接诊断”能区分正常、DNS 失败和代理出口超时

## DNS、IPv6 与泄漏

- [ ] 智能分流：国内域名由直连 AliDNS DoH 解析，非国内域名由经代理的 Google DoH 解析
- [ ] 全局代理：普通域名解析经代理 DoH；节点域名仍可通过独立加密引导解析建立连接
- [ ] 浏览器与常用 DNS 检测站未显示运营商明文 DNS
- [ ] IPv6 可用网络上只返回/使用代理支持的 IPv4，不暴露本地运营商 IPv6，也不因 AAAA 长时间卡死
- [ ] Android 私人 DNS 开 / 关两种状态均可上网；智能分流策略符合预期
- [ ] 网页 QUIC（UDP/443）被快速拒绝并正常回落 TCP/TLS；其他 UDP、视频、语音与即时通信可用

## 协议兼容

- [ ] VLESS：TCP / WS / HTTP Upgrade / gRPC，TLS / REALITY / uTLS
- [ ] Trojan：TLS / REALITY 与常见 transport
- [ ] VMess：标准 Base64 JSON 分享链接及常见 transport
- [ ] Shadowsocks：SIP002、AEAD / 2022 方法；如使用插件，分别验证 obfs-local / v2ray-plugin
- [ ] Hysteria2：普通端口、多端口、OBFS
- [ ] TUIC：常用拥塞控制、UDP relay 与 0-RTT 参数
- [ ] AnyTLS：TLS、SNI、证书校验参数
- [ ] 不支持或格式错误的节点显示明确中文提示，且不会泄露密码、UUID、Token 或完整分享 URI

## Panel 与异常路径

- [ ] `/traffic/report` 连接后按 bootstrap 间隔串行上报，正常断开与热切换会封存上一会话
- [ ] Panel 今日 / 本月 / 累计流量在刷新后更新
- [ ] Panel 停用当前节点后，App 安全断开并刷新节点
- [ ] Token 失效 / 账户停用后安全断开并要求重新登录
- [ ] 无网、DNS 不可达、错误证书、错误 REALITY 公钥均显示可理解的失败信息
- [ ] 通知权限关闭时 VPN 仍可启动，“我的 → 连接状态通知”可打开系统设置
- [ ] Panel 更新接口超时/5xx 时回退 GitHub；Panel 明确 `enabled=false` 时不绕过策略
- [ ] Android 系统 Always-on 控件不可用，普通前台连接与后台保持正常

## 正式版发布门槛

- [ ] 上述真机项目全部通过，至少连续运行 24 小时无崩溃或 VPN 回环
- [ ] 保存 GitHub Actions 日志、APK SHA-256、签名证书摘要与测试记录
- [ ] 仅在完成验收后创建 GitHub `v1.0.1` Release，并继续使用相同固定签名

失败记录至少包含：手机型号、Android 版本、浅色/深色、网络类型、节点协议、分流模式、操作时间、界面提示、通知状态与脱敏后的 `adb logcat -s XVPN-Core`。不得提交节点 URI、UUID、密码、Token、私钥或完整服务器配置。
