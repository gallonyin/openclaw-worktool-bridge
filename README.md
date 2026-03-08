# OpenClaw WorkTool Bridge

WorkTool 企业微信机器人桥接服务。  
用于承接 WorkTool 消息回调、按规则路由 Provider、并下发回复消息；同时提供可视化管理后台。

## Features

- React + FastAPI 前后端分离
- 机器人配置 / 规则管理 / 消息监控
- WorkTool 机器人信息与回调管理
- Docker Compose 一键启动（前端、后端、备份）

## Quick Start

1. 可选：复制环境变量样例

```bash
cp .env.example .env
```

2. 启动服务

```bash
docker compose up -d --build
```

3. 访问控制台

- `http://127.0.0.1:3000`

## Services

- `worktool-backend`: FastAPI backend
- `worktool-frontend`: Nginx + frontend static files
- `worktool-db-backup`: daily SQLite backup (7-day retention)

## Notes

- 默认使用 SQLite（持久化在 Docker Volume）
- `.env` 文件仅本地使用，不应提交到仓库

## License

MIT License. See [LICENSE](./LICENSE).
