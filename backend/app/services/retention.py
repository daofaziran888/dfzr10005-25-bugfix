"""数据保留策略业务规则。

这里集中修复原有实现的几处断点：

1. 旧 ACTION_RULES 把动作直接映射成固定状态、且不校验当前状态，
   于是草稿规则能直接归档、已废止规则又能被改回已到期。这里改成严格状态机：
   草稿 -> 生效中 -> 已到期 -> 已废止，只能顺序推进，废止为终态，禁止回退与跳级。
2. 例外清单/合规依据的修改不再原地覆盖：每次修改冻结旧版本，生成新版本，
   并逐条写审计事件（变更前后值完整留存）。
3. 清理资格只认当前“生效中”规则的当前版本；已废止规则仅供查询，
   生命周期页面不再拿旧结论清理数据。
4. 重复归档 / 两个管理员争抢同一规则：按规则加锁并在锁内复检状态，
   只接受一个有效结论，另一次请求返回冲突说明（409）。
"""
from __future__ import annotations

import threading
from typing import Any

from app.locking import module_lock
from app.services.audit_events import record_audit_event
from app.store import store

MODULE = "retention"
REQUIRED_FIELDS = ["规则编号", "数据集范围", "保留期限"]
# 参与版本快照的业务字段：这些字段的修改都要冻结旧版并生成审计事件
VERSION_FIELDS = ["数据集范围", "保留期限", "过期动作", "例外清单", "合规依据", "执行周期"]
STATUS_ORDER = ["草稿", "生效中", "已到期", "已废止"]
TERMINAL_STATUS = STATUS_ORDER[-1]

# 动作只能把规则推进到状态机中的下一个状态；归档规则对应 生效中 -> 已到期
ACTION_NEXT = {
    "启用规则": "生效中",
    "归档规则": "已到期",
    "废止规则": "已废止",
}
# 每个动作允许的前置状态，杜绝“草稿直接归档”“废止后回到生效”这类越序流转
ACTION_ALLOWED_FROM = {
    "启用规则": {"草稿"},
    "归档规则": {"生效中"},
    "废止规则": {"已到期"},
}


class RetentionConflict(Exception):
    """状态机冲突：重复执行终态动作、并发争抢导致结论已被另一方抢先写出。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize_exceptions(value: Any) -> list[str]:
    """例外清单入参兼容数组、逗号/顿号分隔的字符串，统一成去空白后的字符串列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value)
    for separator in ("，", "、", ";", "；", "\n"):
        text = text.replace(separator, ",")
    return [part.strip() for part in text.split(",") if part.strip()]


def _snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    """冻结当前业务字段，作为历史版本/审计前值。"""
    return {
        "数据集范围": entry.get("数据集范围", ""),
        "保留期限": entry.get("保留期限", ""),
        "过期动作": entry.get("过期动作", ""),
        "例外清单": list(entry.get("例外清单") or []),
        "合规依据": entry.get("合规依据", ""),
        "执行周期": entry.get("执行周期", ""),
    }


class RetentionService:
    # 规则级互斥：两个管理员争抢同一规则时，第二个请求在锁内看到的已是新状态
    _rule_locks: dict[int, threading.RLock] = {}
    _rule_locks_guard = threading.Lock()

    def _rule_lock(self, entry_id: int):
        with self._rule_locks_guard:
            return self._rule_locks.setdefault(entry_id, threading.RLock())

    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        exception: str | None = None,
        basis: str | None = None,
        sort: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if exception:
            rows = [
                row
                for row in rows
                if any(exception in item for item in (row.get("例外清单") or []))
            ]
        if basis:
            rows = [row for row in rows if basis in str(row.get("合规依据", ""))]
        # 按合规依据排序（默认升序，sort=-合规依据 时降序）
        if sort and sort.lstrip("-") == "合规依据":
            descending = sort.startswith("-")
            rows = sorted(rows, key=lambda row: str(row.get("合规依据", "")), reverse=descending)
        total = len(rows)
        page = max(page, 1)
        start = (page - 1) * size
        items = [self._with_affected(row) for row in rows[start:start + size]]
        return items, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        detail = dict(entry)
        detail["versions"] = list(entry.get("versions") or [])
        detail["affected_records"] = self.affected_records(entry_id)
        return detail

    def get_versions(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return list(entry.get("versions") or [])

    def affected_records(self, entry_id: int) -> list[dict[str, Any]]:
        """定位该规则当前版本影响到的回收记录（按规则编号挂接）。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return []
        code = str(entry.get("规则编号", ""))
        return [
            {
                "记录编号": row.get("记录编号", ""),
                "表名称": row.get("表名称", ""),
                "恢复状态": row.get("恢复状态", ""),
                "清理资格": row.get("清理资格", ""),
            }
            for row in store.rows("recycle")
            if str(row.get("来源规则编号", "")) == code
        ]

    def _with_affected(self, entry: dict[str, Any]) -> dict[str, Any]:
        view = dict(entry)
        view["affected_records"] = self.affected_records(int(entry.get("id", 0)))
        return view

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        with module_lock(MODULE):
            rows = store.rows(MODULE)
            code = str(values.get("规则编号")).strip()
            if any(str(row.get("规则编号", "")) == code for row in rows):
                raise RetentionConflict(f"规则编号 {code} 已存在，不能重复登记")
            entry_id = store.next_id(MODULE)
            entry: dict[str, Any] = {
                "id": entry_id,
                "规则编号": code,
                "数据集范围": str(values.get("数据集范围") or "").strip(),
                "保留期限": str(values.get("保留期限") or "").strip(),
                "过期动作": str(values.get("过期动作") or "").strip(),
                "例外清单": normalize_exceptions(values.get("例外清单")),
                "合规依据": str(values.get("合规依据") or "").strip(),
                "执行周期": str(values.get("执行周期") or "").strip(),
            }
            entry["status"] = STATUS_ORDER[0]
            entry["规则状态"] = entry["status"]
            entry["pending"] = True
            entry["abnormal"] = False
            first_version = {
                **_snapshot(entry),
                "version": 1,
                "生效状态": "当前版本",
                "状态": entry["status"],
            }
            entry["current_version"] = first_version
            entry["versions"] = [first_version]
            rows.append(entry)
            record_audit_event(
                target=code,
                change_type="登记保留规则",
                before=None,
                after=_snapshot(entry),
                operator=str(values.get("operator") or "系统"),
            )
            return self._with_affected(entry), []

    # ------------------------------------------------------------------ 动作
    def run_action(
        self, entry_id: int, action: str, *, operator: str = "系统"
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"保留规则 {entry_id} 不存在或已归档"
        if action not in ACTION_NEXT:
            return None, f"动作「{action}」不属于数据保留策略可执行范围"
        with self._rule_lock(entry_id):
            # 锁内复检：并发争抢时，另一个管理员可能已经把状态推进了
            current_status = str(entry.get("status", ""))
            allowed = ACTION_ALLOWED_FROM[action]
            if current_status not in allowed:
                target = ACTION_NEXT[action]
                # 已经处于该动作的目标结论：重复执行，明确告知冲突而不是再写一遍
                if current_status == target or STATUS_ORDER.index(current_status) > STATUS_ORDER.index(target):
                    raise RetentionConflict(
                        f"规则 {entry.get('规则编号')} 当前为「{current_status}」，"
                        f"动作「{action}」的结论已存在或状态已越过该环节，本次请求与既有结论冲突，未重复执行"
                    )
                expected = "、".join(allowed)
                return (
                    None,
                    f"规则当前为「{current_status}」，动作「{action}」仅允许在「{expected}」状态执行；"
                    f"状态只能按 草稿→生效中→已到期→已废止 顺序推进，不能回退或跳级",
                )
            before_snapshot = _snapshot(entry)
            before_status = current_status
            target = ACTION_NEXT[action]
            entry["status"] = target
            entry["规则状态"] = target
            entry["pending"] = target != TERMINAL_STATUS
            entry["abnormal"] = False
            self._mark_current_version(entry, target)
            record_audit_event(
                target=str(entry.get("规则编号", "")),
                change_type=f"状态流转：{before_status}→{target}（{action}）",
                before={"status": before_status, **before_snapshot},
                after={"status": target, **_snapshot(entry)},
                operator=operator,
            )
            # 动作结论联动回收记录的清理资格
            self.sync_recycle_eligibility(entry)
            return self._with_affected(entry), f"保留规则已{action}，状态由「{before_status}」推进到「{target}」"

    # ------------------------------------------------------------------ 修改
    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"保留规则 {entry_id} 不存在或已归档"
        with self._rule_lock(entry_id):
            status = str(entry.get("status", ""))
            if status == TERMINAL_STATUS:
                raise RetentionConflict(
                    f"规则 {entry.get('规则编号')} 已废止，仅供查询，不能再修改例外清单或合规依据"
                )
            changes: dict[str, Any] = {}
            for field in VERSION_FIELDS:
                if field not in values or values[field] is None:
                    continue
                if field == "例外清单":
                    new_value = normalize_exceptions(values[field])
                else:
                    new_value = str(values[field]).strip()
                if new_value != entry.get(field):
                    changes[field] = new_value
            if not changes:
                return self._with_affected(entry), "提交内容与当前版本一致，未产生新版本"

            before_snapshot = _snapshot(entry)
            code = str(entry.get("规则编号", ""))
            operator = str(values.get("operator") or "系统")
            # 先冻结旧版本：旧版清单与旧期限原样保留
            old_versions = entry.setdefault("versions", [])
            for version in old_versions:
                if version.get("生效状态") == "当前版本":
                    version["生效状态"] = "历史版本"
                    version["状态"] = "已失效"
            for field, new_value in changes.items():
                entry[field] = new_value
            new_version = {
                **_snapshot(entry),
                "version": len(old_versions) + 1,
                "生效状态": "当前版本",
                "状态": status,
            }
            old_versions.append(new_version)
            entry["current_version"] = new_version
            # 每个被修改的字段各生成一条审计事件，前后值完整冻结
            for field, new_value in changes.items():
                record_audit_event(
                    target=code,
                    change_type=f"修改{field}",
                    before=before_snapshot.get(field),
                    after=new_value,
                    operator=operator,
                )
            record_audit_event(
                target=code,
                change_type=f"版本冻结：v{new_version['version'] - 1}→v{new_version['version']}",
                before=before_snapshot,
                after=_snapshot(entry),
                operator=operator,
            )
            # 例外清单/合规依据变化后，按新版本重新计算清理资格，杜绝生命周期页沿用旧结论
            self.sync_recycle_eligibility(entry)
            return self._with_affected(entry), f"已冻结旧版本并生成 v{new_version['version']}，审计事件已记录"

    # ------------------------------------------------------------------ 清理联动
    def _mark_current_version(self, entry: dict[str, Any], status: str) -> None:
        for version in entry.get("versions") or []:
            if version.get("生效状态") == "当前版本":
                version["状态"] = status
        current = entry.get("current_version")
        if isinstance(current, dict):
            current["状态"] = status

    def evaluate_record(self, entry: dict[str, Any], table_name: str) -> tuple[str, str]:
        """按当前已生效版本判断单条回收记录的清理资格。

        返回 (资格结论, 判定依据)。只有“生效中”规则参与清理判定；
        已废止规则只供查询，草稿/已到期规则同样不产生清理结论。
        """
        code = str(entry.get("规则编号", ""))
        status = str(entry.get("status", ""))
        if status != "生效中":
            reason = {
                "草稿": "规则尚未启用，不参与清理",
                "已到期": "规则已到期，仅保留查询能力",
                "已废止": "规则已废止，仅供查询，不能继续参与清理",
            }.get(status, f"规则状态为{status}，不参与清理")
            return "不可清理", f"{code}：{reason}"
        current = entry.get("current_version") or {}
        exceptions = list(current.get("例外清单") or [])
        basis = str(current.get("合规依据", ""))
        period = str(current.get("保留期限", ""))
        if table_name in exceptions:
            return (
                "例外保留",
                f"{code} 当前生效版本（{period}）：「{table_name}」在例外清单内，按合规依据「{basis}」保留，不得清理",
            )
        return (
            "可清理",
            f"{code} 当前生效版本（{period}）：「{table_name}」不在例外清单内，按合规依据「{basis}」到期清理",
        )

    def cleanup_results(self) -> list[dict[str, Any]]:
        """生命周期结果清单：每条回收记录都按规则的当前版本给出清理结论。"""
        rules_by_code = {
            str(entry.get("规则编号", "")): entry for entry in store.rows(MODULE)
        }
        results: list[dict[str, Any]] = []
        for record in store.rows("recycle"):
            code = str(record.get("来源规则编号", ""))
            table_name = str(record.get("表名称", ""))
            rule = rules_by_code.get(code)
            if rule is None:
                eligibility, reason = "不可清理", f"未挂接有效保留规则（来源规则 {code or '为空'}），默认保留待人工确认"
                rule_status = "未挂接"
            else:
                eligibility, reason = self.evaluate_record(rule, table_name)
                rule_status = str(rule.get("status", ""))
            results.append(
                {
                    "记录编号": record.get("记录编号", ""),
                    "表名称": table_name,
                    "来源规则编号": code,
                    "规则状态": rule_status,
                    "清理资格": eligibility,
                    "判定依据": reason,
                    "恢复状态": record.get("恢复状态", ""),
                }
            )
        return results

    def sync_recycle_eligibility(self, entry: dict[str, Any]) -> None:
        """规则变化后重算挂接回收记录的清理资格并回写，联动业务页面同步显示。"""
        code = str(entry.get("规则编号", ""))
        for record in store.rows("recycle"):
            if str(record.get("来源规则编号", "")) != code:
                continue
            eligibility, reason = self.evaluate_record(entry, str(record.get("表名称", "")))
            record["清理资格"] = eligibility
            record["判定依据"] = reason

    def sync_all_eligibility(self) -> None:
        """启动/数据装载后全量重算一次，保证回收记录与当前规则结论一致。"""
        with module_lock(MODULE):
            for entry in store.rows(MODULE):
                self.sync_recycle_eligibility(entry)
            # 没有挂接规则的回收记录也给一个兜底结论
            known_codes = {str(entry.get("规则编号", "")) for entry in store.rows(MODULE)}
            for record in store.rows("recycle"):
                if str(record.get("来源规则编号", "")) not in known_codes:
                    record["清理资格"] = "不可清理"
                    record["判定依据"] = "未挂接有效保留规则，默认保留待人工确认"


# 供启动流程复用的单例
service = RetentionService()
