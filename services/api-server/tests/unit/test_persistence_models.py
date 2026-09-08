# 本文件验证 PostgreSQL P0 持久化模型的表集合、敏感字段边界和关键外键。
# 定义 test_p0_table_registry 与 test_sensitive_fields_are_ciphertext_only。

from travel_agent_api.persistence.database import metadata, model_registry


def test_p0_table_registry() -> None:
    """确保 P0 只创建已确认的七类业务表。"""
    assert set(metadata.tables) == {
        "users",
        "conversations",
        "messages",
        "runs",
        "audit_events",
        "trips",
        "travel_policy_rules",
        "travel_order",
        "approval_record",
        "booking_record",
    }
    assert set(model_registry()) == {
        "User",
        "Conversation",
        "Message",
        "Run",
        "AuditEvent",
        "Trip",
        "TravelPolicyRule",
        "TravelOrder",
        "ApprovalRecord",
        "BookingRecord",
    }


def test_sensitive_fields_are_ciphertext_only() -> None:
    """确保证件号、手机号和姓名拼音不以明文字段进入 users。"""
    user_columns = set(metadata.tables["users"].columns.keys())
    assert {"id_number", "phone", "name_pinyin"}.isdisjoint(user_columns)
    assert {"sensitive_ciphertext", "sensitive_nonce", "sensitive_key_version"}.issubset(
        user_columns
    )
