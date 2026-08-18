# XVPN Panel Android App API v1 — v1（Android 开发锁定稿）

Base URL：`https://<Panel域名>/api/v1`

Android App 使用 Bearer Token。普通用户与管理员均可登录；流量统计接口仅接受普通用户 Token。

## 1. 启动 / 公共配置

### GET `/`
无需登录。用于确认 API 可用、注册入口是否开启以及新 Token 的默认有效期。

返回示例：

```json
{
  "ok": true,
  "service": "XVPN Panel",
  "api": "v1",
  "version": "1.2.0",
  "registration_enabled": true,
  "token_days": 30,
  "app_api_ready": true,
  "traffic_reporting": true,
  "traffic_report_interval_seconds": 300,
  "traffic_report_requires_node_id": true,
  "panel_timezone": "Asia/Shanghai"
}
```

### GET `/health`
健康检查。无需登录。

## 2. 注册

### POST `/register`

```json
{
  "invite_code": "朋友专用",
  "username": "user01",
  "password": "至少8位密码"
}
```

成功：HTTP 201

```json
{"ok":true,"message":"注册成功"}
```

常见错误：`REGISTRATION_CLOSED`、`INVALID_INVITE`、`INVALID_USERNAME`、`USERNAME_EXISTS`、`WEAK_PASSWORD`、`RATE_LIMITED`。无效邀请码连续尝试过多时会临时限流。

## 3. 登录

### POST `/login`

```json
{
  "username": "user01",
  "password": "密码"
}
```

成功返回：

```json
{
  "ok": true,
  "token": "Bearer Token",
  "expires_at": "2026-09-17T06:00:00+00:00",
  "user": {"id": 1, "username": "user01"}
}
```

App 将 `token` 安全存储，并在后续请求发送：

```http
Authorization: Bearer <token>
```

登录接口有基础防爆破限制。超过限制返回 HTTP 429 / `RATE_LIMITED`，并带 `retry_after` 秒数。

## 4. App 一次性启动数据

### GET `/app/bootstrap`
Bearer Token 必需。推荐 App 登录成功后以及冷启动时优先调用此接口。

返回：

```json
{
  "ok": true,
  "api": "v1",
  "version": "1.2.0",
  "server_time": "2026-08-18T06:00:00+00:00",
  "registration_enabled": true,
  "user": {
    "id": 1,
    "username": "user01",
    "status": "active",
    "role": "user"
  },
  "traffic_reporting": true,
  "traffic_report_interval_seconds": 300,
  "traffic_report_requires_node_id": true,
  "panel_timezone": "Asia/Shanghai",
  "traffic": {
    "today_upload": 1048576,
    "today_download": 5242880,
    "month_upload": 10485760,
    "month_download": 52428800,
    "total_upload": 104857600,
    "total_download": 524288000,
    "last_report_at": "2026-08-19T02:00:00+00:00",
    "device_count": 1,
    "timezone": "Asia/Shanghai",
    "day": "2026-08-19",
    "month": "2026-08"
  },
  "nodes": {
    "total": 2,
    "countries": [
      {
        "country": "日本",
        "country_code": "JP",
        "flag_emoji": "🇯🇵",
        "sort_order": 10,
        "nodes": [
          {
            "id": 2,
            "name": "日本02",
            "display_name": "日本02",
            "country": "日本",
            "country_code": "JP",
            "region": "",
            "protocol": "vless",
            "config": "vless://...",
            "sort_order": 1
          }
        ]
      }
    ]
  }
}
```

**排序规则已经由 Panel 决定。Android 必须按 `countries` 和各自 `nodes` 的返回顺序直接渲染，不要再次按名称排序。**

`config` 是连接核心使用的数据，不在 App UI 中展示或提供复制/导出入口。

## 5. 节点刷新

### GET `/nodes`
Bearer Token 必需。只返回后台当前所有“启用”节点。节点停用、恢复、删除、重命名和国家排序会在下一次请求时实时反映。

正常账号没有套餐或节点权限差异：所有正常账号获取全部启用节点。

## 6. 用户状态

### GET `/me`
Bearer Token 必需。

```json
{"ok":true,"user":{"id":1,"username":"user01","status":"active"}}
```

## 7. 修改密码

### POST `/change-password`
Bearer Token 必需。

```json
{
  "current_password": "旧密码",
  "new_password": "新密码"
}
```

成功后：
- 该用户之前的所有 Token 立即失效；
- 接口直接返回一个新的 Token；
- Android 应原子替换本地 Token，不需要用户重新输入账号密码。

## 8. 退出登录

### POST `/logout`
Bearer Token 必需。只撤销当前 Token。

## 9. Android 必须统一处理的鉴权错误

- `UNAUTHORIZED` / HTTP 401：当前 Token 无效。断开 VPN、清除运行时节点配置和本地 Token，返回登录页。
- `TOKEN_EXPIRED` / HTTP 401：Token 到期。处理同上，并提示“登录已过期，请重新登录”。
- `ACCOUNT_DISABLED` / HTTP 403：管理员停用账号。立即断开 VPN、清除 Token 和运行时节点配置，返回登录页并提示“账户已停用”。
- `RATE_LIMITED` / HTTP 429：不要继续快速重试，按 `retry_after` 做倒计时。

## 10. Android → Panel 流量上报

Panel 不设置流量额度，也不做节点侧计费；流量来源仍然是 Android VPN/Core。普通用户 App 把**累计计数器 + 当前节点 ID + 会话 ID**上报给 Panel，由 Panel 计算增量，并同时汇总“今日 / 本月 / 累计用户流量”和“今日节点流量”。

### POST `/traffic/report`
普通用户 Bearer Token 必需；管理员 Token 会返回 `TRAFFIC_USER_REQUIRED`。

请求示例：

```json
{
  "device_id": "7d0b61f4-9d6d-4c51-a4c6-4de9b6b3a021",
  "session_id": "dbf25c58-172c-4c0e-98c5-f556bcfbfa70",
  "node_id": 2,
  "upload_total_bytes": 123456789,
  "download_total_bytes": 987654321,
  "app_version": "1.0.0"
}
```

规则：

- `device_id` 应由 App 首次安装时生成随机 UUID 并持久化，**不要使用 IMEI、Android ID 等硬件标识**。
- `session_id` 每次 VPN/Core 新连接会话生成新的随机 UUID；切换节点后应创建新会话。
- `node_id` 必须直接使用 `/nodes` 或 `/app/bootstrap` 返回的节点整数 `id`，不要用节点名称代替。
- `upload_total_bytes` / `download_total_bytes` 是**当前 VPN 会话**累计值，不是“这 5 分钟的增量”。
- **连接成功后应立即上报一次基线**（通常计数器接近 0），之后每 5 分钟上报，并在断开前补报一次；这样不会漏掉会话开始后的第一段流量。
- 同一设备的流量上报应串行发送；切换节点时先等待旧会话最后一次上报完成，再发送新会话 baseline，避免网络乱序让旧会话覆盖新 baseline。
- Panel 只计算“同一 device_id + session_id + node_id 的本次累计值 - 上次累计值”，所以同一请求因网络重试重复发送不会重复计流量。
- 节点切换、新会话、计数器回退时只建立新基线，该次 `delta` 为 0，避免把旧节点流量错误归到新节点或产生负数/巨大异常流量。
- `node_id` 不存在时返回 `INVALID_NODE_ID`；Android 应刷新节点列表，不要盲目重试旧节点 ID。
- 超过 180 天没有上报的设备基线会自动清理；这不会删除已聚合的每日 / 累计流量。
- 每日统计按 Panel 后台配置的 `panel_timezone` 日期边界聚合；跨本地 00:00 的少量误差最多约等于一个上报周期。

成功返回示例：

```json
{
  "ok": true,
  "accepted": true,
  "baseline_reset": false,
  "server_time": "2026-08-19T02:00:00+00:00",
  "node": {"id": 2, "name": "日本02"},
  "delta": {"upload_bytes": 1024, "download_bytes": 4096},
  "traffic": {
    "today_upload": 1024,
    "today_download": 4096,
    "month_upload": 1024,
    "month_download": 4096,
    "total_upload": 1024,
    "total_download": 4096,
    "last_report_at": "2026-08-19T02:00:00+00:00",
    "device_count": 1,
    "timezone": "Asia/Shanghai",
    "day": "2026-08-19",
    "month": "2026-08"
  }
}
```

### GET `/traffic/summary`
普通用户 Bearer Token 必需。返回 Panel 当前保存的今日 / 本月 / 累计上传下载。`/app/bootstrap` 也会携带同一个 `traffic` 对象，Android 冷启动时无需额外请求即可同步服务器统计。

> 这套数据是客户端上报统计，适合后台查看和跨设备汇总；它不是节点服务端计费数据，不能作为防篡改流量计费依据。

## 11. 当前产品规则

- 邀请制注册，无邮箱。
- 一个有效账号 = 全部启用节点使用权。
- 无套餐、余额、订单、订阅链接、流量额度、到期套餐、节点授权组。
- 用户只有 Android App 入口；网站仅供管理员。


## 管理员 App 登录

管理员后台账户也可以直接调用 `POST /api/v1/login` 登录 Android App。成功后 `user.role` 为 `admin`；普通用户为 `user`。管理员 App 登录同样使用 Bearer Token，可读取所有启用节点。管理员在后台修改用户名/密码后，已有管理员 App Token 会立即失效。
