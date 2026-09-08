# 本文件提供 PostgreSQL 企业账号的独立管理入口。
# 定义 create_user 命令，用于交互式创建 Argon2id 账号并可选写入 AES-GCM 加密档案。
# 定义 main，负责解析命令参数。
from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

service_src = Path(__file__).resolve().parents[1] / "services" / "api-server" / "src"
if str(service_src) not in sys.path:
    sys.path.insert(0, str(service_src))

from travel_agent_api.core.encryption import DataEncryptionService  # noqa: E402
from travel_agent_api.core.settings import Settings  # noqa: E402
from travel_agent_api.persistence.database import (  # noqa: E402
    create_async_engine_from_settings,
    session_scope,
)
from travel_agent_api.persistence.models import User  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    """构建独立账号管理命令，不接受明文密码命令行参数。"""
    parser = argparse.ArgumentParser(description="企业级智能旅行助手账号管理脚本")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create", help="创建企业预置账号")
    create.add_argument("--username", required=True, help="登录账号")
    create.add_argument("--role", choices=("user", "admin"), default="user", help="账号角色")
    create.add_argument("--base-city", help="常驻城市")
    create.add_argument("--level", help="职级，例如 P7")
    create.add_argument("--gender", choices=("M", "F"), help="性别")
    create.add_argument("--name-pinyin", help="姓名拼音；将以 AES-GCM 加密保存")
    create.add_argument("--chinese-name", help="中文姓名；将以 AES-GCM 加密保存")
    create.add_argument("--email", help="邮箱；将以 AES-GCM 加密保存")
    create.add_argument("--phone", help="手机号；将以 AES-GCM 加密保存")
    create.add_argument("--id-type", help="证件类型；将以 AES-GCM 加密保存")
    create.add_argument("--id-number", help="证件号码；将以 AES-GCM 加密保存")
    return parser


async def create_user(arguments: argparse.Namespace) -> str:
    """交互式读取密码，创建唯一用户并返回非敏感 user_id。"""
    password = getpass.getpass("请输入初始密码：")
    password_confirmation = getpass.getpass("再次输入初始密码：")
    if not password or password != password_confirmation:
        raise ValueError("password_confirmation_failed")
    settings = Settings.from_environment()
    sensitive_data = _collect_sensitive_data(arguments)
    encrypted = (
        DataEncryptionService.from_secret_file(settings.data_encryption_key_file).encrypt_json(
            sensitive_data
        )
        if sensitive_data
        else None
    )
    user = User(
        user_id=f"usr_{uuid.uuid4().hex}",
        username=arguments.username,
        password_hash=PasswordHasher().hash(password),
        role=arguments.role,
        base_city=arguments.base_city,
        level=arguments.level,
        gender=arguments.gender,
        sensitive_ciphertext=encrypted.ciphertext if encrypted else None,
        sensitive_nonce=encrypted.nonce if encrypted else None,
        sensitive_key_version=encrypted.key_version if encrypted else None,
    )
    engine = create_async_engine_from_settings(settings)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_scope(factory) as session:
            await _ensure_username_available(session, arguments.username)
            session.add(user)
        return user.user_id
    finally:
        await engine.dispose()


async def _ensure_username_available(session: AsyncSession, username: str) -> None:
    """检查登录账号唯一性，避免数据库约束错误直接暴露给管理员。"""
    existing = await session.scalar(select(User.user_id).where(User.username == username))
    if existing is not None:
        raise ValueError("username_already_exists")


def _collect_sensitive_data(arguments: argparse.Namespace) -> dict[str, str]:
    """仅收集提供的高敏感字段，不把空字段写入密文 JSON。"""
    values = {
        "name_pinyin": arguments.name_pinyin,
        "chinese_name": arguments.chinese_name,
        "email": arguments.email,
        "phone": arguments.phone,
        "id_type": arguments.id_type,
        "id_number": arguments.id_number,
    }
    return {key: value for key, value in values.items() if value}


def main(argv: Sequence[str] | None = None) -> int:
    """解析管理命令；失败仅返回安全错误码，不输出密码或敏感字段。"""
    arguments = build_parser().parse_args(argv)
    if arguments.command != "create":
        raise ValueError("unsupported_command")
    try:
        user_id = asyncio.run(create_user(arguments))
    except ValueError as error:
        print(f"账号创建失败：{error}")
        return 1
    print(f"账号创建成功，user_id={user_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
