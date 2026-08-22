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
