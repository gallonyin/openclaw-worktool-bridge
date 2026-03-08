# OpenClaw 对接 WorkTool 项目 TODO（解耦多实例版）

## 0. 已确认目标

- 本项目与 OpenClaw **解耦部署**：OpenClaw 在其他服务器上，不与本项目同机强绑定。
- 每个 `robot_id` 下可有多个 provider；同一机器人不同群/私聊规则可路由到不同 provider。
- provider 中一部分可指向不同 OpenClaw 网关实例。
- WorkTool 只负责把消息推给本项目；本项目负责桥接 OpenClaw 与 WorkTool 发消息。

## 1. 当前实现与目标一致性检查

### 1.1 一致的部分

- 已有 `ai_providers + routing_rules`：支持“同一机器人按规则选不同 provider”。
- 已有回调快返：`POST /api/v1/callback/qa/{robot_id}` 立即返回成功，后台处理。

### 1.2 不一致（必须改）

1. 当前 provider 调用器按 **OpenAI chat/completions** 语义实现，不是 OpenClaw webhook 语义。
2. 当前一次处理只产出一条 reply；不支持 OpenClaw 可能的多条异步输出。
3. 之前的本机 OpenClaw 安装思路是“耦合部署”，与“远端多实例”目标不符。
4. provider 缺少类型/模式字段，无法明确区分 `openai` 与 `openclaw`。

## 2. 联网调研结论（官方文档）

- OpenClaw webhook：`POST /hooks/agent`，启用后走 token 认证，默认异步入口（官方文档）。
- OpenClaw 远程访问推荐单独 Gateway 主机（remote gateway 模式），支持通过隧道/远端连接。
- hooks 配置支持：`enabled/token/path/defaultSessionKey/allowedAgentIds` 等。

参考：
- https://docs.openclaw.ai/automation/webhook
- https://docs.openclaw.ai/gateway/remote
- https://docs.openclaw.ai/gateway/configuration-reference

## 3. 目标架构（按你要求）

1. WorkTool -> 本项目回调（现有）
2. 本项目按规则选 provider（可为不同 OpenClaw 实例）
3. 若 provider.type = `openclaw`：
   - 调用该实例的 webhook（带 token）
   - 使用统一 sessionKey（建议：`wt:{robot_id}:{scene}:{peer}`）
4. OpenClaw 输出 -> 回到本项目 -> 本项目调用 WorkTool `sendRawMessage`

> 关键：本项目是统一“消息总线/桥接器”，OpenClaw 不直接触达 WorkTool。

## 4. 落地改造（下一步）

### Phase A - 数据结构扩展

- `ai_providers` 增加字段：
  - `provider_type`：`openai` | `openclaw`
  - `auth_scheme`：`bearer` | `x-openclaw-token`
  - `extra_json`：存 `hooks_path/default_session_prefix/...`

### Phase B - 运行时路由

- 新增 Provider Adapter：
  - `OpenAIAdapter`
  - `OpenClawAdapter`
- `OpenClawAdapter` 先实现“单回复可用链路”，再扩展多条输出。

### Phase C - 异步输出通道

- 设计 OpenClaw 到本项目的回传通道（推荐：OpenClaw hook/plugin 回调本项目新接口）。
- 本项目接到每条输出后调用 WorkTool `sendRawMessage`。

### Phase D - 前端配置

- Provider 页面支持选择 `provider_type=openclaw`。
- 机器人规则页可直观看到“该规则路由到哪个 OpenClaw 实例”。

## 5. 需要你确认（开工前最后两点）

1. OpenClaw 输出回传到本项目，是否采用“OpenClaw 侧 hook/plugin 回调我方接口”的方案？
2. 第一版是否先接受“每次输入只取一条主回复”跑通，再迭代多条异步输出？

