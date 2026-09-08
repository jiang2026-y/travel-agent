# 本文件定义差旅申请单和审批单的状态机及自动完成规则。
# 定义 TravelOrderStatus、ApprovalRecordStatus、transition_travel_order 和 auto_complete_orders。
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent_api.persistence.models import ApprovalRecord, BookingRecord, TravelOrder


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

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """保存请求范围外可复用的异步 Session 工厂。"""
        self.session_factory = session_factory

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
                .where(ApprovalRecord.user_id == user_id)
                .order_by(ApprovalRecord.submit_time.desc())
                .limit(1)
            )
            return await session.scalar(statement)

    async def query_associated_bookings(
        self, user_id: str, order_id: str
    ) -> list[BookingRecord]:
        """读取差旅单关联的预订记录。"""
        return await self.list_bookings(user_id, travel_order_id=order_id)

    async def get_order(self, user_id: str, order_id: str) -> TravelOrder | None:
        """按用户归属精确读取差旅单。"""
        async with self.session_factory() as session:
            statement = select(TravelOrder).where(
                TravelOrder.order_id == order_id, TravelOrder.user_id == user_id
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
            statement = select(TravelOrder).where(TravelOrder.user_id == user_id)
            if status:
                statement = statement.where(TravelOrder.status == status)
            statement = statement.order_by(TravelOrder.created_at.desc()).limit(100)
            return list((await session.scalars(statement)).all())

    async def get_approval(self, user_id: str, process_instance_id: str) -> ApprovalRecord | None:
        """按用户归属读取审批实例。"""
        async with self.session_factory() as session:
            statement = select(ApprovalRecord).where(
                ApprovalRecord.process_instance_id == process_instance_id,
                ApprovalRecord.user_id == user_id,
            )
            return await session.scalar(statement)

    async def list_bookings(
        self, user_id: str, *, travel_order_id: str | None = None, booking_id: str | None = None
    ) -> list[BookingRecord]:
        """按用户归属和预订条件读取内部记录。"""
        async with self.session_factory() as session:
            statement = select(BookingRecord).where(
                BookingRecord.user_id == user_id, BookingRecord.deleted.is_(False)
            )
            if travel_order_id:
                statement = statement.where(BookingRecord.travel_order_id == travel_order_id)
            if booking_id:
                statement = statement.where(BookingRecord.booking_id == booking_id)
            rows = list((await session.scalars(statement.limit(100))).all())
            today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
            changed = False
            for order in rows:
                if should_auto_complete(TravelOrderStatus(order.status), order.return_date, today):
                    order.status = TravelOrderStatus.COMPLETED
                    changed = True
            if changed:
                await session.commit()
            return rows

    async def cancel_booking(self, user_id: str, booking_id: str) -> BookingRecord | None:
        """酒店等内部预订执行幂等取消，外部平台由上层明确禁用。"""
        async with self.session_factory() as session:
            booking = await session.scalar(
                select(BookingRecord).where(
                    BookingRecord.booking_id == booking_id,
                    BookingRecord.user_id == user_id,
                    BookingRecord.deleted.is_(False),
                )
            )
            if booking is None:
                return None
            if booking.status != "CANCELLED":
                booking.status = "CANCELLED"
                await session.commit()
                await session.refresh(booking)
            return booking

    async def cancel(self, user_id: str, order_id: str) -> TravelOrder | None:
        """取消草稿或审批中的差旅单，并同步撤销当前审批。"""
        async with self.session_factory() as session:
            order = await session.scalar(
                select(TravelOrder).where(
                    TravelOrder.order_id == order_id, TravelOrder.user_id == user_id
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
                    TravelOrder.order_id == order_id, TravelOrder.user_id == user_id
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
        """按已持久化行程执行同城、跨城、接驳和路线断裂的服务端初判。"""
        if departure_date is None or return_date is None:
            return []
        async with self.session_factory() as session:
            statement = select(TravelOrder).where(
                TravelOrder.user_id == user_id,
                TravelOrder.status.notin_(
                    [TravelOrderStatus.CANCELLED, TravelOrderStatus.REJECTED]
                ),
            )
            if excluding_order_id:
                statement = statement.where(TravelOrder.order_id != excluding_order_id)
            rows = list((await session.scalars(statement)).all())
        conflicts: list[dict[str, Any]] = []
        for item in rows:
            if item.departure_date is None or item.return_date is None:
                continue
            overlap = departure_date <= item.return_date and return_date >= item.departure_date
            if overlap:
                new_cities = {value for value in (departure_city, destination) if value}
                existing_cities = {
                    value for value in (item.departure_city, item.destination) if value
                }
                shared_city = new_cities & existing_cities
                if shared_city:
                    conflicts.append(
                        {
                            "severity": "LOW",
                            "type": "same_city_overlap",
                            "description": (
                                f"新行程与已有差旅在{next(iter(shared_city))}存在时间重叠。"
                            ),
                            "suggestion": (
                                "请确认是否确实需要重复提交该城市的差旅。"
                            ),
                        }
                    )
                else:
                    conflicts.append(
                        {
                            "severity": "HIGH",
                            "type": "cross_city_overlap",
                            "description": "新行程与已有差旅存在跨城时间重叠，物理上无法同时完成。",
                            "suggestion": (
                                "建议调整出发或返程日期；如确需继续，必须明确确认忽略冲突。"
                            ),
                        }
                    )
            if item.return_date == departure_date and item.destination != departure_city:
                conflicts.append(
                    {
                        "severity": "MEDIUM",
                        "type": "same_day_transit_insufficient",
                        "description": "已有差旅返程日与新行程出发日相同，跨城衔接时间较为紧张。",
                        "suggestion": (
                            "建议调整日期或选择更早的交通方式，也可以选择忽略冲突继续提交。"
                        ),
                    }
                )
            if item.return_date + timedelta(days=1) == departure_date:
                if item.destination and departure_city and item.destination != departure_city:
                    conflicts.append(
                        {
                            "severity": "MEDIUM",
                            "type": "next_day_route_break",
                            "description": (
                                "已有差旅目的地与新行程出发城市不同，次日路线衔接不完整。"
                            ),
                            "suggestion": "建议补充交通衔接或调整日期后再提交。",
                        }
                    )
        return conflicts

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
        conflicts = await self.detect_conflicts(
            user_id=user_id,
            departure_city=departure_city,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            excluding_order_id=exclude_order_id,
        )
        rank = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        severity = max(
            (str(item["severity"]) for item in conflicts),
            key=rank.get,
            default="NONE",
        )
        return {
            "valid": True,
            "has_conflict": bool(conflicts),
            "severity": severity,
            "summary": "检测到与已有差旅存在潜在冲突" if conflicts else "未发现与已有差旅冲突",
            "conflicts": conflicts,
        }
