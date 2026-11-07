# 雷达计算服务自托管

此目录仅包含独立雷达计算服务、调色板、测试及部署模板，不包含旧 native-proxy，也不承接普通天气、空气质量或预警请求。运行依赖只有 Python 标准库、NumPy、Pillow；Debian 使用发行版 apt 包，无新增 pip 项目依赖。

输出是实验性回波出现/未出现图层，非标定降水强度。RainViewer 只提供历史观测，未来 0–60 分钟由本项目外推。链路成功不证明一小时预测技巧。来源署名保留在响应头和 /status。

## 普通主机

```sh
sudo apt-get update
sudo apt-get install python3 python3-numpy python3-pil ca-certificates
python3 native_gateway.py
```

默认只监听 `127.0.0.1:8768`，不带 `--token-env` 时保持原本本机无令牌行为。`--host` 可显式修改监听地址。`--cache` 和 `--source-cache` 分别指定生成缓存与公开源缓存。

公网部署必须通过 HTTPS 反代，并添加 `--token-env VPS_TOKEN`；令牌只从该环境变量读取，不接受命令行明文令牌。启用后所有 GET（包括 /status）必须携带 `Authorization: Bearer <token>`；缺失/错误返回 401，指定的环境变量缺失/为空时程序启动失败。

## Debian systemd

在此目录运行 `sudo sh deploy/install-systemd.sh`。脚本安装源码至 `/opt/radar-demo`，建立无登录 `radar-demo` 用户，生成 `/etc/radar-demo/token.env`（root:root 0600，只在文件不存在时生成），启用 `radar-demo.service`。服务只写 `/var/lib/radar-demo`，源码归 root、不可由服务用户修改；8768 仅 loopback。

```sh
systemctl is-active radar-demo
journalctl -u radar-demo --no-pager -n 30
cd /opt/radar-demo
python3 -m unittest -v test_native_gateway test_deployment test_mosaic test_apple_texture test_motion_v2 test_motion_v3
```

Nginx 模板须按实际反代的配置目录及证书路径修改后安装。先只安装 HTTP challenge 站点、用 acme.sh webroot 签专属证书，再安装 HTTPS 站点；每次均先对实际运行的 Nginx 执行 `nginx -t`，通过后 reload。保留既有默认站点和证书，禁用雷达代理缓存、保留上游 202/200 和 X-WK3 响应头。

模板中的 `radar.example.com` 必须替换为**你自己的 VPS 域名**；证书、challenge webroot 和配置安装目录也要匹配你的服务器。若 Nginx 运行于容器，应修改容器实际挂载的站点文件，在容器内检查配置并 reload；若原生运行，则使用该进程实际加载的配置目录。

签发证书使用你自己的域名和 ACME 账户；`deploy/radar-demo-renew.{service,timer}` 是可选续签模板，安装前替换域名并核对 acme.sh 路径。项目不要求使用维护者的服务器、证书或账户。

## Docker Compose

先在本目录生成独立本地环境文件，不要把文件加入仓库：

```sh
umask 077
python3 -c 'import secrets; print("VPS_TOKEN="+secrets.token_urlsafe(48))' > radar.env
docker compose up -d --build
```

Dockerfile 使用 Debian 自带 Python + apt NumPy/Pillow，以非 root 用户运行；容器内部 `--host 0.0.0.0`，Compose 只映射主机 `127.0.0.1:8768:8768`。Compose 默认强制 Bearer，并用持久卷保存缓存。systemd 与 Compose 二选一，不同时占用 8768。公网 HTTPS 反代可复用模板，容器应保持 loopback 端口映射。

Compose 语法已在 VPS 用 `docker compose config --quiet` 验证；本次实际运行的是 systemd，未运行 Docker 镜像构建验收。

## Worker 配置与验证

在**你自己的 Cloudflare 账户**部署 Worker，然后设置 `VPS_ORIGIN=https://你的VPS域名`。在该 Worker 的 Variables and Secrets 中添加 Secret `VPS_TOKEN`，值应与你自己 VPS `/etc/radar-demo/token.env` 中的 `VPS_TOKEN` 一致。部署按钮与操作步骤见 [用户自部署指南](../cloudflare/README.zh.md)。不要打印值，不要提交 env 文件，不要把令牌放到客户端模块 URL 或查询参数。Worker 到你自己的 VPS 发送 Bearer；模块的 `RADAR_ENDPOINT` 使用你自己 Worker 的 `/v1/radar`。

查询仅允许 `z,x,y,scale,frameTime` 五字段，`z=0..7`、`scale=2`，时间必须带 UTC offset 且按五分钟对齐。冷 tile 返回 202 和 Retry-After；完整七帧准备后返回 200、`image/ktx`、65,604 字节。缺测/未知运动不发布不完整预测。

可选定向验证：在自己的 VPS 上先设置 `VPS_ORIGIN=https://你的VPS域名`，再运行 `deploy/verify-live.py`。脚本仅供 root 运行：读取本机令牌后立即降权，随机无放回从已声明 18 城抽 8 城，保留全部抽中记录，单 worker 有界构建一个合适的真实回波 tile。脱敏结果保存抽样统计、响应状态／头和尺寸，不保存令牌或二进制 tile，写入 `/var/lib/radar-demo/deployment-result.json`。在 VPS 上访问自身域名可能走本地路由，不能当作外部客户端或 Worker 回源成功证据。

如果你自己的 VPS 已配置 WARP SOCKS5，可用 `python3 deploy/verify-warp.py 'https://你的VPS域名/v1/radar?有效瓦片参数'` 做指定 URL 的外部路径复验；该脚本不会自动选择后端或固定历史时间。最终仍需用你自己的 Worker 与代理模块实际测试，`/health` 的配置状态不能代替雷达 200 图层验收。
