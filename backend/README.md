# Backend 启动说明

## 1. 安装依赖

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 启动服务

```bash
cd backend
python main.py
```

默认监听 `0.0.0.0:8000`。

## 3. 首次启动自动导入

若项目根目录存在 `config.json`，且数据库 `backend/app.db` 中暂无机器人数据，服务会自动导入。
