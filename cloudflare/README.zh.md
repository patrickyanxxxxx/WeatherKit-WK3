# 将雷达 Worker 部署到你自己的 Cloudflare

每位使用者在**自己的 Cloudflare 账户**部署独立 Worker，并将**自己的 VPS** 配置为计算后端。模板没有默认后端，也不会回退到项目维护者的服务器。

```text
你的代理软件（雷达增强模块）
  ├─ 空气质量、分钟降水、预警等 → 正式天气脚本在代理本地执行
  └─ 地图雷达 → 你自己的 Worker → 你自己的 VPS 计算
```

## 在 Cloudflare 部署页创建

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https%3A%2F%2Fgithub.com%2Fpatrickyanxxxxx%2FWeatherKit-WK3%2Ftree%2Ffeat%2Fcloudflare-radar%2Fcloudflare)

1. 先按 [VPS 安装说明](https://github.com/patrickyanxxxxx/WeatherKit-WK3/blob/feat/cloudflare-radar/radar/README.zh.md) 在**你的 VPS** 安装雷达计算服务，并配置自己的 HTTPS 域名。
2. 点击上方部署按钮，登录**你自己的 Cloudflare 账户**，按 Cloudflare 提示将此模板复制到你自己的 Git 仓库，再创建 Worker。此 `cloudflare/` 目录是独立项目，不需要安装整个天气仓库的依赖。
3. 在部署页的 Worker 变量／Secret 配置中填写：

   | 配置项 | 类型 | 你需要填写的值 |
   | --- | --- | --- |
   | `VPS_ORIGIN` | 文本变量 | 你自己的 VPS HTTPS origin，例如 `https://radar.example.com`，不带 `/v1/radar`、查询参数或账号密码 |
   | `VPS_TOKEN` | Secret | 你自己的 VPS 雷达服务所用的 Bearer 密钥，与服务端 `VPS_TOKEN` 一致 |

   如果部署页面未显示配置项，部署后进入该 Worker 的 **Settings → Variables and Secrets** 添加以上两项并重新部署。`VPS_TOKEN` 不是 Cloudflare API Token，不放入代理模块、仓库或 URL。默认 `VPS_ORIGIN` 为空，未配置时 `/health` 返回 503、`configured:false`，雷达请求不会访问任何服务器。
4. 使用 Cloudflare 为**你自己的 Worker** 分配的 `https://<你的Worker>.<你的子域>.workers.dev`，或绑定自己的域名。
5. 将雷达增强模块的 `RADAR_ENDPOINT` 填为 **`https://<你自己的Worker域名>/v1/radar`**。增强模块与普通天气模块二选一启用，保留个人天气参数。

后续更换 VPS，只需在自己的 Worker 中修改 `VPS_ORIGIN` 和 `VPS_TOKEN`；更换 Worker 域名，再修改模块的 `RADAR_ENDPOINT`。不需要项目维护者为每位用户代部署，也不共用维护者的 VPS。外部 VPS 通过 HTTPS 接入，不属于 Cloudflare Service Binding。

也可在 Cloudflare 控制台手动创建 Worker：将本目录 `worker.mjs` 粘贴到模块脚本编辑器部署，然后按上表设置变量与 Secret。无需运行本地命令或提供 Cloudflare API Token。

## 验证与请求行为

访问你自己的 Worker `/health`，`configured:true` 仅表明 VPS origin 格式已配置，不保证实际回源可达。真实雷达请求冷启动会返回 202；VPS 准备完整七帧后，后续请求可返回 200、`image/ktx` 和 65,604 字节。缺测、过期、未知运动、202 或错误时，模块保留 Apple 原始响应。

Worker 只转发严格校验的 `z/x/y/scale/frameTime` 五个雷达参数，以及它自己保存的 VPS Bearer 密钥，不转发 Apple Cookie、Authorization 或其他天气数据。计算算法仍运行于 VPS；当前输出为 0–60 分钟实验性回波出现／未出现图层，未标定 mm/h，也未完成大陆多事件预测技巧和所有客户端真机验收。

## 可选命令行部署

如果希望用 CLI，先在自己的环境中设置 `CLOUDFLARE_API_TOKEN` 和自己的 `VPS_TOKEN`，再运行：

```sh
python3 cloudflare/deploy.py \
  --account YOUR_ACCOUNT_ID --name wk3-radar \
  --origin https://YOUR_VPS_DOMAIN
```

API Token 只用于向你自己的 Cloudflare 账户部署。VPS 密钥写入 Worker Secret binding。使用 Wrangler 时先填写本目录 `wrangler.jsonc` 中的 `VPS_ORIGIN`，密钥通过 `wrangler secret put VPS_TOKEN` 设置；不要把密钥写入 vars。

官方说明：[Deploy to Cloudflare](https://developers.cloudflare.com/workers/platform/deploy-buttons/) · [Workers 限制](https://developers.cloudflare.com/workers/platform/limits/)。
