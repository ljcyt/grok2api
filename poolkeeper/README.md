# g2a-poolkeeper

独立池维护编排服务：对 **Grok2API Build 账号** 做定期测活 → 确认死号清理 → 按水位通过 **8787 注册台** 补号。

## 与现网关系

| 组件 | 地址 | 说明 |
|------|------|------|
| Grok2API | `http://127.0.0.1:8000` | 账号池；需部署含 `POST /api/admin/v1/accounts/build/probe` 的版本 |
| 8787 注册台 | `http://127.0.0.1:8787` | 产号 / 本地测活 / g2a push |
| Cloudflare Tunnel | `https://grok2.081488.xyz` → `127.0.0.1:8787` | 公网入口（配置见 `~/.cloudflared/config-grok2.yml`） |

Poolkeeper 默认连 **本机 8787**，不经过 cftun，避免公网绕路。

## 原则

1. **不解密 Grok2API 数据库**；测活在 Grok2API 进程内完成（admin probe API）。
2. 单次 401 → `suspect_dead`，连续确认后才 `confirmed_dead`。
3. 429 / CF / 5xx / 网络错误 **不删号**。
4. 默认 `dry_run: true`，先观察再 disable。
5. 补号：本地库存 `auth-g2a-push` 优先，再 `jobs/register`。

## 快速开始

```bash
cd poolkeeper
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env
# 编辑 .env 填 G2A_ADMIN_PASSWORD / REGISTER_WEB_TOKEN

# 干跑一轮
POOLKEEPER_ONCE=1 POOLKEEPER_DRY_RUN=1 python -m app.main
```

Docker:

```bash
cp config.example.yaml config.yaml
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

## Git / 上游同步

本目录在 `grok2api-build` 仓库分支 `feat/poolkeeper-8787`：

```bash
cd /home/ljc/grok2api-build
git remote -v   # upstream = https://github.com/chenyme/grok2api.git

# 同步上游
git fetch upstream
git rebase upstream/main   # 或 merge

# 推到你的 fork（先加 origin）
git remote add origin git@github.com:<you>/grok2api.git
git push -u origin feat/poolkeeper-8787
```

Grok2API 侧改动（可单独 rebase）：

- `backend/internal/application/account/build_probe.go`
- `backend/internal/transport/http/account/handler.go`（路由 `POST .../accounts/build/probe`）

## 阶段建议

1. dry_run 观察 3–7 天  
2. inventory 推送  
3. cleanup `disable`  
4. 限额 register  
5. 延迟 hard delete  
