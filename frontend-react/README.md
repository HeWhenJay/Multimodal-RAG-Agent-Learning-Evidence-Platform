# React 前端

基于 Stitch 生成页 `学迹智配管理后台` 的视觉风格实现。

## 启动

```powershell
cd frontend-react
npm ci
npm run dev
```

默认端口：`5178`。`VITE_API_PROXY_TARGET` 未设置时，`/api/*` 默认代理到 `http://127.0.0.1:8090` 的 FastAPI 服务。

## 路由

- `/login`：登录页
- `/`：工作台
- `/materials`：学习资料
- `/preview/material/:id`：资料原文与 evidence 预览
- `/videos`：视频 evidence 播放与复习
- `/reviews`：复习中心与资料归档
- `/reviews/cards`：复习卡片库
- `/reviews/folders/:folderId`：文件夹详情与文件夹内复习
- `/agent`：Agent 会话、LangGraph 任务、审批、记忆与 SSE 工作台
- `/settings`：系统设置
