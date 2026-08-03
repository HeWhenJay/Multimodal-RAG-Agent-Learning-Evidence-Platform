"""复习资料拖拽顺序的事务与归属边界测试。"""

from contextlib import contextmanager

import pytest

from app.core.result import BusinessError
from app.review.repository import DatabaseReviewTransaction
from app.review.service import ReviewService


class OrderingCursor:
    """模拟设置行锁、资料行锁和集合更新所需的最小游标。"""

    def __init__(self, material_ids: list[int]) -> None:
        self.material_ids = material_ids
        self.fetchone_value = None
        self.fetchall_value: list[dict[str, int]] = []
        self.executions: list[tuple[str, object]] = []

    def execute(self, statement, params) -> None:
        """根据 SQL 类型准备下一次读取结果并记录更新参数。"""
        normalized = " ".join(str(statement).split())
        self.executions.append((normalized, params))
        self.fetchone_value = None
        self.fetchall_value = []
        if "SELECT * FROM learning_evidence.learning_review_setting" in normalized:
            self.fetchone_value = {
                "user_id": "7",
                "enabled": True,
                "desired_retention": 0.9,
                "daily_limit": 20,
                "reminder_time": "09:00",
                "timezone": "Asia/Shanghai",
            }
        elif "SELECT rm.material_id" in normalized:
            self.fetchall_value = [{"material_id": item} for item in self.material_ids]

    def fetchone(self):
        return self.fetchone_value

    def fetchall(self):
        return self.fetchall_value


def database_transaction(material_ids: list[int]) -> tuple[DatabaseReviewTransaction, OrderingCursor]:
    """构造使用纯文本 SQL 的数据库事务，便于核对参数化语句。"""
    cursor = OrderingCursor(material_ids)
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query.format(schema="learning_evidence")  # type: ignore[method-assign]
    return transaction, cursor


def test_reorder_places_requested_materials_first_and_keeps_remaining_order() -> None:
    """本次拖拽资料应前置，其余资料保持已有相对顺序并一次集合更新。"""
    transaction, cursor = database_transaction([12, 14, 13])

    assert transaction.reorder_review_materials("7", [13, 12]) == [13, 12]

    update_sql, update_params = next(
        (sql, params) for sql, params in cursor.executions if "SET display_order = ordered.position" in sql
    )
    assert update_params == ([13, 12, 14], "7")
    assert "UNNEST(%s::BIGINT[]) WITH ORDINALITY" in update_sql
    assert "rm.user_id = %s" in update_sql
    assert "rm.display_order IS DISTINCT FROM ordered.position" in update_sql
    select_sql = next(sql for sql, _params in cursor.executions if "SELECT rm.material_id" in sql)
    assert "learning_review_folder_material" in select_sql
    assert "folder_material.material_id = rm.material_id" in select_sql


def test_reorder_rejects_cross_user_or_missing_material_without_any_update() -> None:
    """任一资料未命中当前用户时必须整批失败，不能写入部分顺序。"""
    transaction, cursor = database_transaction([12])

    assert transaction.reorder_review_materials("7", [12, 99]) is None
    assert not any("SET display_order = ordered.position" in sql for sql, _params in cursor.executions)


class OrderingServiceTransaction:
    """向服务层返回可控的批量排序结果。"""

    def __init__(self, result: list[int] | None) -> None:
        self.result = result
        self.calls: list[tuple[str, list[int]]] = []

    def reorder_review_materials(self, user_id: str, material_ids: list[int]) -> list[int] | None:
        self.calls.append((user_id, material_ids))
        return self.result


class OrderingRepository:
    """为服务层提供单个可观察事务。"""

    def __init__(self, transaction: OrderingServiceTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


def test_service_returns_stable_order_and_hides_ownership_failures() -> None:
    """服务响应保留拖拽顺序，越权和不存在资料统一为同一业务错误。"""
    success_transaction = OrderingServiceTransaction([13, 12])
    service = ReviewService(repository=OrderingRepository(success_transaction))

    result = service.reorder_due_groups([13, 12], "7")

    assert result.model_dump() == {"materialIds": [13, 12], "orderedCount": 2}
    assert success_transaction.calls == [("7", [13, 12])]

    missing_service = ReviewService(repository=OrderingRepository(OrderingServiceTransaction(None)))
    with pytest.raises(BusinessError, match="复习资料不存在"):
        missing_service.reorder_due_groups([13, 99], "7")
