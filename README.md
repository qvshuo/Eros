# Eros

刮削视频与 STRM 的 JAV 元数据，保存为外置 NFO 和原图，由 Emby 插件读取。

## 部署

下载仓库后，在项目目录执行：

```sh
cp compose.example.yml compose.yml
mkdir -p data
sudo chown 1000:1000 data
```

编辑 `compose.yml` 的媒体挂载路径，确保 UID 1000 可读，然后启动：

```sh
docker compose up -d --build
```

打开 `http://服务器IP:9307`，在设置中选择作品扫描目录；元数据默认保存到 `data/metadata`。翻译、Cookie、数据源等均在页面配置。FlareSolverr 默认地址为 `http://flaresolverr:8191/v1`，遇到验证墙时自动使用；留空关闭。

## 使用

每部作品一个目录，目录名为完整番号，如 `SSIS-997`、`300MIUM-001`、`FC2PPV-1234567`。Eros 不改名，不修改媒体或 STRM 内容。

在媒体库选择作品，执行补齐缺失、重新抓取或指定来源。目录监控只更新清单，不自动刮削。支持 DMM、JavDB、JavBus、FC2、AVSOX；JavDB 先 API 后网页。

翻译仅处理标题和介绍，要求模型转换为简体中文并保留人物姓名；演员按映射统一为日文主名。元数据与媒体文件同名，图片保留原格式；删除媒体不自动删除元数据。

## Emby

下载 [Eros.Emby.dll](emby-plugin/Eros.Emby.dll?raw=true)，放入 Emby 插件目录。

在 Emby 的 Compose 中将 Eros 的 `data/metadata` 挂载为 `/metadata:ro`、`data/state` 挂载为 `/eros-state:ro`，确保 Emby 运行用户可读。通过原 Compose 重启，在插件中选择 `/metadata` 后刷新媒体库。
