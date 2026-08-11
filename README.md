# 新闻数据采集平台

面向公开 RSS 与允许访问的新闻 API 的自托管国际新闻与金融情报平台。采用“高召回候选、确定性事件评分、本地模型复核、分栏日报”的流水线，降低简单关键词筛选的漏报与误报。

## 功能

- 内置 BBC、DW、France 24、Al Jazeera、UN News、The Guardian、BBC/美联社重大新闻、BBC/DW 商业新闻，以及亚洲的联合早报（FeedX 镜像）、CNA 亚洲/新加坡/商业、南华早报中国/亚洲、The Japan Times 等公开 RSS 源；可在页面中启用、停用、添加或删除来源。
- 联合早报镜像及部分媒体 RSS 仅提供标题、摘要和原文链接，使用前请遵守来源网站的 RSS、版权和个人/非商业使用条款。
- 主题与关键词可在页面中自由新增、编辑、启停或删除；关键词自动清理重复项，一篇新闻可命中多个主题，并按原文链接去重。
- 支持本地 LibreTranslate / Argos 翻译接口，无需付费 API；翻译不可用时自动保留原文，不会中断采集。
- 企业微信机器人 Markdown 自动排版、分段、去重推送；Webhook 仅加密保存在服务器。
- 企业微信单条日报：每次采集只发送一条汇总消息，点击随机只读链接即可免登录查看当次全部新闻；支持分类筛选、全文搜索和移动端阅读。
- SQLite 存储、运行记录、`/healthz` 健康检查和自动备份基础能力。
- 新闻情报控制台采用响应式卡片与新闻流设计，集中展示来源覆盖、今日新增、主题状态、采集记录、推送和聊天助手；支持键盘焦点与减少动画偏好。
- 金融专栏覆盖宏观与央行、全球金融、股票与公司、债券与外汇、商品与能源、监管披露、金融风险和亚洲市场；预置美联储、欧洲央行、国际清算银行等官方公开源。
- 扩展英国央行、IMF、日本央行、韩国央行、香港金管局官方源，以及港交所监管通讯和 Nasdaq Europe 公告；来源失败只记录告警，不会中断整轮任务。
- SEC EDGAR 公司观察名单可在页面填写公司名称、CIK 和关注表单；平台使用官方 Submissions API，每家公司每轮最多一次请求，不进行全站扫描。
- 来源按 T1 官方/监管、T2 主流媒体、T3 补充来源分级；标题词组、事件数量、来源等级和负面噪声共同生成 0–100 影响分。
- 可复用 VPS 上的 Qwen3 1.7B GGUF 本地模型，只复核中等置信度候选，生成中文摘要、事件类型、实体和影响理由；失败时自动退回规则结果，不阻断采集。

只采集公开且明确允许访问的信息，不绕过登录、付费墙、验证码或访问控制。

## 一键安装（原生 Linux）

支持 Ubuntu、Debian、RHEL、Rocky、AlmaLinux、CentOS Stream、Fedora、openSUSE 与 Arch 的 systemd 环境：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/news-data-collection-platform/main/install-linux.sh | sudo bash -s -- https://github.com/dinggood615/news-data-collection-platform.git
```

默认 HTTPS 端口为 `5555`；安装器也会同时监听标准 HTTPS `443`，供 Telegram Webhook 使用。更换仪表盘端口：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/news-data-collection-platform/main/install-linux.sh | sudo env PORT=8443 bash -s -- https://github.com/dinggood615/news-data-collection-platform.git
```

安装完成后访问 `https://<服务器IP>:5555`。默认管理员账户为 `admin / admin`，请立即在服务器 `.env` 中修改 `ADMIN_PASSWORD` 后重启 `news-platform` 服务。

如果需要 Telegram，请先在域名控制台添加 `A` 记录指向服务器公网 IP，并在安全组/防火墙放行 TCP `80`、`443`、`5555`，随后使用下面的安装命令。安装器会自动申请并配置 Let's Encrypt 证书：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/news-data-collection-platform/main/install-linux.sh | sudo env DOMAIN=news.example.com LETSENCRYPT_EMAIL=you@example.com bash -s -- https://github.com/dinggood615/news-data-collection-platform.git
```

`DOMAIN` 只能填写域名，不含 `https://`、路径或端口。未提供 `DOMAIN` 时仍可安装平台，但会使用自签名证书，Telegram Webhook 不可用。

## 首次配置

1. 在“国际新闻源”确认启用所需 RSS 源，或添加公开 RSS/API 地址。
2. 在“主题与关键词”维护中国、科技、政治、经济、战争的匹配规则。
3. 在“企业微信与翻译”填写企业微信机器人 Webhook，并设定每日汇总时间。
4. 填写与平台相同的 HTTPS 根地址（例如 `https://news.example.com`），设置日报链接有效天数和消息头条数量。
4. 本地免费翻译服务默认地址为 `http://127.0.0.1:5000/translate`；配置 LibreTranslate 后会自动使用。未配置时保留原文。
5. 点击“立即采集”执行首次测试。

### 金融情报配置

页面中可为每个新来源指定资讯专栏和来源等级，并调整“最低金融影响分”（默认 35）与“每轮模型复核上限”（默认 12）。较低阈值提高召回率但会带来更多噪声；本地模型只负责复核和摘要，不用于预测价格，也不会自动执行交易。

SEC 观察名单使用官方 `https://data.sec.gov/submissions/CIK##########.json`。先在设置中填写包含有效联系邮箱的 User-Agent（例如 `MyResearchPlatform name@example.com`），再添加 10 位 CIK。默认关注 `8-K,10-K,10-Q,6-K,20-F`，可按公司修改。系统限制最多 50 家公司、每家公司每轮一次请求，并在请求间隔 120 毫秒，以遵守 SEC Fair Access 要求。

交易所公告当前接入港交所官方监管通讯、Nasdaq Europe Main Market 与 First North 公告。伦交所已停止公开 RNS RSS，且部分交易所数据要求授权，因此不会通过隐藏接口或绕过许可采集；有合法数据许可时可在“国际新闻源”添加对应 RSS/API。

本地模型接口默认采用 llama.cpp 的 OpenAI 兼容地址 `http://127.0.0.1:8082/v1/chat/completions`。Linux 环境可将 `LLAMA_SERVER` 和 `FINANCE_MODEL_PATH` 指向本机二进制与 GGUF 文件；平台会在有候选项时按需启动，任务结束后关闭进程。未安装模型时仍可完整使用规则筛选。

“全球重要金融网站”指经过筛选、允许公开聚合的官方源和 RSS/API，不代表互联网全部金融网站。Bloomberg、Reuters 等付费或限制自动访问的内容不会绕过付费墙；管理员可在页面继续添加有权使用的 RSS/API。

> 风险声明：平台生成的评分、翻译、摘要与模型判断仅供信息研究，不构成投资建议、买卖信号或收益保证。重要决策前请核验原始公告，并咨询持牌专业人士。

### 企业微信：推送与聊天助手

- 仅接收每日新闻推送时，使用企业微信群机器人 Webhook：在“企业微信与翻译”粘贴 Webhook 后点击“发送测试消息”即可，无须配置回调。
- 每次运行只请求一次企业微信 Webhook。消息显示分类统计与少量头条，完整新闻固化为只读日报快照；链接使用 256 位随机令牌，默认 7 天后失效，不能访问管理后台，页面同时禁止搜索引擎收录。
- 需要像 Telegram 一样通过聊天控制平台时，在“企业微信助手”仅填写 **CorpID、HTTPS 公网地址、管理员 UserID**。平台会自动生成 Token 与 EncodingAESKey，并显示“回调地址、Token、EncodingAESKey”三项。
- 将这三项复制到企业微信管理后台的“应用管理 → 自建应用 → 接收消息”，保存验证后，即可向应用发送“状态”“立即采集”“最新新闻”或“备份”。
- 企业微信后台必须由企业管理员完成一次回调验证；这是企业微信的平台安全要求，无法由服务器绕过或自动代替。

### Telegram 绑定

Telegram 通过 Webhook 接收命令，因此必须准备一个已解析到服务器的域名和有效 HTTPS 证书；不要填写 IP 地址或 `:5555` 端口。平台会在标准 `443` 端口接收 `https://<域名>/telegram/callback`，该路径仅校验 Telegram 的秘密请求头，不会暴露后台页面。

1. 在 BotFather 创建机器人并复制 Token；Token 仅填写在平台页面，不要提交至 GitHub 或发送给他人。
2. 在“Telegram 机器人”填入 Token 和 `https://<你的域名>`，保存后点击“配置 Telegram Webhook”。
3. 在机器人会话中发送**准确的** `/start`（不是 `/star`）。
4. 回到平台点击“一键批准绑定”；之后可从 Telegram 发送“状态”“立即采集”“最新新闻”或“备份”。

### Telegram 无法绑定时的检查顺序

1. 确认域名的公网 DNS 已解析到当前服务器，而非旧 IP。
2. 浏览器访问 `https://<你的域名>/healthz`，应返回 `{"status":"ok"...}`；证书不能显示不受信任。
3. 在 Telegram 设置中确认地址是 `https://<你的域名>`，不带 `:5555`；Telegram 仅能使用标准 Webhook HTTPS 端口。
4. 点击“配置 Telegram Webhook”后再发送 `/start`，刷新平台后点击“一键批准绑定”。
5. 若仍失败，在服务器运行 `sudo journalctl -u news-platform -n 100 --no-pager`；出现 `/telegram/callback` 的 `POST 200` 即代表消息已送达平台。

## 一键卸载

以下命令会停止新闻平台、删除程序目录、SQLite 数据、平台 TLS 与 Nginx 配置；不会卸载系统 Nginx、Docker 或其他服务：

```bash
curl -fsSL https://raw.githubusercontent.com/dinggood615/news-data-collection-platform/main/uninstall-linux.sh | sudo bash -s -- --yes
```

## 运维

```bash
sudo systemctl status news-platform
sudo journalctl -u news-platform -f
curl -k https://127.0.0.1:5555/healthz
```

数据目录默认为 `/opt/news-data-collection-platform/data`。不要提交 `.env`、企业微信 Webhook、服务器密码或 `APP_SECRET`。
