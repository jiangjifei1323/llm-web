# llm-web 问答助手

基于 **Vue 3 + FastAPI** 的前后端分离问答助手。前端提供对话界面，后端对接大模型（OpenAI 兼容接口）完成问答。

## 技术栈

| 分层 | 选型 |
|---|---|
| 前端 | Vue 3、Vue Router 5、Pinia 4、Vite 8、TypeScript |
| 后端 | Python 3.13、FastAPI、Uvicorn |
| 大模型 | 阿里云百炼 Token Plan（OpenAI 兼容接口） |
| 前端测试 | Vitest（单测）、Playwright（E2E） |
| 代码质量 | ESLint + oxlint + oxfmt |
| 后端环境 | uv（`pyproject.toml` + `uv.lock`） |

## 目录结构

```
llm-web/
├─ backend/            # FastAPI 后端
│  ├─ app/
│  │  ├─ main.py       # 应用入口，/api/chat、/api/health 接口
│  │  └─ llm.py        # 大模型客户端（未配置 Key 时返回 mock 回答）
│  ├─ .env             # 本地密钥配置（不提交）
│  ├─ .env.example     # 配置模板
│  ├─ pyproject.toml   # 依赖声明
│  └─ uv.lock          # 依赖锁定文件
└─ frontend/           # Vue 前端
   ├─ src/
   │  ├─ api/chat.ts        # 问答接口封装
   │  ├─ views/ChatView.vue # 问答页面
   │  ├─ router/            # 路由
   │  └─ stores/            # Pinia 状态
   ├─ e2e/                  # Playwright E2E 测试
   └─ vite.config.ts        # 含 /api 到后端 8000 端口的开发代理
```

## 环境要求

- Node.js `^22.18.0 || >=24.12.0`
- Python `>=3.13`
- [uv](https://docs.astral.sh/uv/)（安装：`pip install uv` 或官方脚本）

## 快速开始

### 1. 配置后端密钥

```sh
cd backend
copy .env.example .env    # Windows；Linux/macOS 用 cp
```

编辑 `.env`，填入大模型服务配置（阿里云 Token Plan 请使用专属 Base URL）：

```ini
LLM_API_KEY=sk-xxx
LLM_BASE_URL=https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen3.7-plus
LLM_TEMPERATURE=0.7
```

> 不配置 `LLM_API_KEY` 时后端返回 mock 回答，前端仍可正常开发。

### 2. 启动后端

```sh
cd backend
uv sync                                            # 首次或依赖变更后执行
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

接口文档：<http://127.0.0.1:8000/docs>

### 3. 启动前端

```sh
cd frontend
npm install
npm run dev
```

访问 <http://localhost:5173>，`/api` 请求由 Vite 自动代理到后端 8000 端口。

## 前端常用命令（在 `frontend/` 下执行）

```sh
npm run dev          # 开发（热重载）
npm run build        # 类型检查 + 生产构建
npm run preview      # 预览构建产物
npm run test:unit    # Vitest 单元测试
npm run lint         # ESLint + oxlint 检查并修复
npm run format       # oxfmt 格式化
```

E2E 测试（首次需 `npx playwright install` 安装浏览器；CI 模式下需先 `npm run build`）：

```sh
npm run test:e2e
npm run test:e2e -- --project=chromium    # 仅 Chromium
npm run test:e2e -- --debug               # 调试模式
```

## 后端常用命令（在 `backend/` 下执行）

```sh
uv sync                  # 按 uv.lock 安装/对齐环境
uv add <包名>            # 新增依赖（自动更新 pyproject.toml 与 uv.lock）
uv remove <包名>         # 移除依赖
uv lock                  # 依赖声明变更后重新锁定
```

## 分支约定

- `dev`：日常开发分支，功能完成后合并回 `main`；
- `main`：稳定分支。

功能开发建议从 `dev` 切出独立分支：

```sh
git switch dev && git pull
git switch -c feature/xxx
```

## 推荐 IDE 设置

- [VS Code](https://code.visualstudio.com/) + [Vue (Official)](https://marketplace.visualstudio.com/items?itemName=Vue.volar)（禁用 Vetur）；
- 本项目使用 [typescript-native-bridge](https://github.com/johnsoncodehk/typescript-native-bridge) 替代官方 TypeScript 包：安装依赖后在 VS Code 提示中选择 **Use Workspace Version**（或执行命令 **TypeScript: Select TypeScript Version** 手动选择）；
- `.vue` 文件的类型检查依赖 Volar，命令行使用 `vue-tsc`。

## 浏览器调试建议

- Chromium 系浏览器（Chrome / Edge / Brave）：安装 [Vue.js devtools](https://chromewebstore.google.com/detail/vuejs-devtools/nhdogjmejiglipccpnnnanhbledajbpd)，并在 DevTools 中开启 [Custom Object Formatter](http://bit.ly/object-formatters)；
- Firefox：安装 [Vue.js devtools](https://addons.mozilla.org/en-US/firefox/addon/vue-js-devtools/)，并开启 [Custom Object Formatter](https://fxdx.dev/firefox-devtools-custom-object-formatters/)。

## 相关文档

- [Vite 配置参考](https://vite.dev/config/)
- [FastAPI 文档](https://fastapi.tiangolo.com/zh/)
- [uv 文档](https://docs.astral.sh/uv/)
