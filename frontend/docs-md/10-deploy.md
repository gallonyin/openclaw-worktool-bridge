# 部署与运维

## Docker 部署

推荐使用 `docker compose` 统一启动：

1. MySQL
2. Backend
3. Frontend

## 时区建议

MySQL 和应用统一使用 `Asia/Shanghai`，便于日志和数据库对齐。

## 环境变量

重点关注：

- WorkTool API Base
- 短信服务配置
- 默认测试 AI 引擎开关与参数

## 升级建议

- 先在测试环境验证迁移与回调链路
- 再滚动升级生产
- 升级后验证：登录、规则命中、AI 回复、转发、监控
