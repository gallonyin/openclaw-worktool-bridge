# Docker 部署（双容器）

## 1. 启动

```bash
docker compose up -d --build
```

启动后访问：
- 前端: `http://127.0.0.1:8080`
- 后端: 仅容器内访问（由前端 Nginx 反代 `/api`）
- 备份容器: `worktool-db-backup`（每日自动备份 SQLite）

## 2. 停止

```bash
docker compose down
```

## 3. 查看日志

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

## 4. 数据持久化（纯 Docker 卷）

SQLite 与备份都使用 Docker 命名卷，不落宿主机目录：
- 数据库卷：`worktool_db_data`（容器内 `/data/app.db`）
- 备份卷：`worktool_db_backup`（容器内 `/backup`）
- 备份策略：每天 `03:30` 备份一次，保留最近 `7` 天（滚动删除）

删除容器不会丢失数据库。

## 5. 手动触发一次备份（可选）

```bash
docker compose exec db-backup /usr/local/bin/backup.sh
```

## 6. 查看卷内备份文件

```bash
docker run --rm -v worktool_db_backup:/backup alpine:3.20 ls -lah /backup
```
