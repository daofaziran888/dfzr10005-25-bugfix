"""数据回收站业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "recycle"
REQUIRED_FIELDS = ["记录编号", "表名称", "删除时间"]
STATUS_ORDER = ["待恢复", "已恢复", "已清理", "已过期"]
ACTION_RULES = {"恢复数据": "待恢复", "永久清理": "已恢复", "延期保留": "已过期"}
NEGATIVE_ACTIONS = []


class RecycleService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        rule_code: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if rule_code:
            rows = [row for row in rows if str(row.get("来源规则编号", "")) == rule_code]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        # 可选联动字段（如来源保留规则编号）也要落库，否则清理资格无法挂接
        for optional in ("来源规则编号",):
            if optional in values:
                entry[optional] = values[optional]
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        # 挂接到保留规则的新回收记录，立即按规则当前版本算出清理资格
        rule_code = entry.get("来源规则编号")
        if rule_code:
            from app.services.retention import service as retention_service

            linked = next(
                (rule for rule in store.rows("retention") if rule.get("规则编号") == rule_code),
                None,
            )
            if linked is not None:
                retention_service.sync_recycle_eligibility(linked)
            else:
                entry["清理资格"] = "不可清理"
                entry["判定依据"] = f"来源规则 {rule_code} 不存在，默认保留待人工确认"
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"回收记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据回收站可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"回收记录已{action}"
