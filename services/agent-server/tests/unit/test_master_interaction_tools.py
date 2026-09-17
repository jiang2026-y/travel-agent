# 文件职责：验证 Master 主动提问工具的载荷结构与参数清洗。
# 定义 UserQuestion 载荷、选项清洗与表单字段清洗测试。
from __future__ import annotations

from travel_agent_agent.agents.master.tools import (
    UserQuestion,
    normalize_ui_fields,
    normalize_ui_options,
)


def test_user_question_payload_carries_full_java_contract() -> None:
    """主动提问载荷必须包含 ui_type、options、fields、默认值与允许自定义项。"""
    payload = UserQuestion(
        ui_type="select",
        question="请选择出发城市",
        options=("上海", "杭州"),
        fields=({"name": "city", "label": "城市"},),
        default_value="上海",
        allow_other=True,
    ).as_interrupt_payload()
    assert payload["kind"] == "clarification"
    assert payload["ui_type"] == "select"
    assert payload["options"] == ["上海", "杭州"]
    assert payload["fields"] == [{"name": "city", "label": "城市"}]
    assert payload["default_value"] == "上海"
    assert payload["allow_other"] is True


def test_option_and_field_normalization_drops_invalid_entries() -> None:
    """空选项、重复选项与缺少 name 的表单字段都必须被清洗掉。"""
    assert normalize_ui_options(["上海", " 上海 ", "", "杭州"]) == ("上海", "杭州")
    assert normalize_ui_options(None) == ()
    fields = normalize_ui_fields(
        [
            {"name": "city", "label": "城市"},
            {"label": "缺少名称"},
            "not-a-dict",
        ]
    )
    assert fields == ({"name": "city", "label": "城市"},)
