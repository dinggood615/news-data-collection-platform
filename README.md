# 新闻数据采集平台

面向公开 RSS 与允许访问的新闻 API 的自托管国际新闻采集平台。自动采集、去重并筛选“中国、科技、政治、经济、战争”主题新闻，生成适合企业微信群阅读的 Markdown 消息。

## 功能

- 内置 BBC、DW、France 24、Al Jazeera、UN News、The Guardian、BBC/美联社重大新闻、BBC/DW 商业新闻，以及亚洲的联合早报（FeedX 镜像）、CNA 亚洲/新加坡/商业、南华早报中国/亚洲、The Japan Times 等公开 RSS 源；可在页面中启用、停用、添加或删除来源。
- 联合早报镜像及部分媒体 RSS 仅提供标题、摘要和原文链接，使用前请遵守来源网站的 RSS、版权和个人/非商业使用条款。
- 五类主题规则可编辑，一篇新闻可命中多个主题；按原文链接去重。
- 支持本地 LibreTranslate / Argos 翻译接口，无需付费 API；翻译不可用时自动保留原文，不会中断采集。
- 企业微信机器人 Markdown 自动排版、分段、去重推送；Webhook 仅加密保存在服务器。
- SQLite 存储、运行记录、`/healthz` 健康检查和自动备份基础能力。

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

## 首次配置

1. 在“国际新闻源”确认启用所需 RSS 源，或添加公开 RSS/API 地址。
2. 在“主题与关键词”维护中国、科技、政治、经济、战争的匹配规则。
3. 在“企业微信与翻译”填写企业微信机器人 Webhook，并设定每日汇总时间。
4. 本地免费翻译服务默认地址为 `http://127.0.0.1:5000/translate`；配置 LibreTranslate 后会自动使用。未配置时保留原文。
5. 点击“立即采集”执行首次测试。

### Telegram 绑定

Telegram 通过 Webhook 接收命令，因此必须准备一个已解析到服务器的域名和有效 HTTPS 证书；不要填写 IP 地址或 `:5555` 端口。平台会在标准 `443` 端口接收 `https://<域名>/telegram/callback`，该路径仅校验 Telegram 的秘密请求头，不会暴露后台页面。

1. 在“Telegram 机器人”填入 BotFather 生成的 Token 和 `https://<你的域名>`，保存后点击“配置 Telegram Webhook”。
2. 在机器人会话中发送**准确的** `/start`。
3. 回到平台点击“一键批准绑定”；之后可从 Telegram 发送“状态”“立即采集”“最新新闻”或“备份”。

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
