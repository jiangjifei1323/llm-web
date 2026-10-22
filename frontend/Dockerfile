# 问答助手前端（Vue 3 + Vite）镜像：多阶段构建

# 构建阶段：安装依赖并产出静态文件
# 用 Debian(glibc) 版而非 alpine(musl)：新版 TypeScript 的原生引擎
# @typescript-native-bridge 提供的是 glibc 构建，在 alpine 上会 dlopen 失败(ERR_DLOPEN_FAILED)。
FROM node:22-slim AS build
WORKDIR /app

# 先复制清单，命中依赖缓存
COPY package.json package-lock.json ./
RUN npm ci

# 复制源码并构建（含 vue-tsc 类型检查 + vite build，产物在 dist/）
COPY . .
RUN npm run build

# 运行阶段：nginx 托管静态资源，并把 /api 反向代理到后端
FROM nginx:1.27-alpine AS runtime
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
