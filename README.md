# Eros

刮削视频与 STRM 的 JAV 元数据，由 Emby 插件读取。

## 部署

```sh
cp docker-compose.example.yml docker-compose.yml
mkdir -p data
# 修改 docker-compose.yml 的挂载路径
docker compose up -d --build
```

打开 `http://服务器IP:9307`。

## 使用

按视频或 STRM 所在目录的名称（番号）刮削，将 NFO 和图片保存到 `data/metadata`，由 Emby 插件加载。

下载 [Eros.Emby.dll](emby-plugin/Eros.Emby.dll?raw=true)，放入 Emby 插件目录。

在 Emby 的 Compose 中将 `data/metadata` 挂载为 `/metadata:ro`、`data/state` 挂载为 `/eros-state:ro`。重启 Emby，在插件中选择 `/metadata` 后刷新媒体库。
