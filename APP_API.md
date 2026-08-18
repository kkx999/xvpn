# VPN Panel Android App API v1 — v1（Android 开发锁定稿）

Base URL：`https://<Panel域名>/api/v1`

Android App 使用普通用户 Bearer Token。

## 1. 启动 / 公共配置

### GET `/`
无需登录。用于确认 API 可用、注册入口是否开启以及新 Token 的默认有效期。

返回示例：

```json
{
  "ok": true,
  "service": "VPN Panel",
  "api": "v1",
  "version": "1.0.0",
  "registration_enabled": true,
  "token_days": 30,
  "app_api_ready": true
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
  "version": "1.0.0",
  "server_time": "2026-08-18T06:00:00+00:00",
  "registration_enabled": true,
  "user": {
    "id": 1,
    "username": "user01",
    "status": "active"
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

## 10. App 流量统计

Panel 不设流量额度，也不限制流量。App 的“今日 / 本月 / 累计上传下载”由 Android VPN/Core 本地统计即可，不需要上传 Panel。

## 11. 当前产品规则

- 邀请制注册，无邮箱。
- 一个有效账号 = 全部启用节点使用权。
- 无套餐、余额、订单、订阅链接、流量额度、到期套餐、节点授权组。
- 用户只有 Android App 入口；网站仅供管理员。
