# 文件职责：验证途牛查询结果归一为前端卡片，以及卡片中间件的发布与跳过行为。
# 定义航班/火车/酒店三类真实载荷 fixture、缺失载荷降级与中间件用例。
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from langchain.messages import ToolMessage

from travel_agent_agent.agents.base import AgentContext
from travel_agent_agent.agents.common.result_cards import (
    ToolResultCardMiddleware,
    build_result_card,
    build_result_card_middleware,
)

_FLIGHT_PAYLOAD = {
    "result": json.dumps(
        {
            "data": [
                {
                    "airlineCompany": "吉祥",
                    "flightNumber": "HO1254",
                    "departureTime": "2026-09-20 21:25",
                    "arrivalTime": "2026-09-20 23:35",
                    "departureAirport": "大兴",
                    "arrivalAirport": "浦东",
                    "cabinClass": "经济舱",
                    "remainingSeats": "9",
                    "totalDuration": "2h10m",
                    "type": "直飞",
                    "basePrice": "370",
                    "airComImageUrl": ["http://cdn/x.png"],
                }
            ]
        },
        ensure_ascii=False,
    )
}

_TRAIN_PAYLOAD = {
    "result": json.dumps(
        {
            "successCode": True,
            "data": [
                {
                    "trainNum": "1461",
                    "departStationName": "北京",
                    "destStationName": "上海",
                    "trainType": "direct",
                    "departureTime": "2026-09-20 11:59",
                    "arrivalTime": "2026-09-21 06:45",
                    "duration": "18时46分",
                    "price": {"wzPrice": "156.5", "ywPrice": "304.5", "edzPrice": ""},
                    "seatAvailable": {"wzNum": 99, "ywNum": 13},
                }
            ],
        },
        ensure_ascii=False,
    )
}

_HOTEL_PAYLOAD = {
    "message": "找到 8 家酒店",
    "totalPageNum": 2,
    "hotels": [
        {
            "hotelId": 1935284133,
            "hotelName": "全季酒店（上海外滩金陵东路店）",
            "starName": "高档型",
            "business": "外滩 · 南京路步行街",
            "brandName": "全季",
            "commentScore": 4.6,
            "commentDigest": "地理位置优越，门口就是豫园地铁站",
            "lowestPrice": 920,
            "meal": "无早餐",
            "roomName": "大床房",
        }
    ],
}


class _Recorder:
    """收集诊断回调发布的卡片事件。"""

    def __init__(self) -> None:
        """初始化空事件列表。"""
        self.events: list[tuple[str, dict[str, object]]] = []

    async def publish(self, event_type: str, data: dict[str, object]) -> None:
        """保存事件类型与数据。"""
        self.events.append((event_type, data))


def _request(tool_name: str) -> Any:
    """构造最小工具调用请求替身。"""
    return SimpleNamespace(tool_call={"name": tool_name, "id": "call_1", "args": {}})


def test_train_card_extracts_lowest_seat_and_direct_flag() -> None:
    """火车卡片取最低非空席别价格、席别名与直达标记。"""
    card = build_result_card("search_tuniu_train", _TRAIN_PAYLOAD)

    assert card is not None
    assert card["kind"] == "train"
    assert card["count"] == 1
    item = card["items"][0]
    assert item["code"] == "1461"
    assert (item["fromStation"], item["toStation"]) == ("北京", "上海")
    assert item["price"] == "156.5"
    assert item["cabin"] == "无座"
    assert item["transport"] == "直达"
    assert item["duration"] == "18时46分"


def test_flight_card_extracts_route_and_price() -> None:
    """航班卡片包含航司航班号、起降、时长与基础票价。"""
    card = build_result_card("search_tuniu_flight", _FLIGHT_PAYLOAD)

    assert card is not None
    item = card["items"][0]
    assert item["code"] == "HO1254"
    assert item["airline"] == "吉祥"
    assert (item["fromStation"], item["toStation"]) == ("大兴", "浦东")
    assert item["cabin"] == "经济舱"
    assert item["price"] == "370"
    assert "airComImageUrl" not in item


def test_hotel_card_formats_price_and_score() -> None:
    """酒店卡片展示商圈、品牌、星级、评分与「¥XXX起」价格。"""
    card = build_result_card("search_tuniu_hotel", _HOTEL_PAYLOAD)

    assert card is not None
    assert card["kind"] == "hotel"
    item = card["items"][0]
    assert item["code"] == "全季酒店（上海外滩金陵东路店）"
    assert item["fromStation"] == "外滩 · 南京路步行街"
    assert item["cabin"] == "高档型"
    assert item["duration"] == "4.6 分"
    assert item["price"] == "920"
    assert item["priceLabel"] == "起"


def test_cards_cap_items_and_reject_unknown_payloads() -> None:
    """卡片最多 5 条；工具名不匹配或载荷无效时返回 None，由前端退回 Markdown 表格。"""
    many = {"hotels": [dict(_HOTEL_PAYLOAD["hotels"][0], hotelId=index) for index in range(8)]}
    card = build_result_card("search_tuniu_hotel", many)

    assert card is not None
    assert len(card["items"]) == 5
    assert build_result_card("cancel_booking", _HOTEL_PAYLOAD) is None
    assert build_result_card("search_tuniu_hotel", {"hotels": []}) is None
    assert build_result_card("search_tuniu_train", "不是对象") is None


@pytest.mark.asyncio
async def test_card_middleware_publishes_only_for_target_tools() -> None:
    """只有三个查询工具成功时才发布 result_card；其它工具与失败结果都不发布。"""
    recorder = _Recorder()
    context = AgentContext(
        trace_id="trace", diagnostic_callback=recorder.publish
    )
    middleware = ToolResultCardMiddleware(lambda: context)

    async def handler(_: Any) -> Any:
        """返回可识别的酒店结果。"""
        return _HOTEL_PAYLOAD

    async def other_handler(_: Any) -> Any:
        """返回不可识别的结果。"""
        return {"status": "booking_not_found"}

    await middleware.awrap_tool_call(_request("search_tuniu_hotel"), handler)
    await middleware.awrap_tool_call(_request("cancel_booking"), other_handler)
    await middleware.awrap_tool_call(
        _request("search_tuniu_flight"), other_handler
    )

    assert [event_type for event_type, _ in recorder.events] == ["result_card"]
    assert recorder.events[0][1]["kind"] == "hotel"
    assert build_result_card_middleware(lambda: None)


@pytest.mark.asyncio
async def test_card_middleware_parses_tool_message_text() -> None:
    """工具结果是 ToolMessage 文本时同样能解析出卡片。"""
    recorder = _Recorder()
    context = AgentContext(trace_id="trace", diagnostic_callback=recorder.publish)
    middleware = ToolResultCardMiddleware(lambda: context)

    async def handler(_: Any) -> Any:
        """返回 JSON 文本形式的火车票结果。"""
        return ToolMessage(content=json.dumps(_TRAIN_PAYLOAD), tool_call_id="call_1")

    await middleware.awrap_tool_call(_request("search_tuniu_train"), handler)

    assert recorder.events[0][1]["kind"] == "train"
