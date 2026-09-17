# 本文件定义差旅申请单和审批单的状态机及自动完成规则。
# 定义 TravelOrderStatus、ApprovalRecordStatus、transition_travel_order 和 auto_complete_orders。
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent_api.config.city_tier import CityTierConfig, load_city_tier_config
from travel_agent_api.persistence.database import metadata
from travel_agent_api.persistence.models import ApprovalRecord, BookingRecord, TravelOrder

# 使用表元数据列对象做条件比较，避免 SQLModel 类属性在静态检查中被推断成实例类型。
_ORDER_COLUMNS = metadata.tables["travel_order"].c
_APPROVAL_COLUMNS = metadata.tables["approval_record"].c
_BOOKING_COLUMNS = metadata.tables["booking_record"].c

# 冲突严重等级排序权重，用于结果稳定排序与最高等级汇总。
_CONFLICT_SEVERITY_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
# 同日跨城衔接：超过该时长视为时间紧张，超过 24 小时视为当日无法完成。
_SAME_DAY_TIGHT_MINUTES = 8 * 60
_SAME_DAY_IMPOSSIBLE_MINUTES = 24 * 60
# 相邻日跨城衔接：不超过该时长即认为一天足够，不再告警。
_ADJACENT_DAY_MIN_TRANSIT_MINUTES = 8 * 60


@dataclass(frozen=True, slots=True)
class TravelOrderFact:
    """保存冲突检测所需的已有行程字段，使规则本身可脱离数据库单测。"""

    order_id: str
    departure_city: str | None
    destination: str | None
    departure_date: date
    return_date: date
    status: str


def evaluate_travel_conflicts(
    existing: Sequence[TravelOrderFact],
    *,
    departure_city: str | None,
    destination: str | None,
    departure_date: date,
    return_date: date,
    transit_minutes: Callable[[str | None, str | None], int],
) -> list[dict[str, Any]]:
    """按参考项目口径逐条判定冲突事实，返回按严重等级降序排列的结果。

    冲突规则与优先级：①已有行程在候选出发当天结束（同日交接）→ ②候选在已有行程
    出发当天结束（反向同日交接）→ ③日期区间重叠 → ④已有行程结束次日候选出发
    → ⑤候选结束次日已有行程出发。每条已有行程最多命中一个分支，避免同一段关系
    被重复报告成多条冲突。
    """
    conflicts: list[dict[str, Any]] = []
    for item in existing:
        _evaluate_single_conflict(
            item,
            departure_city=departure_city,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            transit_minutes=transit_minutes,
            out=conflicts,
        )
    conflicts.sort(
        key=lambda entry: (
            -_CONFLICT_SEVERITY_RANK.get(str(entry["severity"]), 0),
            str(entry.get("order_id") or ""),
        )
    )
    return conflicts


def _evaluate_single_conflict(
    item: TravelOrderFact,
    *,
    departure_city: str | None,
    destination: str | None,
    departure_date: date,
    return_date: date,
    transit_minutes: Callable[[str | None, str | None], int],
    out: list[dict[str, Any]],
) -> None:
    """判定单条已有行程与候选行程的冲突，命中一个分支后立即返回。"""
    existing_departure = item.departure_date
    existing_return = item.return_date
    # 双重等式同时成立说明两张都是同一天的单日行程，交给重叠分支判定。
    if existing_return == departure_date and existing_departure != return_date:
        _append_same_day_transit(item, item.destination, departure_city, transit_minutes, out)
        return
    if existing_departure == return_date and existing_return != departure_date:
        _append_same_day_transit(item, destination, item.departure_city, transit_minutes, out)
        return
    if existing_return >= departure_date and existing_departure <= return_date:
        _append_overlap_conflict(item, departure_city, destination, out)
        return
    if existing_return + timedelta(days=1) == departure_date:
        _append_adjacent_day_conflict(
            item,
            end_city=item.destination,
            start_city=departure_city,
            end_date=existing_return,
            start_date=departure_date,
            side="departure",
            transit_minutes=transit_minutes,
            out=out,
        )
        return
    if existing_departure - timedelta(days=1) == return_date:
        _append_adjacent_day_conflict(
            item,
            end_city=destination,
            start_city=item.departure_city,
            end_date=return_date,
            start_date=existing_departure,
            side="return",
            transit_minutes=transit_minutes,
            out=out,
        )


def _append_overlap_conflict(
    item: TravelOrderFact,
    departure_city: str | None,
    destination: str | None,
    out: list[dict[str, Any]],
) -> None:
    """日期区间重叠：出发地与目的地都相同视为重复提交，否则物理上无法同时完成。"""
    summary = _order_summary(item)
    if _same_city_value(item.departure_city, departure_city) and _same_city_value(
        item.destination, destination
    ):
        out.append(
            {
                "severity": "LOW",
                "type": "same_city_overlap",
                "description": (
                    f"已有行程（{summary}）与本次时间重叠且出发地、目的地相同，"
                    "属于同城市重复提交。"
                ),
                "suggestion": "可继续提交，但建议先取消或调整其中一张差旅单，避免重复审批。",
                "order_id": item.order_id,
                "order_summary": summary,
            }
        )
        return
    description = (
        f"已有行程（{summary}）与本次时间重叠，但目的地不同，物理上不可能同时身处两地。"
    )
    if _same_city_value(item.departure_city, destination) and _same_city_value(
        item.destination, departure_city
    ):
        description += (
            "注意：两张差旅单方向恰好相反，若属同一趟出行的往返拆单，请确认是否重复提交。"
        )
    out.append(
        {
            "severity": "HIGH",
            "type": "cross_city_overlap",
            "description": description,
            "suggestion": (
                "请调整本次差旅的出发日期或返回日期，避开已有行程；"
                "如确有需要请先取消或修改已有差旅单。"
            ),
            "order_id": item.order_id,
            "order_summary": summary,
        }
    )


def _append_same_day_transit(
    item: TravelOrderFact,
    end_city: str | None,
    start_city: str | None,
    transit_minutes: Callable[[str | None, str | None], int],
    out: list[dict[str, Any]],
) -> None:
    """同一天先结束一段行程再出发：按跨城衔接时长决定是否告警以及等级。"""
    if _same_city_value(end_city, start_city):
        return
    minutes = transit_minutes(end_city, start_city)
    if minutes <= 0:
        return
    hours = minutes // 60
    summary = _order_summary(item)
    if minutes > _SAME_DAY_IMPOSSIBLE_MINUTES:
        severity = "HIGH"
        description = (
            f"同日需要从 {end_city} 前往 {start_city}，最短衔接约 {hours} 小时，"
            "超出当日可行范围，物理上无法完成。"
        )
        suggestion = "请将出发日期至少延后 1 天，或调整目的地。"
    elif minutes > _SAME_DAY_TIGHT_MINUTES:
        severity = "MEDIUM"
        description = (
            f"同日需要从 {end_city} 前往 {start_city}，估算最短衔接约 {hours} 小时，"
            "时间非常紧张。"
        )
        suggestion = "建议改为次日出发，或选择更早的航班/高铁以预留缓冲时间。"
    else:
        severity = "MEDIUM"
        description = (
            f"同日需要从 {end_city} 前往 {start_city}，估算衔接约 {hours} 小时。"
        )
        suggestion = "衔接可行但偏紧，建议选择早班交通并预留 1~2 小时缓冲。"
    out.append(
        {
            "severity": severity,
            "type": "same_day_transit_insufficient",
            "description": f"已有行程（{summary}）与本次首尾相接：{description}",
            "suggestion": suggestion,
            "order_id": item.order_id,
            "order_summary": summary,
        }
    )


def _append_adjacent_day_conflict(
    item: TravelOrderFact,
    *,
    end_city: str | None,
    start_city: str | None,
    end_date: date,
    start_date: date,
    side: str,
    transit_minutes: Callable[[str | None, str | None], int],
    out: list[dict[str, Any]],
) -> None:
    """相邻日衔接：一天内能完成跨城交通则不告警，否则提示路径断裂风险。"""
    if _same_city_value(end_city, start_city):
        return
    minutes = transit_minutes(end_city, start_city)
    if minutes <= _ADJACENT_DAY_MIN_TRANSIT_MINUTES:
        return
    summary = _order_summary(item)
    if side == "departure":
        relation = (
            f"已有行程（{summary}）在 {end_date} 结束于 {end_city}，"
            f"本次行程在次日（{start_date}）从 {start_city} 出发"
        )
    else:
        relation = (
            f"本次行程在 {end_date} 结束于 {end_city}，"
            f"已有行程（{summary}）在次日（{start_date}）从 {start_city} 出发"
        )
    out.append(
        {
            "severity": "MEDIUM",
            "type": "next_day_route_break",
            "description": (
                f"{relation}，但跨城交通至少需要 {minutes // 60} 小时，仅 1 天衔接偏紧，"
                "存在误机或赶不上高铁的风险。"
            ),
            "suggestion": "建议在两段行程之间留出 1~2 天缓冲，或将其中一段改为同城行程。",
            "order_id": item.order_id,
            "order_summary": summary,
        }
    )


def _order_summary(item: TravelOrderFact) -> str:
    """生成可直接向用户解释的已有行程摘要。"""
    return (
        f"{item.departure_city or '未填'} → {item.destination or '未填'}，"
        f"{item.departure_date.isoformat()} ~ {item.return_date.isoformat()}（{item.status}）"
    )


def _same_city_value(first: str | None, second: str | None) -> bool:
    """比较城市名时忽略空白与“市”后缀，供同城判定复用。"""
    if not first or not second:
        return False
    return _normalize_city_name(first) == _normalize_city_name(second)


def _normalize_city_name(value: str) -> str:
    """去除城市名两侧空白与常见“市”后缀，便于跨数据源比较。"""
    name = value.strip()
    while name.endswith("市") and len(name) > 1:
        name = name[:-1]
    return name.casefold()


class TravelOrderStatus(StrEnum):
    """差旅申请单生命周期状态。"""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ApprovalRecordStatus(StrEnum):
    """差旅审批单生命周期状态。"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


_TRAVEL_TRANSITIONS = {
    TravelOrderStatus.DRAFT: {TravelOrderStatus.SUBMITTED, TravelOrderStatus.CANCELLED},
    TravelOrderStatus.SUBMITTED: {
        TravelOrderStatus.APPROVED,
        TravelOrderStatus.REJECTED,
        TravelOrderStatus.CANCELLED,
    },
    TravelOrderStatus.APPROVED: {TravelOrderStatus.COMPLETED, TravelOrderStatus.CANCELLED},
    TravelOrderStatus.REJECTED: set(),
    TravelOrderStatus.COMPLETED: set(),
    TravelOrderStatus.CANCELLED: set(),
}


def transition_travel_order(
    current: TravelOrderStatus, target: TravelOrderStatus
) -> TravelOrderStatus:
    """校验差旅申请单状态转移，不允许恢复终态或跳过审批。"""
    if target not in _TRAVEL_TRANSITIONS[current]:
        raise ValueError(f"travel_order_transition_not_allowed:{current}->{target}")
    return target


def should_auto_complete(status: TravelOrderStatus, return_date: date | None, today: date) -> bool:
    """判断已通过差旅单是否应在返程日次日自动完成。"""
    return status is TravelOrderStatus.APPROVED and return_date is not None and return_date < today


class TravelOrderPersistenceService:
    """通过 SQLModel 事务读写差旅单、审批单和预订记录，保证双向关联与幂等。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        city_tier_config: CityTierConfig | None = None,
    ) -> None:
        """保存请求范围外可复用的异步 Session 工厂。"""
        self.session_factory = session_factory
        self.city_tier_config = city_tier_config or load_city_tier_config()

    async def submit(
        self, *, user_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> TravelOrder:
        """原子创建差旅单和审批单，并以 order_id 作为幂等结果索引。"""
        order_id = idempotency_key
        async with self.session_factory() as session:
            existing = await session.get(TravelOrder, order_id)
            if existing is not None:
                if existing.user_id != user_id:
                    raise PermissionError("travel_order_forbidden")
                return existing
            now = datetime.now(UTC)
            approval_id = f"approval_{uuid4().hex}"
            order = TravelOrder(
                order_id=order_id,
                user_id=user_id,
                destination=payload.get("destination"),
                departure_city=payload.get("departure_city"),
                departure_date=payload.get("departure_date"),
                return_date=payload.get("return_date"),
                purpose=payload.get("purpose"),
                status=TravelOrderStatus.SUBMITTED,
                approval_id=approval_id,
                created_at=now,
                updated_at=now,
            )
            approval = ApprovalRecord(
                process_instance_id=approval_id,
                user_id=user_id,
                title=payload.get("purpose") or "差旅申请",
                status=ApprovalRecordStatus.PENDING,
                approval_form={
                    key: value.isoformat() if isinstance(value, (date, datetime)) else value
                    for key, value in payload.items()
                },
                submit_time=now,
                update_time=now,
                order_id=order_id,
            )
            session.add_all([order, approval])
            await session.commit()
            await session.refresh(order)
            return order

    async def create_and_submit(
        self, *, user_id: str, payload: dict[str, Any], order_id: str
    ) -> TravelOrder:
        """以领域服务命名封装差旅单与审批单三步事务写入。"""
        return await self.submit(user_id=user_id, payload=payload, idempotency_key=order_id)

    async def cancel_with_approval(self, user_id: str, order_id: str) -> TravelOrder | None:
        """以领域服务命名封装差旅单取消和审批同步。"""
        return await self.cancel(user_id, order_id)

    async def modify_and_resubmit(
        self, *, user_id: str, order_id: str, payload: dict[str, Any]
    ) -> TravelOrder | None:
        """以领域服务命名封装修改、旧审批撤销和新审批提交。"""
        return await self.modify(user_id=user_id, order_id=order_id, payload=payload)

    async def find_by_order_id(self, user_id: str, order_id: str) -> TravelOrder | None:
        """在事务外按用户和订单号执行幂等查询。"""
        return await self.get_order(user_id, order_id)

    async def verify_order_status(
        self, user_id: str, order_id: str, expected_status: TravelOrderStatus
    ) -> bool:
        """写入后回查订单状态，确认数据库已落库。"""
        order = await self.get_order(user_id, order_id)
        return order is not None and order.status == expected_status

    async def find_latest_approval(self, user_id: str) -> ApprovalRecord | None:
        """读取当前用户最近一条审批实例。"""
        async with self.session_factory() as session:
            statement = (
                select(ApprovalRecord)
                .where(_APPROVAL_COLUMNS.user_id == user_id)
                .order_by(_APPROVAL_COLUMNS.submit_time.desc())
                .limit(1)
            )
            row = await session.scalar(statement)
        return row if isinstance(row, ApprovalRecord) else None

    async def query_associated_bookings(
        self, user_id: str, order_id: str
    ) -> list[BookingRecord]:
        """读取差旅单关联的预订记录。"""
        return await self.list_bookings(user_id, travel_order_id=order_id)

    async def get_order(self, user_id: str, order_id: str) -> TravelOrder | None:
        """按用户归属精确读取差旅单。"""
        async with self.session_factory() as session:
            statement = select(TravelOrder).where(
                _ORDER_COLUMNS.order_id == order_id, _ORDER_COLUMNS.user_id == user_id
            )
            order = await session.scalar(statement)
            if order is not None and should_auto_complete(
                TravelOrderStatus(order.status),
                order.return_date,
                datetime.now(ZoneInfo("Asia/Shanghai")).date(),
            ):
                order.status = TravelOrderStatus.COMPLETED
                await session.commit()
            return order

    async def list_orders(self, user_id: str, status: str | None = None) -> list[TravelOrder]:
        """按用户和可选状态读取差旅单列表。"""
        async with self.session_factory() as session:
            statement = select(TravelOrder).where(_ORDER_COLUMNS.user_id == user_id)
            if status:
                statement = statement.where(_ORDER_COLUMNS.status == status)
            statement = statement.order_by(_ORDER_COLUMNS.created_at.desc()).limit(100)
            return list((await session.scalars(statement)).all())

    async def get_approval(self, user_id: str, process_instance_id: str) -> ApprovalRecord | None:
        """按用户归属读取审批实例。"""
        async with self.session_factory() as session:
            statement = select(ApprovalRecord).where(
                _APPROVAL_COLUMNS.process_instance_id == process_instance_id,
                _APPROVAL_COLUMNS.user_id == user_id,
            )
            row = await session.scalar(statement)
        return row if isinstance(row, ApprovalRecord) else None

    async def list_approvals_by_process_instance_ids(
        self, user_id: str, process_instance_ids: Sequence[str | None]
    ) -> dict[str, ApprovalRecord]:
        """按用户归属批量读取审批实例，供只读列表展示审批状态。

        以差旅单上的 approval_id 作为查询键（对应 approval_record.process_instance_id），
        一次性查询避免逐单查询形成 N+1；空集合直接返回空字典，调用方无需分支。
        """
        unique_ids = sorted({item for item in process_instance_ids if item})
        if not unique_ids:
            return {}
        async with self.session_factory() as session:
            statement = select(ApprovalRecord).where(
                _APPROVAL_COLUMNS.user_id == user_id,
                _APPROVAL_COLUMNS.process_instance_id.in_(unique_ids),
            )
            rows = (await session.scalars(statement)).all()
        return {
            row.process_instance_id: row
            for row in rows
            if isinstance(row, ApprovalRecord)
        }

    async def list_approvals_for_admin(
        self, status: str | None = None
    ) -> list[ApprovalRecord]:
        """管理员按状态查看审批实例，仅返回审批元数据不含差旅正文。"""
        async with self.session_factory() as session:
            statement = select(ApprovalRecord)
            if status:
                statement = statement.where(_APPROVAL_COLUMNS.status == status)
            statement = statement.order_by(_APPROVAL_COLUMNS.submit_time.desc()).limit(100)
            return list((await session.scalars(statement)).all())

    async def decide_approval(
        self, process_instance_id: str, decision: str, remark: str | None = None
    ) -> ApprovalRecord | None:
        """按管理员决定流转审批状态；已处于终态时返回 None 以避免重复决策。"""
        if decision not in {"APPROVED", "REJECTED"}:
            raise ValueError("approval_decision_invalid")
        async with self.session_factory() as session:
            statement = select(ApprovalRecord).where(
                _APPROVAL_COLUMNS.process_instance_id == process_instance_id
            )
            row = await session.scalar(statement)
            if not isinstance(row, ApprovalRecord):
                return None
            if row.status in {"APPROVED", "REJECTED", "CANCELLED"}:
                return None
            row.status = decision
            row.remark = (remark or "").strip()[:512] or row.remark
            row.update_time = datetime.now(UTC)
            await session.commit()
        return row

    async def list_bookings(
        self,
        user_id: str,
        *,
        travel_order_id: str | None = None,
        booking_id: str | None = None,
        biz_type: str | None = None,
    ) -> list[BookingRecord]:
        """按用户归属、差旅单、业务类型和预订号读取内部记录。"""
        async with self.session_factory() as session:
            statement = select(BookingRecord).where(
                _BOOKING_COLUMNS.user_id == user_id, _BOOKING_COLUMNS.deleted.is_(False)
            )
            if travel_order_id:
                statement = statement.where(
                    _BOOKING_COLUMNS.travel_order_id == travel_order_id
                )
            if booking_id:
                statement = statement.where(_BOOKING_COLUMNS.booking_id == booking_id)
            if biz_type and biz_type.strip():
                statement = statement.where(
                    _BOOKING_COLUMNS.biz_type == biz_type.strip().upper()
                )
            # 预订记录不参与差旅单的自动完成判定，这里只返回已授权范围内的记录。
            return list((await session.scalars(statement.limit(100))).all())

    async def cancel_booking(self, user_id: str, booking_id: str) -> BookingRecord | None:
        """酒店等内部预订执行幂等取消，外部平台由上层明确禁用。"""
        async with self.session_factory() as session:
            booking = await session.scalar(
                select(BookingRecord).where(
                    _BOOKING_COLUMNS.booking_id == booking_id,
                    _BOOKING_COLUMNS.user_id == user_id,
                    _BOOKING_COLUMNS.deleted.is_(False),
                )
            )
            if booking is None:
                return None
            if booking.status != "CANCELLED":
                booking.status = "CANCELLED"
                await session.commit()
                await session.refresh(booking)
            return booking

    async def upsert_booking(
        self, *, user_id: str, payload: dict[str, Any], idempotency_key: str
    ) -> BookingRecord:
        """按幂等键保存途牛订单结果；Provider 失败时不会创建内部订单。"""
        async with self.session_factory() as session:
            existing = await session.scalar(
                select(BookingRecord).where(
                    _BOOKING_COLUMNS.booking_id == idempotency_key,
                    _BOOKING_COLUMNS.user_id == user_id,
                    _BOOKING_COLUMNS.deleted.is_(False),
                )
            )
            if existing is not None:
                return existing
            booking = BookingRecord(
                booking_id=idempotency_key,
                user_id=user_id,
                conversation_id=payload.get("conversation_id"),
                travel_order_id=payload.get("travel_order_id"),
                biz_type=str(payload.get("biz_type") or "UNKNOWN").upper(),
                platform=str(payload.get("platform") or "TUNIU").upper(),
                external_order_no=payload.get("external_order_no"),
                status=str(payload.get("status") or "PAYMENT_PENDING").upper(),
                external_status=payload.get("external_status"),
                payment_status=payload.get("payment_status"),
                title=payload.get("title"),
                total_amount=payload.get("total_amount"),
                currency=str(payload.get("currency") or "CNY"),
                contact_name=payload.get("contact_name"),
                contact_phone=payload.get("contact_phone"),
                detail=payload.get("detail") if isinstance(payload.get("detail"), dict) else {},
                remark=payload.get("remark"),
                booked_at=datetime.now(UTC),
            )
            session.add(booking)
            await session.commit()
            await session.refresh(booking)
            return booking

    async def cancel(self, user_id: str, order_id: str) -> TravelOrder | None:
        """取消草稿或审批中的差旅单，并同步撤销当前审批。"""
        async with self.session_factory() as session:
            order = await session.scalar(
                select(TravelOrder).where(
                    _ORDER_COLUMNS.order_id == order_id, _ORDER_COLUMNS.user_id == user_id
                )
            )
            if order is None:
                return None
            if order.status not in {
                TravelOrderStatus.DRAFT,
                TravelOrderStatus.SUBMITTED,
                TravelOrderStatus.APPROVED,
            }:
                raise ValueError("travel_order_cancel_not_allowed")
            order.status = TravelOrderStatus.CANCELLED
            if order.approval_id:
                approval = await session.get(ApprovalRecord, order.approval_id)
                if approval is not None and approval.status == ApprovalRecordStatus.PENDING:
                    approval.status = ApprovalRecordStatus.CANCELLED
                    approval.update_time = datetime.now(UTC)
            await session.commit()
            await session.refresh(order)
            return order

    async def modify(
        self, *, user_id: str, order_id: str, payload: dict[str, Any]
    ) -> TravelOrder | None:
        """更新可修改差旅单、撤销旧待审批实例并生成新的审批快照。"""
        async with self.session_factory() as session:
            order = await session.scalar(
                select(TravelOrder).where(
                    _ORDER_COLUMNS.order_id == order_id, _ORDER_COLUMNS.user_id == user_id
                )
            )
            if order is None:
                return None
            if order.status not in {
                TravelOrderStatus.DRAFT,
                TravelOrderStatus.SUBMITTED,
                TravelOrderStatus.APPROVED,
            }:
                raise ValueError("travel_order_modify_not_allowed")
            previous_approval_id = order.approval_id
            for field in (
                "destination",
                "departure_city",
                "departure_date",
                "return_date",
                "purpose",
                "plan_html_url",
            ):
                if field in payload:
                    setattr(order, field, payload[field])
            if (
                order.departure_date
                and order.return_date
                and order.return_date < order.departure_date
            ):
                raise ValueError("invalid_date_range")
            now = datetime.now(UTC)
            if previous_approval_id:
                previous = await session.get(ApprovalRecord, previous_approval_id)
                if previous is not None and previous.status == ApprovalRecordStatus.PENDING:
                    previous.status = ApprovalRecordStatus.CANCELLED
                    previous.update_time = now
            approval_id = f"approval_{uuid4().hex}"
            approval_form = {
                "order_id": order_id,
                **{
                    key: value.isoformat() if isinstance(value, (date, datetime)) else value
                    for key, value in payload.items()
                },
            }
            approval = ApprovalRecord(
                process_instance_id=approval_id,
                user_id=user_id,
                title=order.purpose or "差旅申请修改",
                status=ApprovalRecordStatus.PENDING,
                approval_form=approval_form,
                submit_time=now,
                update_time=now,
                order_id=order_id,
            )
            order.approval_id = approval_id
            order.status = TravelOrderStatus.SUBMITTED
            order.updated_at = now
            session.add(approval)
            await session.commit()
            await session.refresh(order)
            return order

    async def detect_conflicts(
        self,
        *,
        user_id: str,
        departure_city: str | None,
        destination: str | None,
        departure_date: date | None,
        return_date: date | None,
        excluding_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按已持久化行程执行同城、跨城、接驳和路线断裂的服务端初判。

        数据库查询只负责取出行程事实，具体规则交给 ``evaluate_travel_conflicts``，
        便于对规则本身做纯函数级回归测试。
        """
        if departure_date is None or return_date is None:
            return []
        async with self.session_factory() as session:
            statement = select(TravelOrder).where(
                _ORDER_COLUMNS.user_id == user_id,
                _ORDER_COLUMNS.status.notin_(
                    [TravelOrderStatus.CANCELLED, TravelOrderStatus.REJECTED]
                ),
            )
            if excluding_order_id:
                statement = statement.where(_ORDER_COLUMNS.order_id != excluding_order_id)
            rows = list((await session.scalars(statement)).all())
        facts = [
            TravelOrderFact(
                order_id=row.order_id,
                departure_city=row.departure_city,
                destination=row.destination,
                departure_date=row.departure_date,
                return_date=row.return_date,
                status=row.status,
            )
            for row in rows
            if isinstance(row, TravelOrder)
            and row.departure_date is not None
            and row.return_date is not None
        ]
        return evaluate_travel_conflicts(
            facts,
            departure_city=departure_city,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            transit_minutes=self.city_tier_config.transit_minutes,
        )

    async def check_conflicts(
        self,
        *,
        user_id: str,
        departure_city: str | None,
        destination: str | None,
        departure_date: date | None,
        return_date: date | None,
        exclude_order_id: str | None = None,
    ) -> dict[str, Any]:
        """构建冲突工具的事实型结果，不对用户决策做代码层阻断。"""
        if (
            departure_date is not None
            and return_date is not None
            and departure_date > return_date
        ):
            return {
                "valid": False,
                "has_conflict": False,
                "severity": "NONE",
                "summary": (
                    f"出发日期（{departure_date.isoformat()}）晚于返回日期"
                    f"（{return_date.isoformat()}），请先修正日期。"
                ),
                "conflicts": [],
            }
        conflicts = await self.detect_conflicts(
            user_id=user_id,
            departure_city=departure_city,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            excluding_order_id=exclude_order_id,
        )
        severity = max(
            (str(item["severity"]) for item in conflicts),
            key=lambda value: _CONFLICT_SEVERITY_RANK.get(value, 0),
            default="NONE",
        )
        if not conflicts:
            summary = "未发现与已有差旅冲突"
        else:
            high = sum(1 for item in conflicts if item["severity"] == "HIGH")
            medium = sum(1 for item in conflicts if item["severity"] == "MEDIUM")
            low = sum(1 for item in conflicts if item["severity"] == "LOW")
            summary = (
                f"命中 {len(conflicts)} 条冲突（严重 {high} 条、中等 {medium} 条、"
                f"轻微 {low} 条）；请逐条向用户说明冲突类型、对方行程与建议，"
                "严重冲突需用户明确同意后才能提交。"
            )
        return {
            "valid": True,
            "has_conflict": bool(conflicts),
            "severity": severity,
            "summary": summary,
            "conflicts": conflicts,
        }
