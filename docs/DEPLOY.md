# 部署（Docker / 24h）

## 1. 最快：Docker 跑机器人

```bat
copy .env.example .env
:: 填 DEEPSEEK_API_KEY、RICEFISH_WS_TOKEN、白名单（也可以启动后在面板加）
docker compose up -d --build
docker compose logs -f bot
```

打开 `http://<host>:8721`，用 `.env` 里的 `RICEFISH_PANEL_TOKEN` 登录。
`data/` 和 `config/` 已挂载到宿主机；升级镜像不会丢数据。

> 公开仓库只带通用配置：`.env`、`data/`、`vendor/`、真实群号/QQ 人设、
> `config/personas/learned.md` 和 `*.personal.*` 备份都在本地，不进入 Git / 发行包。

### 必要环境变量

| 变量 | 作用 |
|---|---|
| `DEEPSEEK_API_KEY` | 必填 |
| `RICEFISH_WS_TOKEN` | 必须和 NapCat 反向 WS 的 token 一致 |
| `RICEFISH_PANEL_TOKEN` | 面板登录；公网必须设强 token |
| `RICEFISH_WEB_HOST` | 容器里用 `0.0.0.0`（compose 已设） |
| `RICEFISH_DB` | 容器里 `/app/data/bot.db` |

### NapCat 放哪

Dockerfile 只跑「大脑」，不跑 QQ 客户端。三种拓扑：

- **A（推荐，风控低）**：NapCat 跑在 Windows/家里机器，反向 WS 指向云服务器的
  `wss://<域名>/onebot/v11/ws` 或 Tailscale 内网地址；机器人跑在 Linux 云。
- **B（全栈 Linux）**：另起 NapCat Linux 容器 + Xvfb + QQ，反代到 bot 的 8080；
  维护成本、封号风险更高。
- **C（官方 Bot API）**：如果只做频道/群，不走 NapCat；本项目需要 OneBot 适配层。

面板和 8080 不要裸奔公网：至少套 Caddy/Nginx + HTTPS + 强 token，
或只监听 Tailscale/WireGuard 内网。

## 2. 健康检查与指标

- `GET /healthz`：公开，返回 `ok/connected/dry_run/uptime_sec`。
- `GET /metrics?token=<PANEL_TOKEN>`：Prometheus 文本；
  也可以先登录面板用 cookie 访问。
- Docker healthcheck 已内置。

## 3. 备份

```bat
python scripts/backup_db.py --db data/bot.db --out data/backups --keep 14
```

- 用 SQLite 在线 backup API，运行中也能拷。
- 宿主机 cron / systemd timer：

```cron
0 4 * * * cd /opt/ricefish && python scripts/backup_db.py --keep 14 >> data/logs/backup.log 2>&1
```

- 云上建议再同步到对象存储并加密；恢复时停 bot，把备份覆盖 `data/bot.db` 再启动。
- 升级前先备份：`data/` 里是全部记忆和表情。

## 4. 长期运行

- `docker compose` 已设 `restart: unless-stopped`。
- 非 Docker：systemd 示例

```ini
[Unit]
Description=ricefish
After=network.target

[Service]
WorkingDirectory=/opt/ricefish
ExecStart=/usr/bin/python3 run.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

- 日志：`data/logs/bot.log` 自动按 5MB 滚动；容器 `docker logs` 另有一份。
- 成本：面板概览有 24h token 用量；主动陪伴/群插话都有每日配额，别关限流。

## 5. 安全清单

- [ ] 面板只在内网/Tailscale，或 HTTPS + 强 token
- [ ] NapCat token 不泄露；8080 不直接公网
- [ ] `.env` 不进 git；`data/`、`vendor/` 不进镜像
- [ ] 定期备份并做一次恢复演练
- [ ] 先 `dry_run: true` 跑一天，再关
- [ ] 小号、白名单、限流、静默时段
- [ ] 素材许可：CC BY-NC-SA 素材商用/再分发前确认

## 6. 常见故障

| 现象 | 排查 |
|---|---|
| `/healthz` 通但机器人不回 | 看 NapCat 是否连上 8080、token 是否一致、白名单 |
| 面板打不开 | 端口映射、`RICEFISH_WEB_HOST=0.0.0.0`、防火墙 |
| 容器启动即退出 | `docker compose logs bot`；`.env` 是否在；config 挂载是否为空 |
| 数据库锁 | 只跑一个 bot 实例；SQLite 不适合多进程写 |
| 视觉/摘要变慢 | 检查 `thinking_vision/thinking_utility` 是否 disabled |
