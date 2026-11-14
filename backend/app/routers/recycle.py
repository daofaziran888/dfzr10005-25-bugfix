"""数据回收站接口：维护回收记录，覆盖恢复数据、永久清理、延期保留等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.recycle import RecycleService

router = APIRouter(prefix="/api/recycle", tags=["数据回收站"])

service = RecycleService()

LIST_FIELDS = ["记录编号", "表名称", "删除时间", "回收原因", "数据大小", "可恢复期", "恢复状态", "清理时间"]
STATUSES = ["待恢复", "已恢复", "已清理", "已过期"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="待恢复、已恢复、已清理、已过期"),
    rule_code: str | None = Query(default=None, description="按来源保留规则编号定位受影响记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、状态与来源规则过滤数据回收站列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, rule_code=rule_code, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# 固定路径必须声明在 /{entry_id} 之前，否则会被参数路由吞掉返回 422
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据回收站清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "recycle", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条回收记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"回收记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条回收记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="回收记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条回收记录执行恢复数据、永久清理、延期保留；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
