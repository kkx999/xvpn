# Third-party notices

## sing-box / libbox 1.13.19

This 1.0.1 source package includes `app/libs/libbox-1.13.19-arm64.aar`, containing the Android/arm64 libbox native library and its generated Java bindings.

- Upstream project: https://github.com/SagerNet/sing-box
- Android client: https://github.com/SagerNet/sing-box-for-android
- Release: https://github.com/SagerNet/sing-box/releases/tag/v1.13.19
- Corresponding source tag: https://github.com/SagerNet/sing-box/archive/refs/tags/v1.13.19.tar.gz
- Upstream source revision: `b5ebaa1fc0f2b94256180b95468e73ef53caa27d`
- Official Android client tag revision: `aed2b6ea126e627fca2c6189a944000dcf5315b5`
- Official `SFA-1.13.19-arm64-v8a.apk` SHA-256: `6e07580500a0e74881d7d8fc3b4ef9d88dce8e65c8d44a4a41be839fbaeaeaf5`
- Bundled AAR SHA-256: `9b73fc5fb5591e9b0f269ae04685daa9ece38e95452454bf642f8140fa769ca8`
- Bundled `libbox.so` SHA-256: `a54962a1ecc2f3d6b2311903dc80993d7aa7f5327d5bbb06a61899e420326aaa`

The bundled AAR was assembled from the native library and generated bindings in the official SFA 1.13.19 arm64 release APK. The APK-generated `go.libbox.gojni.R` class is intentionally excluded because Android Gradle Plugin generates the AAR resource class from its namespace; bundling both copies causes a duplicate-class DEX failure. It is not claimed as an XVPN-authored component.

sing-box is distributed under GNU GPL version 3 or later. Upstream also states that derivative works may not use its application name or imply association without prior consent. This client uses the independent XVPN name and does not imply upstream endorsement or association.

The full GPL-3.0 text is in `licenses/GPL-3.0.txt`; the upstream project notice is in `licenses/sing-box-LICENSE.txt`.

## SagerNet routing rule sets

XVPN 1.0.1 bundles two official sing-box binary rule sets so Smart Routing is available on the first connection and does not depend on a background rule download:

- `app/src/main/assets/rules/geosite-geolocation-cn.srs`
  - Upstream: https://github.com/SagerNet/sing-geosite
  - Pinned revision: `b3e5c6a15dd82d367ba45cc8c03c81b6fc6b7792`
  - SHA-256: `9cccc08ff669d707d7662ed78cc0a3b2626c4f16a6d151a9167dadb44d3da7b8`
- `app/src/main/assets/rules/geoip-cn.srs`
  - Upstream: https://github.com/SagerNet/sing-geoip
  - Pinned revision: `b9c5e675b4d5359d4b47f4434fa7ae77e9991306`
  - SHA-256: `0acf5dad38fba9db2dade29ce5e4edc6902220944f30628ae46ed16cb0ec5edd`

Both rule repositories are distributed under GNU GPL version 3 or later. The bundled GPL-3.0 text applies to these data files as well.
