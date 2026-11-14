"""数据保留策略接口：维护保留规则，覆盖启用规则、废止规则、归档规则等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.retention import RetentionConflict, RetentionService

router = APIRouter(prefix="/api/retention", tags=["数据保留策略"])

service = RetentionService()

LIST_FIELDS = ["规则编号", "数据集范围", "保留期限", "过期动作", "例外清单", "合规依据", "执行周期", "规则状态"]
STATUSES = ["草稿", "生效中", "已到期", "已废止"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、生效中、已到期、已废止"),
    exception: str | None = Query(default=None, description="按例外清单内容筛选并定位受影响记录"),
    basis: str | None = Query(default=None, description="按合规依据筛选"),
    sort: str | None = Query(default=None, description="按合规依据排序，传 合规依据 或 -合规依据"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """支持按规则编号、状态、例外清单、合规依据筛选，按合规依据排序，并分页定位受影响记录。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    page = max(page, 1)
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        exception=exception,
        basis=basis,
        sort=sort,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据保留策略清单：返回当前全量数据（含版本与受影响记录）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "retention", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条保留规则明细，含全部历史版本与受影响记录；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"保留规则 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/versions", response_model=list[dict])
def get_versions(entry_id: int) -> list[dict[str, Any]]:
    """查看规则的版本链：旧版例外清单与旧期限均冻结保留。"""
    versions = service.get_versions(entry_id)
    if versions is None:
        raise HTTPException(status_code=404, detail=f"保留规则 {entry_id} 不存在或已归档")
    return versions


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条保留规则，缺字段时说明原因而不是静默丢弃；编号冲突返回 409。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except RetentionConflict as conflict:
        raise HTTPException(status_code=409, detail=conflict.message)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="保留规则已登记", entry=entry)


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修改例外清单/合规依据等字段：冻结旧版本、生成新版本并写审计事件。"""
    try:
        entry, message = service.update_entry(entry_id, payload.values)
    except RetentionConflict as conflict:
        raise HTTPException(status_code=409, detail=conflict.message)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条保留规则执行启用规则、废止规则、归档规则。

    状态只能顺序推进；重复执行或并发争抢造成的冲突返回 409 并说明冲突原因。
    """
    action = str(payload.values.get("action") or "").strip()
    operator = str(payload.values.get("operator") or "系统")
    try:
        entry, message = service.run_action(entry_id, action, operator=operator)
    except RetentionConflict as conflict:
        raise HTTPException(status_code=409, detail=conflict.message)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
