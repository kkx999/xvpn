# XVPN Android API v1

Base path: `/api/v1`

## 核心原则

Panel 不向 Android 下发 sing-box 配置。节点由 Panel 标准化为 `xvpn.node.v1`，Android 直接根据 `profile` 生成 Mihomo 配置。

## 主要接口

- `GET /health`
- `POST /register`
- `POST /login`
- `POST /logout`
- `GET /me`
- `POST /change-password`
- `GET /nodes`
- `GET /app/bootstrap`
- `POST /traffic/report`
- `GET /traffic/summary`
- `GET /app/update`

## 节点响应

```json
{
  "ok": true,
  "schema": "xvpn.nodes.v1",
  "node_schema": "xvpn.node.v1",
  "core": "mihomo",
  "revision": "节点目录修订号",
  "generated_at": "服务端生成时间",
  "countries": [
    {
      "country": "香港",
      "country_code": "HK",
      "nodes": [
        {
          "id": 1,
          "name": "香港01",
          "protocol": "vless",
          "profile": {
            "schema": "xvpn.node.v1",
            "protocol": "vless",
            "server": "hk.example.com",
            "port": 443,
            "auth": {"uuid": "..."},
            "tls": {"enabled": true, "server_name": "example.com"},
            "transport": {"type": "tcp"},
            "options": {}
          }
        }
      ]
    }
  ]
}
```

支持的标准节点协议：VLESS、VMess、Trojan、Shadowsocks、Hysteria2、TUIC、AnyTLS。

## 同步、流量与版本策略

- `GET /app/bootstrap` 返回节点修订号、流量摘要和 `traffic_report_interval_seconds: 10`。
- `POST /traffic/report` 使用设备、会话、节点和累计计数器实现幂等重试。
- Android 发送 `X-XVPN-Version-Name` 与 `X-XVPN-Version-Code`；低于最低版本时，受保护接口返回 HTTP 426 和完整更新元数据。
- `GET /app/update` 返回 APK URL、大小、SHA-256、版本号、最低版本与强制升级状态。
