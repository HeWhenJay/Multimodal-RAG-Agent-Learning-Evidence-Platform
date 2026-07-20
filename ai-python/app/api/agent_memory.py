"""历史内部记忆路由占位模块。

公开记忆 CRUD 已统一至 ``app.api.agent`` 的 ``/api/agent/memories*``。
保留空路由仅避免旧导入路径导致服务启动失败，不能再暴露内部令牌接口。
"""

from fastapi import APIRouter


router = APIRouter(tags=["Agent Memory"])
