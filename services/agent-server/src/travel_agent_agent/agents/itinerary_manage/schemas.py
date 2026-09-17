# 文件职责：定义行程管理工具的结构化请求与响应 Record。
# 定义差旅单、提交、审批、取消和修改结果 Record。
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TravelOrderRecord(BaseModel):
    """表示一条差旅申请单的公开只读信息。"""

    model_config = ConfigDict(extra="forbid")

    order_id: str
    user_id: str
    destination: str | None = None
    departure_city: str | None = None
    departure_date: date | None = None
    return_date: date | None = None
    purpose: str | None = None
    status: str
    approval_id: str | None = None


class SubmitTravelApprovalRecord(BaseModel):
    """表示差旅单和审批单提交结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    order_id: str
    approval_id: str | None = None
    order_status: str
    approval_status: str | None = None
    idempotent: bool = False
    verified: bool = False
    message: str = ""


class ApprovalStatusRecord(BaseModel):
    """表示审批实例查询结果。"""

    model_config = ConfigDict(extra="forbid")

    found: bool
    process_instance_id: str | None = None
    status: str | None = None
    submit_time: datetime | None = None
    latest: bool = False
    message: str = ""


class CancelTravelOrderRecord(BaseModel):
    """表示差旅单取消结果及关联预订摘要。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    order_id: str
    order_status: str
    approval_id: str | None = None
    approval_status: str | None = None
    associated_bookings: list[dict[str, Any]] = Field(default_factory=list)
    requires_confirmation: bool = False
    message: str = ""


class ModifyTravelOrderRecord(BaseModel):
    """表示差旅单修改和重新提交审批结果。"""

    model_config = ConfigDict(extra="forbid")

    success: bool
    order_id: str
    old_approval_id: str | None = None
    new_approval_id: str | None = None
    old_approval_cancelled: bool = False
    updated_fields: list[str] = Field(default_factory=list)
    associated_bookings: list[dict[str, Any]] = Field(default_factory=list)
    no_op: bool = False
    message: str = ""


class TravelOrderInput(BaseModel):
    """校验提交和修改共用的差旅字段。"""

    model_config = ConfigDict(extra="forbid")

    # 提交差旅申请时可由服务端生成单号，因此允许省略；修改走独立工具参数。
    order_id: str | None = Field(default=None, max_length=64)
    destination: str | None = Field(default=None, max_length=256)
    departure_city: str | None = Field(default=None, max_length=128)
    departure_date: date | str | None = None
    return_date: date | str | None = None
    purpose: str | None = Field(default=None, max_length=512)


class TravelOrderConflictRequest(BaseModel):
    """定义冲突检测工具可见的行程输入，不包含用户身份。"""

    model_config = ConfigDict(extra="forbid")

    departure_city: str | None = Field(default=None, max_length=128)
    destination: str | None = Field(default=None, max_length=256)
    departure_date: date | str | None = None
    return_date: date | str | None = None
    exclude_order_id: str | None = Field(default=None, max_length=64)


class ConflictDetailRecord(BaseModel):
    """表示一条可向用户解释的冲突事实和建议。"""

    model_config = ConfigDict(extra="forbid")

    type: str
    severity: str
    description: str
    suggestion: str
    order_id: str | None = None
    order_summary: str | None = None


class TravelOrderConflictRecord(BaseModel):
    """表示冲突检测的事实结果，由 Agent 决定下一步动作。"""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    has_conflict: bool
    severity: str
    summary: str
    conflicts: list[ConflictDetailRecord] = Field(default_factory=list)


class UserContactInfoRecord(BaseModel):
    """表示预订资料完整度和缺失字段，不包含档案明文。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    flight_complete: bool = Field(alias="flightComplete")
    hotel_complete: bool = Field(alias="hotelComplete")
    train_complete: bool = Field(alias="trainComplete")
    missing_fields: dict[str, list[str]] = Field(alias="missingFields")
    message: str


class UserContactUpdateRequest(BaseModel):
    """定义用户主动提供的档案局部更新，不包含身份字段。"""

    model_config = ConfigDict(extra="forbid")

    chinese_name: str | None = Field(default=None, max_length=64)
    name_pinyin: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=128)
    phone: str | None = Field(default=None, max_length=32)
    id_type: str | None = Field(default=None, max_length=32)
    id_number: str | None = Field(default=None, max_length=32)
    gender: str | None = Field(default=None, max_length=4)


class UserContactUpdateRecord(UserContactInfoRecord):
    """表示档案更新成功后的字段名和新的预订资料完整度。"""

    success: bool
    updated_fields: list[str] = Field(alias="updatedFields")


class UserBaseLocationRecord(BaseModel):
    """表示常驻城市查询或更新结果。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    found: bool | None = None
    success: bool | None = None
    base_city: str | None = Field(default=None, alias="baseCity")
    updated_fields: list[str] = Field(default_factory=list, alias="updatedFields")
    message: str


class UserBaseLocationUpdateRequest(BaseModel):
    """定义用户主动提供的常驻城市更新。"""

    model_config = ConfigDict(extra="forbid")

    base_city: str = Field(min_length=1, max_length=128)
