# 本文件验证一次性 HITL Token 的签发、轮换、消费和身份绑定。
# 定义待确认交互构造器及 Token 轮换、重复消费和参数篡改测试。
from __future__ import annotations

import pytest

from travel_agent_agent.orchestration.interactions import (
    InMemoryPendingInteractionStore,
    InteractionError,
    PendingInteraction,
    canonical_args_hash,
)


def _interaction() -> PendingInteraction:
    """构造绑定固定调用链和写工具参数的审批交互。"""
    return PendingInteraction(
        interaction_id="hitl_001",
        kind="approval",
        status="issued",
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
        graph_thread_id="thread_001:itinerary_manage",
        tool_name="cancel_travel_order",
        args_hash=canonical_args_hash({"order_id": "order_001"}),
        allowed_decisions=("approve", "reject", "edit"),
        summary={"question": "是否取消差旅单？"},
        token_hash=None,
        action_version=1,
        created_at="",
    )


@pytest.mark.asyncio
async def test_display_rotates_token_and_only_hash_is_stored() -> None:
    """页面恢复读取应轮换 Token：只宽限上一版，更早的 Token 必须失效。"""
    store = InMemoryPendingInteractionStore({})
    _, first_token = await store.issue(_interaction())

    displayed = await store.get_for_display(
        "user_001", "conversation_001", "run_001", "thread_001"
    )

    assert displayed is not None
    second_token = displayed["confirmation_token"]
    assert second_token != first_token
    assert first_token not in store.records["hitl_001"].to_json()
    # 再次展示会再轮换一版，此时 first_token 已超出宽限窗口。
    displayed_again = await store.get_for_display(
        "user_001", "conversation_001", "run_001", "thread_001"
    )
    assert displayed_again is not None
    third_token = displayed_again["confirmation_token"]
    assert third_token not in {first_token, second_token}
    # 更早的一版必须失效（超出宽限窗口）。
    with pytest.raises(InteractionError, match="confirmation_token_invalid"):
        await store.consume(
            "hitl_001",
            first_token,
            user_id="user_001",
            conversation_id="conversation_001",
            run_id="run_001",
            thread_id="thread_001",
            tool_name="cancel_travel_order",
            args_hash=canonical_args_hash({"order_id": "order_001"}),
            decision="approve",
        )
    # 上一版 Token 仍可用：前端轮询刷新令牌与用户点击之间存在时间差。
    consumed = await store.consume(
        "hitl_001",
        second_token,
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
        tool_name="cancel_travel_order",
        args_hash=canonical_args_hash({"order_id": "order_001"}),
        decision="approve",
    )
    assert consumed.status == "consumed"


@pytest.mark.asyncio
async def test_token_rejects_replay_and_parameter_tampering() -> None:
    """同一 Token 不能消费两次，也不能替换为不同工具参数。"""
    store = InMemoryPendingInteractionStore({})
    _, token = await store.issue(_interaction())

    with pytest.raises(InteractionError, match="interaction_action_mismatch"):
        await store.consume(
            "hitl_001",
            token,
            user_id="user_001",
            conversation_id="conversation_001",
            run_id="run_001",
            thread_id="thread_001",
            tool_name="cancel_travel_order",
            args_hash=canonical_args_hash({"order_id": "order_002"}),
            decision="approve",
        )

    await store.consume(
        "hitl_001",
        token,
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
        tool_name="cancel_travel_order",
        args_hash=canonical_args_hash({"order_id": "order_001"}),
        decision="approve",
    )
    with pytest.raises(InteractionError, match="interaction_not_consumable"):
        await store.consume(
            "hitl_001",
            token,
            user_id="user_001",
            conversation_id="conversation_001",
            run_id="run_001",
            thread_id="thread_001",
            tool_name="cancel_travel_order",
            args_hash=canonical_args_hash({"order_id": "order_001"}),
            decision="approve",
        )


@pytest.mark.asyncio
async def test_hidden_interaction_stops_display_until_consumed() -> None:
    """用户提交批准后交互不再展示，但同一 Token 仍可被写事务原子消费。"""
    store = InMemoryPendingInteractionStore({})
    await store.issue(_interaction())
    displayed = await store.get_for_display(
        "user_001", "conversation_001", "run_001", "thread_001"
    )
    assert displayed is not None
    token = displayed["confirmation_token"]

    await store.hide_from_display(
        "hitl_001",
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
    )

    # 恢复执行期间不得再向前端暴露同一张确认卡片。
    assert (
        await store.get_for_display(
            "user_001", "conversation_001", "run_001", "thread_001"
        )
        is None
    )
    # 记录本身与 Token 哈希保持不变，写事务仍能原子消费。
    assert store.records["hitl_001"].status == "issued"
    consumed = await store.consume(
        "hitl_001",
        token,
        user_id="user_001",
        conversation_id="conversation_001",
        run_id="run_001",
        thread_id="thread_001",
        tool_name="cancel_travel_order",
        args_hash=canonical_args_hash({"order_id": "order_001"}),
        decision="approve",
    )
    assert consumed.status == "consumed"


@pytest.mark.asyncio
async def test_hide_from_display_rejects_foreign_identity() -> None:
    """摘除展示索引同样受身份绑定约束，不能跨用户隐藏他人交互。"""
    store = InMemoryPendingInteractionStore({})
    await store.issue(_interaction())

    with pytest.raises(InteractionError, match="interaction_identity_mismatch"):
        await store.hide_from_display(
            "hitl_001",
            user_id="user_002",
            conversation_id="conversation_001",
            run_id="run_001",
            thread_id="thread_001",
        )
