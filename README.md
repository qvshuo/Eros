# Eros

刮削视频或 STRM 的 JAV 元数据，由 Emby 插件读取。

## 部署

```sh
# 克隆此仓库
git clone --depth=1 https://github.com/qvshuo/Eros.git && cd Eros

# 复制配置并创建数据目录
cp docker-compose.example.yml docker-compose.yml
mkdir -p data

# 修改 docker-compose.yml 中的作品目录挂载路径后启动
docker compose up -d --build
```

打开 `http://服务器IP:9307`。

## 使用

1. 设置作品扫描目录并执行刮削。Eros 按视频或 STRM 所在目录名（番号）获取元数据，将 NFO 和原图保存到 `data/metadata`。

2. 下载 [Eros.dll](emby-plugin/Eros.dll?raw=true)，放入 Emby 插件目录，升级时先移除旧版插件 DLL。Docker 部署时，若配置挂载为 `/path/to/emby/config:/config`，则放入 `/path/to/emby/config/plugins/Eros.dll`。

3. 在 Emby 的 Compose 中增加以下挂载，将 `/path/to/Eros` 替换为 Eros 的宿主机绝对路径：

   ```yaml
   volumes:
     - /path/to/Eros/data/metadata:/eros-metadata:ro
   ```

4. 重启 Emby，在插件中确认元数据目录为 `/eros-metadata`，然后刷新媒体库。
