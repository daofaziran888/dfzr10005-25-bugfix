"""审计事件写入：保留规则的每次关键变更都要冻结变更前后值并落到审计模块。

审计记录直接写入 audit 表，审计页面可以按变更对象（规则编号）检索。
前后值统一冻结成 JSON 字符串，避免后续再改规则时把历史审计里的旧值带跑。
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

from app.store import store

AUDIT_MODULE = "audit"


def _freeze(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def record_audit_event(
    *,
    target: str,
    change_type: str,
    before: Any,
    after: Any,
    operator: str = "系统",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """落一条审计事件，before/after 都会深拷贝后冻结，杜绝引用被后续修改污染。"""
    frozen_before = _freeze(copy.deepcopy(before))
    frozen_after = _freeze(copy.deepcopy(after))
    rows = store.rows(AUDIT_MODULE)
    event_id = store.next_id(AUDIT_MODULE)
    event = {
        "id": event_id,
        "status": "已记录",
        "pending": True,
        "abnormal": False,
        "记录编号": f"AUDI-E{event_id:04d}",
        "变更对象": target,
        "变更类型": change_type,
        "变更前值": frozen_before,
        "变更后值": frozen_after,
        "操作人": operator or "系统",
        "操作时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "复核状态": "待复核",
    }
    if extra:
        event.update(extra)
    rows.append(event)
    return event
