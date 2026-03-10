# Docker 部署（三容器）

## 1. 启动

```bash
docker compose up -d --build
```

启动后访问：
- 前端: `http://127.0.0.1:8080`
- 后端: 仅容器内访问（由前端 Nginx 反代 `/api`）
- MySQL: `worktool-mysql`（仅容器网络内访问）

## 2. 停止

```bash
docker compose down
```

## 3. 查看日志

```bash
docker compose logs -f mysql
docker compose logs -f backend
docker compose logs -f frontend
```

## 4. 数据持久化（纯 Docker 卷）

MySQL 数据使用 Docker 命名卷，不落宿主机目录：
- 数据库卷：`worktool_mysql_data`（容器内 `/var/lib/mysql`）

删除容器不会丢失数据库。

## 5. 进入 MySQL（可选）

```bash
docker compose exec mysql mysql -u${MYSQL_USER} -p${MYSQL_PASSWORD} ${MYSQL_DATABASE}
```
