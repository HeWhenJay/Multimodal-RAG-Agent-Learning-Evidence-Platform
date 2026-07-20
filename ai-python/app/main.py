from pathlib import Path
import sys

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

# 直接运行 app/main.py 时，先进入统一启动入口，避免配置加载晚于路由初始化。
if __name__ == "__main__":
    from run import main as run_ai_service

    run_ai_service()
    raise SystemExit

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.agent import router as agent_router
from app.api.auth import router as auth_router
from app.api.logs import router as logs_router
from app.api.page_data import router as page_data_router
from app.api.rag_control import router as rag_control_router
from app.core.result import BusinessError, Result


app = FastAPI(
    title="Multimodal RAG Agent Learning Evidence Platform - AI Service",
    version="0.1.0",
)


@app.exception_handler(BusinessError)
async def handle_business_error(_: Request, error: BusinessError) -> JSONResponse:
    """将公开领域的受控错误统一为既有结果信封。"""
    return JSONResponse(status_code=200, content=Result.failure(error.message).model_dump())


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, error: RequestValidationError):
    """将公开认证和 RAG 参数错误转换为既有 `Result` 信封。"""
    if request.url.path.startswith(("/api/auth", "/api/rag", "/api/agent")):
        return JSONResponse(status_code=200, content=Result.failure(public_validation_message(error)).model_dump())
    return await request_validation_exception_handler(request, error)


def public_validation_message(error: RequestValidationError) -> str:
    """提取公开认证和 RAG 参数的稳定中文校验提示。"""
    for item in error.errors():
        location = item.get("loc", ())
        field = location[-1] if location else ""
        if field == "account":
            return "账号不能为空"
        if field == "password":
            return "密码不能为空"
        if field == "question":
            return "问题不能为空"
        if field == "title":
            return "标题不能为空"
        if field == "content":
            return "内容不能为空"
        if field == "file":
            return "上传文件不能为空"
    return "请求参数不合法"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-python-rag"}


app.include_router(agent_router)
app.include_router(auth_router)
app.include_router(logs_router)
app.include_router(page_data_router)
app.include_router(rag_control_router)
