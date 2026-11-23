# 问答接口 → 任务型 Agent 改造设计文档

- 状态：M2 已落地（HITL + 外部工具 + 审计导出）
- 日期：2026-11-23
- 范围：后端 Agent API + 前端模式/审批 + 管理后台 Agent 统计
- 配套现状文档：[功能清单](功能清单.md)、[接口文档](接口文档.md)

---

## 1. 背景与目标

### 1.1 现状

当前后端问答是「单轮/多轮对话 + 可选知识库 RAG」：

1. 组装 system / history / user message；
2. 若带 `kb_ids`，先向量召回 + rerank，把资料注入 system；
3. 一次调用大模型，流式或非流式返回文本。

本质是 **RAG 增强的聊天补全**，模型只能「读知识、写文字」，不能主动调用外部能力、不能拆解多步任务、不能在中途根据工具结果改计划。

### 1.2 目标

把 `/api/chat` 从「回答问题」升级为「完成任务」的 **Agent 入口**：

| 目标 | 说明 |
|---|---|
| 复杂任务 | 多步骤、可分支、依赖中间结果的任务可被拆解并执行 |
| 可行动 | 通过工具访问知识库、计算、检索、业务 API 等，而非仅凭参数知识作答 |
| 可观测 | 用户能看到规划、工具调用、中间结论，而不只是最终一段文字 |
| 可控 | 预算上限、危险操作审批、可取消/可暂停 |
| 可演进 | 先单 Agent + 工具闭环，再按需扩展多 Agent / 工作流 |

### 1.3 非目标（本期不做）

- 通用电脑操控（桌面 RPA / Computer Use）
- 任意沙箱内执行用户任意代码（安全与运维成本过高）
- 完全替代现有简单问答体验（简单问题应仍走短路径，延迟与成本要可控）
- 引入独立 Agent 编排 SaaS（优先自研编排 + 现有 OpenAI 兼容模型）

---

## 2. 主流 Agent 能力盘点

综合业界常见实践（ReAct、Plan-and-Execute、OpenAI Agents、LangChain/LangGraph 1.x 等），生产级 Agent 通常具备以下几类能力。

### 2.1 能力地图

```
┌─────────────────────────────────────────────────────────────┐
│                        Agent Runtime                         │
│  感知输入 → 规划/推理 → 行动(工具) → 观察 → 再规划 → 收敛输出 │
├──────────────┬──────────────┬──────────────┬────────────────┤
│ 规划 Planning │ 工具 Tools   │ 记忆 Memory  │ 编排 Orchestr. │
│ · 任务拆解    │ · Function   │ · 短时对话   │ · 单 Agent 环  │
│ · 动态重规划  │ · RAG/检索   │ · 工作记忆   │ · 路由/分工    │
│ · 停止条件    │ · API/计算   │ · 长期记忆   │ · 人机协同     │
├──────────────┴──────────────┴──────────────┴────────────────┤
│ 横切：流式事件 · 预算/超时 · 鉴权与隔离 · 审计日志 · 评测    │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 能力说明

| 能力 | 业界常见做法 | 对本项目的意义 |
|---|---|---|
| **Tool Calling** | 模型输出结构化 tool call，运行时执行后把 observation 回填 | 从「只会说话」变为「能办事」的基础 |
| **推理循环（ReAct）** | Thought → Action → Observation 循环直至给出 Final Answer | 适合探索型、步骤依赖中间结果的任务 |
| **规划（Plan-and-Execute）** | 先出计划再逐步执行，失败可 replan | 适合长任务、进度可见、成本可拆分「强模型规划 / 弱模型执行」 |
| **Agentic RAG** | 检索本身作为工具，可多轮检索、改写 query、换库 | 现有 RAG 从「请求前硬注入」变为「按需检索」 |
| **记忆** | 短时：对话与本轮轨迹；长时：跨会话摘要/向量记忆 | 复杂任务需工作记忆；用户偏好可后置 |
| **人机协同（HITL）** | 高风险工具前中断，等人批准/修改/拒绝后再继续 | 删改、外发、付费类动作的安全阀 |
| **多 Agent** | Supervisor / Handoff / Agent-as-Tool | 后期按领域拆分（检索、写作、分析），初期不必上 |
| **流式与可观测** | 流式输出 token + 步骤/工具事件；Trace 可回放 | 前端进度条、步骤卡片、排障必备 |
| **可靠性** | 超时、重试、幂等、checkpoint、预算熔断 | 长任务断线、进程重启、死循环防护 |

### 2.3 推理范式选型（单选主路径）

| 范式 | 优点 | 缺点 | 建议 |
|---|---|---|---|
| 纯 ReAct | 实现简单、灵活 | 长任务易跑偏、进度难展示 | 作为 **执行器内核** |
| 纯 Plan-and-Execute | 计划可见、易控 | 计划僵硬，需 replan | 作为 **外层壳**（复杂任务） |
| 固定 Workflow | 最可控 | 覆盖不全 | 仅用于少数标准化业务流 |
| Reflection | 提升质量 | 额外延迟与费用 | 可选后置校验，非主路径 |

**推荐主路径：Plan-and-Execute 外壳 + ReAct/Tool-Calling 执行步。**

- 简单问答：跳过规划，直接单次补全（兼容现体验）。
- 复杂任务：Planner 产出步骤列表 → Executor 逐步调用工具 → 必要时 Replan → 汇总最终答案。

---

## 3. 现状差距

| 维度 | 现状 | Agent 所需 |
|---|---|---|
| 模型交互 | 单次 `chat_completion` / stream | 多轮：模型 ↔ 工具 ↔ 模型 |
| 知识库 | 请求前一次性 `retrieve` 注入 | 检索工具化，可多轮、可改写 query |
| 输出 | 仅文本 delta + sources | 计划、步骤、工具结果、最终答案等事件 |
| 执行模型 | 逻辑跑在请求/SSE 生成器内 | 长任务需与 HTTP 连接解耦（断线不等于停任务） |
| 状态 | `messages` 存问答对 | 需任务状态、步骤轨迹、工具审计 |
| 控制 | 几乎无（仅超时 120s） | 步数/调用次数/token/截止时间；取消与审批 |
| 工具 | 无 | 注册表、Schema、超时、权限、风险分级 |

---

## 4. 总体架构

### 4.1 分层

```
前端 Chat / 任务面板
        │  HTTP + SSE
        ▼
┌───────────────────┐
│  API 层            │  创建任务 / 订阅事件 / 取消 / 审批
│  (FastAPI)         │  兼容：简单问答仍可走短路径
└─────────┬─────────┘
          ▼
┌───────────────────┐
│  Agent Orchestrator│  路由：简单补全 vs 任务 Agent
│  · Router/Classifier│ · Planner · Executor · Replanner
└─────────┬─────────┘
          ▼
┌───────────────────┐     ┌──────────────────┐
│  Tool Runtime      │────▶│  现有能力复用     │
│  注册 / 校验 / 执行 │     │  llm.py / rag.py │
└───────────────────┘     │  db / auth / kb   │
          ▼               └──────────────────┘
┌───────────────────┐
│  Persistence       │  tasks / steps / events / tool_calls
│  + 会话 messages   │  最终答案仍写入 messages，兼容历史会话
└───────────────────┘
```

### 4.2 核心设计原则

1. **会话兼容**：Agent 任务仍归属 `conversation_id`；完成后把用户输入与最终回答写入现有 `messages`，会话列表/历史回放不破。
2. **执行与推送分离**：任务在服务端后台推进；SSE 只订阅事件流。客户端断开可重连续看，不默认取消任务。
3. **工具失败 ≠ 任务失败**：工具错误以 observation 回灌，由模型自纠或触发 replan；仅不可恢复错误才标失败。
4. **简单路径优先**：分类器判定为「无需工具的短问答」时，走现有一次补全，避免 Agent 固定开销。
5. **安全默认关闭写操作**：高风险工具需显式启用 + HITL；默认工具集只读、无副作用。

### 4.3 框架选型建议

项目已使用 **LangChain + OpenAI 兼容 ChatOpenAI**，且部署在自有 FastAPI / PostgreSQL 上。

| 方案 | 适用 | 建议 |
|---|---|---|
| 自研循环 + `bind_tools` | 完全掌控，依赖少 | **一期推荐**：循环清晰、易对齐现有落库与鉴权 |
| LangChain `create_agent` | 标准 tool loop + middleware | 二期可引入，减少样板代码 |
| LangGraph 显式图 | 复杂分支、持久化 checkpoint、HITL | 任务状态变复杂后再上，避免一期过度设计 |
| OpenAI Agents SDK | 与 OpenAI 生态深度绑定 | 与多厂商兼容目标冲突，不作为主依赖 |

**一期结论：自研 Orchestrator + LangChain 模型/工具绑定；状态与事件落自有表。** 不把运行时生命期交给第三方图引擎。

---

## 5. 运行时设计

### 5.1 任务生命周期

```
created → running ⇄ awaiting_approval → running
                ↓
         succeeded | failed | cancelled
```

- `awaiting_approval`：仅高风险工具触发；用户批准/改参/拒绝后继续或结束。
- `cancelled`：用户主动取消；进行中的工具尽量协作式中断（超时后强制丢弃结果）。

### 5.2 单次任务主循环（伪流程）

```
1. Router：simple_chat | agent_task
2. 若 simple_chat → 现有补全路径，结束
3. Planner：生成 plan（有序列表：目标、依赖、成功标准）
4. 发布 plan.updated 事件
5. for step in plan:
     a. 发布 step.started
     b. 组装上下文（系统提示 + 工作记忆 + 相关历史）
     c. 模型推理（可带 tools）→ 可能多轮 tool call
     d. 每轮工具：校验 → 权限 →（审批？）→ 执行 → 观察回填
     e. 发布 step.finished / tool.* 事件
     f. 预算检查（步数 / llm 次数 / token / deadline）
6. 若未达成目标且允许 → Replanner，回到 4
7. 汇总最终答案 → 写 messages → task.succeeded
```

### 5.3 停止与熔断

| 熔断项 | 默认建议 | 超限行为 |
|---|---|---|
| 最大规划步数 | 8 | 强制汇总已有观察作答或失败 |
| 最大 LLM 调用次数 | 20 | 同上 |
| 最大工具调用次数 | 30 | 同上 |
| Token 预算 | 可配置 | 暂停并提示用户 |
| 墙钟截止时间 | 如 10 分钟 | 失败并保留已产出内容 |

所有上限走环境变量，便于按环境调参。

### 5.4 与 SSE 的关系

**禁止**把 Agent 主循环写在 SSE 生成器里：连接断开时生成器可能被丢弃，导致执行中断且状态不一致。

推荐模式：

1. `POST /api/agent/tasks` 创建任务并立即返回 `task_id`；
2. Worker（同进程线程池或后续独立 worker）异步执行；
3. `GET /api/agent/tasks/{id}/events` SSE 按游标推送事件；
4. 浏览器用 `Last-Event-ID` 重连补洞。

一期可「同进程有界线程池」；流量上来后再拆进程/队列，接口形状保持不变。

---

## 6. 工具体系

### 6.1 工具协议

每个工具至少声明：

| 字段 | 说明 |
|---|---|
| `name` | 唯一标识 |
| `description` | 给模型看的用途说明（质量决定选型准确率） |
| `input_schema` | JSON Schema（参数校验） |
| `risk` | `read` / `write` / `external` |
| `timeout_seconds` | 执行超时 |
| `idempotent` | 是否可安全重试 |

执行统一返回：

```json
{
  "ok": true,
  "observation": "给模型看的文本或结构化摘要",
  "data": {}
}
```

失败时 `ok=false`，`observation` 仍可读，供模型调整策略。

### 6.2 一期工具清单（建议）

| 工具 | 风险 | 说明 |
|---|---|---|
| `kb_search` | read | 封装现有 `rag.retrieve`；支持 query 改写与多库 |
| `kb_list` | read | 列出当前用户可用知识库，避免瞎选 id |
| `datetime_calc` | read | 日期差/加减/安全四则运算，消除心算错误 |
| `web_search`（可选） | external | 若配置搜索 API 再启用；默认关 |
| `http_request`（可选） | external | 仅白名单域名；默认关 |

二期再考虑：会话内笔记、结构化抽取落库、受控脚本等。

### 6.3 安全策略

- 工具内再次校验 `user_id` / 知识库归属，**不信任模型传入的权限字段**。
- 观察结果脱敏（API Key、Token 模式）。
- 超长 observation 截断，避免撑爆上下文。
- `write` / `external`：默认禁用；启用后进入 HITL。

### 6.4 Agentic RAG（相对现状的关键变化）

现状：请求入口一次性检索并塞进 system。

改造后：

- 简单问答且用户显式选了 `kb_ids`：可保留「预检索」以降低延迟；
- Agent 模式：把 `kb_search` 交给模型按需调用；Planner 可要求「先检索再作答」。

这样支持「检索不足 → 改写 query 再搜」「先看目录再深挖」等行为。

---

## 7. 记忆与上下文

| 层级 | 内容 | 存储 |
|---|---|---|
| 对话短时记忆 | 会话历史 messages | 现有表 |
| 任务工作记忆 | 本轮 plan、步骤结论、工具观察 | `agent_tasks` / `agent_steps` |
| 长期记忆（二期） | 用户偏好、跨会话摘要 | 新表或向量库 |

上下文装配原则：

1. System：角色 + 工具使用规范 + 输出语言（简体中文）+ 安全约束；
2. 必要历史：最近 N 轮，超限则摘要；
3. 工作记忆：已完成步骤的摘要，而非全文堆叠；
4. 当前用户目标：原始任务描述保持可见。

---

## 8. 接口设计（草案）

### 8.1 兼容策略

| 接口 | 策略 |
|---|---|
| `POST /api/chat`、`/api/chat/stream` | **保留**。内部可增加轻量 Router：无需工具则行为不变；可选 query 参数或 body 字段 `mode=agent` 升级为建任务 |
| 新 Agent API | `/api/agent/...` 作为任务型入口，前端复杂任务面板优先对接 |

### 8.2 新建任务

`POST /api/agent/tasks`

```json
{
  "message": "根据知识库整理一份竞品对比，并算出上线倒计时还有几天",
  "conversation_id": "可选",
  "kb_ids": [1, 2],
  "mode": "auto"
}
```

`mode`：

- `auto`：后端 Router 决定 simple / agent；
- `agent`：强制任务模式；
- `chat`：强制短路径。

响应：

```json
{
  "task_id": "…",
  "conversation_id": "…",
  "status": "created",
  "mode": "agent"
}
```

若判定为 `chat`，可直接同步返回答案（或返回 `mode=chat` 并引导前端走原 stream 接口），避免无谓任务表膨胀。

### 8.3 事件订阅（SSE）

`GET /api/agent/tasks/{task_id}/events?after={cursor}`

事件类型建议：

| type | 含义 |
|---|---|
| `task.started` | 开始执行 |
| `plan.updated` | 计划生成或修订 |
| `step.started` / `step.finished` | 步骤边界 |
| `tool.started` / `tool.finished` | 工具调用与结果摘要 |
| `message.delta` | 最终（或中间）文本增量 |
| `approval.required` | 等待人工确认 |
| `task.completed` | 成功，含 `message_id`、usage |
| `task.failed` / `task.cancelled` | 终态 |

### 8.4 控制类接口

- `POST /api/agent/tasks/{id}/cancel`
- `POST /api/agent/tasks/{id}/approvals`（approve / reject / edit args）
- `GET /api/agent/tasks/{id}`（状态快照：plan、进度、用量）

### 8.5 鉴权

与现有一致：JWT；任务与会话同属用户；管理员可只读审计（可选）。

---

## 9. 数据模型（草案）

在现有 `users` / `conversations` / `messages` / `llm_calls` 之上新增：

### 9.1 `agent_tasks`

| 字段 | 说明 |
|---|---|
| id | UUID |
| conversation_id / user_id | 归属 |
| input | 用户原始任务 |
| status | 生命周期状态 |
| plan_json | 当前计划 |
| mode | auto 实际落定的模式 |
| usage 计数 | steps / llm_calls / tool_calls / tokens |
| deadline_at | 截止时间 |
| error | 失败原因 |
| result_message_id | 关联 `messages.id` |
| created_at / updated_at | 时间 |

### 9.2 `agent_steps`

每步一条：step_id、title、status、输入摘要、输出摘要、起止时间。

### 9.3 `agent_events`

只追加事件流：全局自增 id（SSE 游标）、task_id、type、payload、created_at。

### 9.4 `agent_tool_calls`（或并入 steps 明细）

工具名、参数、结果摘要、latency、ok、风险级别——便于审计与统计。

`llm_calls` 继续记录每一次模型调用；建议增加可选 `task_id` 外键，方便按任务聚合成本。

---

## 10. 前端影响（摘要）

一期最小改动：

1. 发送时可选择「智能任务」或默认 `auto`；
2. Agent 模式下展示：计划时间线 + 工具卡片 + 最终 Markdown；
3. SSE 按事件类型渲染，而不仅是打字机 delta；
4. 支持取消按钮；审批弹窗二期再做。

简单问答 UX 保持不变。

---

## 11. 分阶段落地

### M0 — 可演示的最小 Agent（约 1–2 周）

- 任务创建 + 同进程执行 + 事件 SSE
- 工具：`datetime_calc` + `kb_search`
- 固定/轻量 Planner（模板或一次 LLM 规划）+ tool-calling 循环
- 预算熔断；最终答案写入 `messages`
- 简单问题仍走原 `/api/chat/stream`

**验收**：用户问「结合某知识库说明 X，并计算距 Y 还有几天」——可见检索与计算步骤，答案引用工具结果。

### M1 — 规划可见与稳定性 ✅

- LLM Planner + 失败 Replan（`AGENT_MAX_REPLANS`，解析失败回退规则计划）
- 事件与步骤落库完善；进程重启后未完成任务有限重试（`AGENT_MAX_RETRIES`），超限则失败
- Token/步数统计进管理后台：`GET /api/admin/statistics/agent`

### M2 — 安全与人机协同 ✅

- 工具风险分级 + HITL（`HITL_RISKS`，`awaiting_approval` + `/approvals`）
- 可选外部工具：`web_search`、`http_request`（域名白名单 `HTTP_ALLOWLIST`）
- 观察脱敏增强 + 审计导出：`GET /api/agent/tasks/{id}/audit`

### M3 — 体验与多智能体（按需）

- 长期记忆、会话摘要
- 领域子 Agent（检索 / 写作）由 Supervisor 调度
- 评估集：任务成功率、平均步数、费用、用户满意度

---

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 延迟与费用上升 | Router 分流；弱模型执行；缓存检索；预算熔断 |
| 工具幻觉调用 | Schema 强校验；未知工具当观察回灌；description 精写 |
| 死循环 | 硬上限 + 重复工具调用检测 |
| 断线丢任务 | 执行与 SSE 解耦；事件可重放 |
| 权限绕过 | 工具内二次鉴权；禁止模型指定任意用户资源 |
| 与旧接口双轨复杂度 | 明确 `mode`；共享会话与 messages；文档同步更新 |

---

## 13. 成功标准

1. **功能**：至少 2 个真实工具参与的多步任务端到端可跑通，前端可展示步骤。
2. **兼容**：未开 Agent 时，原问答延迟与行为无明显回退。
3. **可控**：超预算必停；用户可取消；知识库越权返回明确错误而非泄露。
4. **可运维**：每次任务可追溯 plan、工具调用、llm_calls 与最终 message。

---

## 14. 待评审决策

请产品/研发确认：

1. **入口形态**：新 `/api/agent` 为主，还是强化现有 `/api/chat` 的 `mode=agent`？
2. **一期工具范围**：是否包含联网搜索，还是仅站内知识库 + 计算？
3. **执行部署**：一期同进程线程池是否可接受？（接受则 M0 最快）
4. **简单问答**：Router 用规则（关键词/长度）还是小模型分类？
5. **HITL**：是否必须进入 M0，还是放到 M2？

---

## 15. 附录：与主流方案的对照（简表）

| 主流能力 | 本设计落点 |
|---|---|
| ReAct / tool loop | Executor 内核 |
| Plan-and-Execute | Planner + Replanner 外壳 |
| Agentic RAG | `kb_search` 工具化 |
| HITL | `awaiting_approval` + 审批 API |
| Streaming observability | `agent_events` + SSE |
| Multi-agent | M3 可选，不阻塞一期 |
| LangGraph persistence | 一期自建任务表；复杂度上升后再评估引入 |

---

文档版本：v0.1（独立草案，供评审后进入接口定稿与 M0 开发）。
