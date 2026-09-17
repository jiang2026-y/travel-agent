# 文件职责：创建用户级第三方 API Key 密文表。
# 定义 upgrade 创建 user_api_key 表与唯一约束，downgrade 回滚该表。
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260916_0004"
down_revision = "20260909_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """创建按 (user_id, provider) 唯一的 API Key 密文表。"""
    op.create_table(
        "user_api_key",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.user_id"), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("api_key_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("api_key_nonce", sa.LargeBinary(), nullable=False),
        sa.Column("api_key_key_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_api_key_user_provider"),
    )
    op.create_index("ix_user_api_key_user_id", "user_api_key", ["user_id"])


def downgrade() -> None:
    """删除 API Key 密文表；降级会丢失已保存的用户密钥。"""
    op.drop_index("ix_user_api_key_user_id", table_name="user_api_key")
    op.drop_table("user_api_key")
