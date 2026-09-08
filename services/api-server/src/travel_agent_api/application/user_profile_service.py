# 文件职责：管理当前用户的加密联系档案和常驻城市，并计算预订资料完整度。
# 定义档案服务、完整度计算和性别规范化函数。
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from travel_agent_api.core.encryption import DataEncryptionService, EncryptedPayload
from travel_agent_api.persistence.models import User

_SENSITIVE_FIELDS = frozenset(
    {"chinese_name", "name_pinyin", "email", "phone", "id_type", "id_number"}
)
_FLIGHT_FIELDS = ("chinese_name", "id_type", "id_number", "phone", "gender")
_HOTEL_FIELDS = ("name_pinyin", "email")
_TRAIN_FIELDS = ("chinese_name", "id_number")


class UserProfileError(RuntimeError):
    """表示用户档案不存在、字段不合法或加密档案无法安全读取。"""


@dataclass(frozen=True, slots=True)
class UserProfileService:
    """通过当前用户身份安全读取和更新加密预订档案。"""

    session_factory: async_sessionmaker[AsyncSession]
    encryption: DataEncryptionService

    async def contact_info(self, user_id: str) -> dict[str, Any]:
        """返回完整度和缺失项，不回传任何敏感档案明文。"""
        async with self.session_factory() as session:
            user = await self._find_user(session, user_id)
            profile = self._decrypt_profile(user)
        return contact_readiness(profile, user.gender)

    async def update_contact(self, user_id: str, updates: dict[str, str]) -> dict[str, Any]:
        """合并局部档案更新并重新加密，返回更新字段和新的完整度。"""
        if not updates:
            raise UserProfileError("profile_update_empty")
        async with self.session_factory() as session:
            user = await self._find_user(session, user_id)
            profile = self._decrypt_profile(user)
            updated_fields: list[str] = []
            for name, value in updates.items():
                if name == "gender":
                    normalized_gender = normalize_gender(value)
                    if user.gender != normalized_gender:
                        user.gender = normalized_gender
                        updated_fields.append(name)
                    continue
                if name not in _SENSITIVE_FIELDS:
                    raise UserProfileError("profile_field_not_supported")
                normalized_value = _normalize_field(name, value)
                if profile.get(name) != normalized_value:
                    profile[name] = normalized_value
                    updated_fields.append(name)
            if updated_fields:
                encrypted = self.encryption.encrypt_json(profile)
                user.sensitive_ciphertext = encrypted.ciphertext
                user.sensitive_nonce = encrypted.nonce
                user.sensitive_key_version = encrypted.key_version
                await session.commit()
                await session.refresh(user)
            readiness = contact_readiness(profile, user.gender)
        return {"success": True, "updatedFields": updated_fields, **readiness}

    async def base_location(self, user_id: str) -> dict[str, Any]:
        """读取当前用户常驻城市，不存在时明确返回缺失状态。"""
        async with self.session_factory() as session:
            user = await self._find_user(session, user_id)
            city = user.base_city.strip() if user.base_city else None
        return {
            "found": bool(city),
            "baseCity": city,
            "message": "已找到用户常驻城市" if city else "未找到用户常驻城市",
        }

    async def update_base_location(self, user_id: str, base_city: str) -> dict[str, Any]:
        """更新当前用户常驻城市并返回非敏感结果。"""
        city = base_city.strip()
        if not city or len(city) > 128:
            raise UserProfileError("base_city_invalid")
        async with self.session_factory() as session:
            user = await self._find_user(session, user_id)
            changed = user.base_city != city
            if changed:
                user.base_city = city
                await session.commit()
        return {
            "success": True,
            "updatedFields": ["base_city"] if changed else [],
            "baseCity": city,
            "message": "常驻城市已更新" if changed else "常驻城市未发生变化",
        }

    async def _find_user(self, session: AsyncSession, user_id: str) -> User:
        """按当前认证用户身份读取未停用档案。"""
        user = await session.scalar(
            select(User).where(User.user_id == user_id, User.disabled_at.is_(None))
        )
        if user is None:
            raise UserProfileError("profile_not_found")
        return user

    def _decrypt_profile(self, user: User) -> dict[str, str]:
        """解密用户档案并拒绝密文元数据不完整的记录。"""
        if user.sensitive_ciphertext is None:
            return {}
        if user.sensitive_nonce is None or user.sensitive_key_version is None:
            raise UserProfileError("profile_encryption_metadata_invalid")
        try:
            payload = self.encryption.decrypt_json(
                EncryptedPayload(
                    user.sensitive_ciphertext,
                    user.sensitive_nonce,
                    user.sensitive_key_version,
                )
            )
        except Exception as error:
            raise UserProfileError("profile_decryption_failed") from error
        return {
            name: value
            for name, value in payload.items()
            if name in _SENSITIVE_FIELDS and isinstance(value, str) and value.strip()
        }


def contact_readiness(profile: dict[str, str], gender: str | None) -> dict[str, Any]:
    """按机票、酒店和火车票规则计算完整度与精确缺失字段。"""
    fields = {**profile, "gender": gender or ""}
    missing = {
        "flight": _missing_fields(fields, _FLIGHT_FIELDS),
        "hotel": _missing_fields(fields, _HOTEL_FIELDS),
        "train": _missing_fields(fields, _TRAIN_FIELDS),
    }
    return {
        "flightComplete": not missing["flight"],
        "hotelComplete": not missing["hotel"],
        "trainComplete": not missing["train"],
        "missingFields": missing,
        "message": _readiness_message(missing),
    }


def normalize_gender(value: str) -> str:
    """接受中文或 M/F 性别表达，并统一为数据库存储值。"""
    normalized = value.strip().upper()
    mapping = {"男": "M", "女": "F", "M": "M", "F": "F"}
    if normalized not in mapping:
        raise UserProfileError("gender_invalid")
    return mapping[normalized]


def _missing_fields(values: dict[str, str], required: tuple[str, ...]) -> list[str]:
    """以中文显示名返回当前预订类型真正缺少的字段。"""
    labels = {
        "chinese_name": "中文姓名",
        "name_pinyin": "姓名拼音",
        "email": "邮箱",
        "phone": "手机号",
        "id_type": "证件类型",
        "id_number": "证件号",
        "gender": "性别",
    }
    return [labels[name] for name in required if not values.get(name)]


def _readiness_message(missing: dict[str, list[str]]) -> str:
    """生成只包含缺口字段的面向用户提示，不回显已有敏感资料。"""
    groups = {
        "机票预订": missing["flight"],
        "酒店预订": missing["hotel"],
        "火车票预订": missing["train"],
    }
    incomplete = [f"{name}还缺：{'、'.join(fields)}" for name, fields in groups.items() if fields]
    return "；".join(incomplete) if incomplete else "预订所需档案信息完整"


def _normalize_field(name: str, value: str) -> str:
    """校验并规范化可写入加密档案的字段。"""
    normalized = value.strip()
    if not normalized:
        raise UserProfileError("profile_field_empty")
    if name == "phone" and not re.fullmatch(r"1[3-9]\d{9}", normalized):
        raise UserProfileError("phone_invalid")
    if name == "email" and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise UserProfileError("email_invalid")
    if name == "id_number" and not re.fullmatch(r"[A-Za-z0-9-]{6,32}", normalized):
        raise UserProfileError("id_number_invalid")
    if name == "id_type" and len(normalized) > 32:
        raise UserProfileError("id_type_invalid")
    if name == "name_pinyin":
        if not re.fullmatch(r"[A-Za-z .'-]{2,64}", normalized):
            raise UserProfileError("name_pinyin_invalid")
        return " ".join(normalized.upper().split())
    if name == "chinese_name" and len(normalized) > 64:
        raise UserProfileError("chinese_name_invalid")
    if len(normalized) > 128:
        raise UserProfileError("profile_field_too_long")
    return normalized
