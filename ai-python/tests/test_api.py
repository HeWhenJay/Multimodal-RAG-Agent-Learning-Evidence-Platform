"""纯 Python 公开 API 注册门禁。"""

from __future__ import annotations

from app.main import app


def test_public_api_surface_contains_all_frontend_rag_routes() -> None:
    """RAG 控制面必须保留 React 已调用的全部公开路径。"""
    paths = registered_paths()

    assert {
        "/api/rag/overview",
        "/api/rag/materials",
        "/api/rag/materials/{material_id}",
        "/api/rag/materials/{material_id}/evidences",
        "/api/rag/materials/{material_id}/preview",
        "/api/rag/materials/text",
        "/api/rag/materials/upload",
        "/api/rag/materials/upload/chunk",
        "/api/rag/materials/{material_id}/reindex",
        "/api/rag/query",
        "/api/rag/query/history",
        "/api/rag/query/tasks",
        "/api/rag/query/tasks/{task_id}",
    }.issubset(paths)


def test_no_legacy_internal_http_routes_are_registered() -> None:
    """FastAPI 不再暴露仅供旧后端调用的内部 HTTP API。"""
    paths = registered_paths()

    assert not any(path.startswith("/internal/") for path in paths)


def registered_paths() -> set[str]:
    """兼容 FastAPI 在路由列表中保留的无路径包装对象。"""
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        nested_router = getattr(route, "original_router", None)
        nested_routes = getattr(nested_router, "routes", None)
        if nested_routes is not None:
            paths.update(registered_paths_from(nested_routes))
    return paths


def registered_paths_from(routes: object) -> set[str]:
    """递归展开 FastAPI 惰性包含的子路由。"""
    paths: set[str] = set()
    for route in routes:  # type: ignore[union-attr]
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        nested_router = getattr(route, "original_router", None)
        nested_routes = getattr(nested_router, "routes", None)
        if nested_routes is not None:
            paths.update(registered_paths_from(nested_routes))
    return paths
