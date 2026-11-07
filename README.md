#  iRingo: 🌤 WeatherKit

基于 [NSRingo/WeatherKit](https://github.com/NSRingo/WeatherKit) **v3.3.2 / `main`（`c40e8f8`）**，面向 iOS 27 的 WK3 天气接口增加代理软件本机处理，并保留上游 WK2 入口。本分支还提供可自行部署的二维雷达回波外推实验模块。

普通天气功能在代理软件内执行，按所选来源请求 Apple、彩云或和风数据；雷达增强功能需要用户自己的 Cloudflare Worker 和 VPS。各平台支持范围、默认设置和部署入口见下文。

## 相比上游新增与改进

以下以所基于的上游 `main` 为对比范围；上游已有的数据源和空气质量算法在本分支继续复用，新增重点是 WK3 编解码、本机运行适配及雷达扩展。

| 功能 | 本分支提供的能力 |
| --- | --- |
| WK3 二进制处理 | 增加 WK3 FlatBuffer 根表及空气质量、分钟降水、昨日比较和预警的编解码；只修改已验证的数据结构，保留其他天气数据槽与未知字段。 |
| WK3 空气质量替换 | 对命中配置的原始标准执行同标准 AQI 更新，或将国标转换为彩云美标；完整美标替换同步指数、污染物、单位、首要污染物和来源信息，同标准原位更新保留原污染物与昨日比较。 |
| 大陆及香港缺失 AQ 补入 | Apple 原响应没有 AQ 子表时，按地区和配置条件补入完整彩云美标空气质量；大陆路径要求请求与设置启用 AQ、替换范围命中大陆、今日来源为 `ColorfulCloudsUS` 且指数替换目标非空。香港采用独立补入条件；数据不完整或请求失败时保留原响应。 |
| WK3 分钟级降水 | 将彩云或和风的分钟降水填入已验证的 WK3 未来一小时降水结构，在 Apple 天气原生界面展示；选择 `WeatherKit` 时保留原数据。此功能与二维雷达地图分别配置。 |
| WK3 昨日空气等级比较 | 按同来源、同标准补充“比昨天更好／更差／相近”等等级比较；遵循昨日来源与变化时替换开关，不兼容或缺少数据时保留原值或返回未知。 |
| WK3 原生预警补全 | 使用和风天气网页补全 Apple 已有预警的标题、发布机构、正文与详情；保留 Apple 原事件 UUID 和原生详情入口，只处理严格唯一匹配的事件，失配时保留原信息。 |
| 空气质量标尺 | 按所需标准与语言在本机构建受支持的 AQ 标尺，供空气质量原生展示使用；上游已有的标尺能力接入本机脚本入口。 |
| Apple 能力信息 | 合并 Apple availability 与模块支持能力，保留未知字段、原错误状态及语言响应头，避免固定能力列表覆盖原响应。 |
| 多平台代理本机模块 | Egern、Surge、Loon、Stash、Quantumult X 共用 WK3 业务核心、参数和路由，分别生成客户端专用脚本与模块；Quantumult X 的二进制处理限制见平台表。 |
| 二维雷达实验扩展 | 增加 RainViewer 历史回波取源、邻域拼图、运动估计、0–60 分钟外推及 Apple KTX 瓦片输出，通过自有 Worker 和 VPS 接入原生降水地图；支持四款代理的独立雷达模块，详见雷达段落。 |

### 空气质量算法与配置

- 复用上游 **12 个算法选项（含 `None`，即 11 种计算算法）**：UBA、EU EAQI、美标／国标／国标草案 InstantCast、加拿大／香港 AQHI、AQHI-Multi 及致死风险 AQHI 系列。内置算法默认 `WAQI_InstantCast_US`，仅在今日指数来源选择 `Calculate` 时运行；WK3 只写入已支持的目标标准。
- 校正美标浓度截断与超量程连续性、加拿大 AQHI 单位与四舍五入、国标草案零浓度；CO 换算可重复调用且不修改原输入，缺少必要污染物或无有效分项时不生成有效指数。
- 今日与昨日来源分别配置；空替换目标、`None` 和 `WeatherKit` 均按选择执行。只有候选有效且指数实际变化时，才按开关重算已有比较；避免无必要的 AQ 预取。
- 五平台提供统一的 **23 项天气参数**，包括数据集、替换范围、来源、算法、污染物单位、昨日比较、API 凭据、超时和日志等级。模块标签与说明沿用上游习惯，并注明仅 WK2 生效或 WK3 尚不支持的设置。

### 客户端适配、日志与构建

- Loon 按准确字节范围回传原生 `Uint8Array`；Surge／Loon／Stash 按客户端要求返回请求与响应修改对象，支持空数组等参数形式，并使用实际解析后的超时和日志设置。
- Surge／Loon／Stash／Quantumult X 的预警详情兼容普通响应头大小写，并提供局部 Base64 编解码；Egern 使用其原生上下文。五类路由统一维护，避免原生预警详情重复匹配。
- 五平台通过 `console.log` 输出脱敏后的单字符串。`INFO` 显示开始、处理摘要和结束，`DEBUG`／`ALL` 展示更多步骤，`WARN`／`ERROR` 显示回退或异常，`OFF` 静默；日志不明文输出令牌和完整私人配置。
- 增加共享核心、平台代理、算法边界、真实抓包回放与模块构建测试；CI 在构建前执行回归。版本化构建生成 **10 份平台专用 JS 和 5 份普通 WK3 模块**，纳入发行附件；雷达增强版另有独立构建入口。
- 保留上游 WK2 的天气来源选择和处理能力；WK3 支持范围由已验证的编解码决定，详见文末限制。

## 模块适配状态

| 软件 | 模块格式 | 当前验证范围 |
| --- | --- | --- |
| Egern | `.yaml` | 用户已确认 Egern 测试版大陆缺 AQ 补入生效；正式模块引用同版本附件，预警实现保持原样。 |
| Surge | `.sgmodule` | 独立 JS 构建、参数传递与合成 WK3 AQ 响应端到端回放通过（AQI 60→87）；未设备验证。 |
| Loon | `.lpx` | 用户已确认测试版 2（`3.3.5-test-loon-binary.2-aq.1`）实机可用；对应修复已纳入 v3.3.6。 |
| Stash | `.stoverride` | 独立 JS、配置解析、参数与二进制响应模拟通过；未设备验证。 |
| Quantumult X | `.snippet` | 请求/文本路径及脚本模拟通过；**WK3 二进制天气响应只透传，不作转换**；模块无可交互的参数选择界面。 |
| Shadowrocket | 无 WK3 本机模块 | 上游仅提供服务端 Rewrite 模板；本分支没有经过验证的本机二进制适配，故不提供伪装可用的模块。 |

正式模块：[Egern](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases/latest/download/iRingo.WeatherKit.WK3.yaml) · [Surge](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases/latest/download/iRingo.WeatherKit.WK3.sgmodule) · [Loon](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases/latest/download/iRingo.WeatherKit.WK3.lpx) · [Stash](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases/latest/download/iRingo.WeatherKit.WK3.stoverride) · [Quantumult X](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases/latest/download/iRingo.WeatherKit.WK3.snippet)。

升级时停用测试版及其他冲突天气模块，更新整个正式模块，使五条脚本都引用 v3.3.6；保留并核对个人参数。MITM 需启用 `weatherkit.apple.com` 并信任证书。成功补入 AQ 的日志位于 `WeatherKit.api.weather.response`，包含 `WK3 missing AQ patch`。

五平台统一构建入口为 `npm run build:wk3`：调用 Egern `scripts/build-egern.mjs` 和其他平台 `scripts/build-wk3-platform.mjs`，生成 10 份平台专用 JS。打标签时添加 `-- --version <版本号>`，同时生成 5 份模块至 `dist/` 根目录；Egern 模块改为指向同版本的发行附件，与其他平台一同进入现有构建/Release 工作流。请使用发行附件中的模块；五平台脚本不能互换。

## 独立雷达实验模块

[部署到你自己的 Cloudflare](https://deploy.workers.cloudflare.com/?url=https%3A%2F%2Fgithub.com%2Fpatrickyanxxxxx%2FWeatherKit-WK3%2Ftree%2Ffeat%2Fcloudflare-radar%2Fcloudflare)。部署页填写你自己的 `VPS_ORIGIN` 和 Secret `VPS_TOKEN`，部署后将自己的 Worker 地址填入雷达模块；没有默认共享后端。

**普通 WK3 天气模块与独立雷达模块同时启用。** 空气质量、分钟雨、昨日比较和预警仍由普通模块在代理本地处理，原有天气参数和第三方凭据继续留在普通模块。雷达模块只有 `RADAR_ENDPOINT` 一项设置、一条地图请求脚本、地图域 MITM 和该域 UDP 443 拒绝规则；二维雷达外推通过自己的 Worker 交给自己的 VPS。

`npm run build:wk3:radar -- --version 3.3.6 --outdir dist-radar` 生成 Surge／Loon／Stash／Egern 的四份独立雷达模块与四份雷达脚本，不包含普通天气脚本或天气参数。`RADAR_ENDPOINT` 默认空，填写自己的 Worker `https://<自己的域名>/v1/radar`；空值或雷达校验失败时保留 Apple 响应。Quantumult X 暂不提供雷达模块。

由旧完整增强版迁移时，先关闭旧增强模块及其他地图实验模块，启用普通 WK3 模块并核对原天气参数，再启用一份对应平台的独立雷达模块、填写端点。请勿同时保留旧增强模块或重复安装雷达模块，以免重复执行。MITM 启用并信任证书后，普通模块提供 `weatherkit.apple.com`，雷达模块追加 `weather-map2.apple.com`；两份模块各自维护脚本和参数。

部署及实验能力边界见 [Cloudflare 与自有 VPS 指南](cloudflare/README.zh.md)。

## 代码组织

- `src/wk3/class`、`function`、`process`、`proto`、`utils`：五平台共用的编解码、算法、来源处理与业务逻辑，按上游分类保留；不再嵌套在 Egern 目录。
- `src/wk3/entry`：共用的配置归一化与请求／响应处理；`runtime.mjs`、`parameters.mjs`、`routes.mjs` 分别维护共享 HTTP 封装、参数和五类路由。
- `src/platform/wk3-adapter.mjs`：Surge/Loon/Stash/Quantumult X 的客户端对象适配；Egern 原生上下文直接调用同一组共享入口。客户端网络 API 和输出格式由各自 Rollup 构建器绑定，不混入业务算法。
- `tests/wk3/`：共享协议及业务测试；根目录 `tests/wk3-*.test.mjs`：跨平台代理和发行构建测试。

## 默认配置

| 设置 | 默认值 | 生效范围 |
| --- | --- | --- |
| 今日空气指数来源 | `ColorfulCloudsUS` | 彩云美标；已有 AQ 仅匹配 `HJ6332012` 后替换，大陆缺 AQ 时按上述条件补入。 |
| 昨日空气指数来源 | `ColorfulCloudsUS` | 与今日同来源、同标准才比较；否则保持未知。 |
| 内置 AQ 计算算法 | `WAQI_InstantCast_US` | **只有来源设为 `Calculate` 才执行**；共 12 个选项含 `None`（11 种计算），WK3 目标标准不兼容时透传。 |
| 分钟降水 | `ColorfulClouds` | 已验证的分钟雨结构；未知条件透传。 |
| 天气预警 | `QWeatherWeb` | 严格匹配后补全原生详情；`WeatherKit` 为原样透传。 |
| 天气来源 | `ColorfulClouds` | WK2 可用；WK3 当前/逐日/逐小时天气仍透传 Apple。 |
| 逐日/逐小时替换 | `false` / `false` | WK2 开关，WK3 不生效。 |
| 天气替换范围 / 请求超时 / 日志 | `CN` / `8000` 毫秒 / `INFO` | 香港缺 AQ 补入另有专用条件；第三方令牌按需在设备上配置。 |

上述为生成模块的默认参数，不覆盖已安装模块或设备上的个性化参数。改变内置算法默认值也**不会**自动将今日指数来源改为 `Calculate`。

## 已知限制与发布

- WK3 `currentWeather`、`forecastDaily`、`forecastHourly` 缺少经真实样本验证的编解码映射；不可把上游 WK2 codec 直接用于 WK3。现阶段这三类数据原样透传，即使在设置中选择了天气数据源。
- Quantumult X 二进制天气不可转换；Loon、Egern 的实机反馈范围见平台表，Surge、Stash 已通过压缩脚本和抓包回放，尚无对应设备验收。
- 预警补全依赖可严格唯一匹配的 Apple 原有事件；失配或来源失败时保留原信息。上游和风／彩云 API 预警来源尚未移植。
- 内置 InstantCast 单组浓度计算不等同于 EPA NowCast 时间加权；算法选项也不会自动提供 AQHI 所需的历史均值。WK3 昨日比较不支持 `Calculate` 路径，其他跨来源、跨标准组合返回未知。
- 部分城市 UV 面板的防晒时段建议可能显示英文，标题和等级正常；根因尚未证实。
- 雷达当前输出为实验性回波出现／未出现图层，未标定 mm/h；缺测、源过期、未知运动、202 或错误时保留 Apple 响应。大陆多地区独立有雨事件的预测技巧、真实图层到 Apple 绘制的完整链路及各客户端真机验收仍待完成。
- 本 Fork 的 v3.3.6 WK3 模块在 [Releases](https://github.com/patrickyanxxxxx/WeatherKit-WK3/releases) 获取，文件名含 `.WK3.`。发行附件中的 Egern YAML 引用同版本 JS；更新模块后请核对已保存的个人参数。
- 上游的私有 GitHub Packages 依赖在无权限环境会使干净 `npm install` 返回 401。本分支使用已有依赖及上游已声明的 Rollup 工具完成验证，没有新增依赖。

验证命令：`npm run test:wk3`（包含旧入口、算法边界、配置、模块、五平台日志与发行构建回归，串行避免本机并发打包争用）。普通分支运行 `npm run build:wk3` 仅生成五平台脚本；打标签时 CI 使用实际标签版本运行 `npm run build:wk3 -- --version <版本号>`，将十份脚本与五份模块一起写入 `dist/` 并进入现有附件上传步骤。指定版本构建本身不创建标签或 Release；发版后仍须核对客户端能访问每个附件 URL。
