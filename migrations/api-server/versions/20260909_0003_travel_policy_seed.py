# 文件职责：将 business_travel_policy.docx 提取的制度规则初始化到差旅政策表。
# 定义 upgrade 写入三档职级与三类城市政策，downgrade 删除本次种子规则。
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_0003"
down_revision = "20260904_0002"
branch_labels = None
depends_on = None


_RULES = (
    (1, 8, "一线", "ECONOMY", "SECOND", 500, 4, 200, 200, 8000, 3),
    (1, 8, "新一线", "ECONOMY", "SECOND", 400, 4, 200, 200, 8000, 3),
    (1, 8, "其他", "ECONOMY", "SECOND", 350, 4, 200, 200, 8000, 3),
    (9, 10, "一线", "BUSINESS", "FIRST", 600, 5, 300, 250, 10000, 3),
    (9, 10, "新一线", "BUSINESS", "FIRST", 500, 5, 300, 250, 10000, 3),
    (9, 10, "其他", "BUSINESS", "FIRST", 400, 5, 300, 250, 10000, 3),
    (11, 99, "一线", "BUSINESS", "FIRST", 700, 5, 500, 300, 15000, 3),
    (11, 99, "新一线", "BUSINESS", "FIRST", 550, 5, 500, 300, 15000, 3),
    (11, 99, "其他", "BUSINESS", "FIRST", 450, 5, 500, 300, 15000, 3),
)


def upgrade() -> None:
    """幂等写入制度中的三档职级和三类城市政策。"""
    connection = op.get_bind()
    for rule in _RULES:
        connection.execute(
            sa.text(
                """
                INSERT INTO travel_policy_rules
                    (level_min, level_max, city_tier, flight_class, train_seat_class,
                     hotel_limit, hotel_star_limit, daily_meal_limit,
                     daily_transport_limit, approval_threshold, advance_booking_days)
                VALUES (:level_min, :level_max, :city_tier, :flight_class, :train_seat_class,
                        :hotel_limit, :hotel_star_limit, :daily_meal_limit,
                        :daily_transport_limit, :approval_threshold, :advance_booking_days)
                ON CONFLICT (level_min, level_max, city_tier) DO UPDATE SET
                    flight_class = EXCLUDED.flight_class,
                    train_seat_class = EXCLUDED.train_seat_class,
                    hotel_limit = EXCLUDED.hotel_limit,
                    hotel_star_limit = EXCLUDED.hotel_star_limit,
                    daily_meal_limit = EXCLUDED.daily_meal_limit,
                    daily_transport_limit = EXCLUDED.daily_transport_limit,
                    approval_threshold = EXCLUDED.approval_threshold,
                    advance_booking_days = EXCLUDED.advance_booking_days,
                    updated_at = now()
                """
            ),
            dict(
                level_min=rule[0],
                level_max=rule[1],
                city_tier=rule[2],
                flight_class=rule[3],
                train_seat_class=rule[4],
                hotel_limit=rule[5],
                hotel_star_limit=rule[6],
                daily_meal_limit=rule[7],
                daily_transport_limit=rule[8],
                approval_threshold=rule[9],
                advance_booking_days=rule[10],
            ),
        )


def downgrade() -> None:
    """删除本迁移写入的九条制度规则。"""
    op.execute(
        sa.text(
            "DELETE FROM travel_policy_rules WHERE (level_min, level_max) IN "
            "((1, 8), (9, 10), (11, 99)) AND city_tier IN ('一线', '新一线', '其他')"
        )
    )
