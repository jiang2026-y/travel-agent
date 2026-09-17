# 文件职责：把途牛查询工具结果归一为前端可渲染的结果卡片。
# 定义 build_result_card、ToolResultCardMiddleware 与各服务的字段抽取辅助函数：
# 只覆盖机票/火车票/酒店三个查询工具，最多 5 条；载荷不匹配时返回 None，
# 由前端退回渲染助手正文里的 Markdown 表格。
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest

from travel_agent_agent.agents.base import AgentContext

MAX_CARD_ITEMS = 5
_CARD_TOOLS = {
    "search_tuniu_flight": "flight",
    "search_tuniu_train": "train",
    "search_tuniu_hotel": "hotel",
}
_TRAIN_SEAT_LABELS = (
    ("swzPrice", "商务座"),
    ("ydzPrice", "一等座"),
    ("edzPrice", "二等座"),
    ("dwPrice", "动卧"),
    ("rwPrice", "软卧"),
    ("ywPrice", "硬卧"),
    ("rzPrice", "软座"),
    ("yzPrice", "硬座"),
    ("wzPrice", "无座"),
)


def build_result_card(tool_name: str, payload: object) -> dict[str, object] | None:
    """按工具名把原始返回归一为结果卡片；无法识别或没有条目时返回 None。"""
    kind = _CARD_TOOLS.get(tool_name)
    if kind is None or not isinstance(payload, dict):
        return None
    if kind == "hotel":
        items = _hotel_items(payload)
        title = "酒店搜索结果"
    else:
        records = _record_list(payload)
        items = _flight_items(records) if kind == "flight" else _train_items(records)
        title = "航班搜索结果" if kind == "flight" else "火车票搜索结果"
    if not items:
        return None
    return {
        "kind": kind,
        "title": title,
        "source": "tuniu",
        "count": len(items),
        "items": items[:MAX_CARD_ITEMS],
    }


def _record_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从机票/火车票返回中取出记录数组，兼容内嵌 JSON 字符串包装。"""
    body: object = payload
    if "result" in payload:
        body = _loads(payload.get("result"))
    if isinstance(body, dict):
        for key in ("data", "list", "resultList"):
            value = body.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _flight_items(records: list[dict[str, Any]]) -> list[dict[str, object]]:
    """抽取航班卡片条目。"""
    items: list[dict[str, object]] = []
    for record in records:
        number = _text(record, "flightNumber")
        departure = _text(record, "departureTime")
        arrival = _text(record, "arrivalTime")
        if not (number and departure and arrival):
            continue
        items.append(
            {
                "code": number,
                "airline": _text(record, "airlineCompany"),
                "fromStation": _text(record, "departureAirport"),
                "toStation": _text(record, "arrivalAirport"),
                "departureTime": departure,
                "arrivalTime": arrival,
                "duration": _text(record, "totalDuration"),
                "cabin": _text(record, "cabinClass"),
                "seats": _text(record, "remainingSeats"),
                "transport": _text(record, "type"),
                "price": _text(record, "basePrice"),
                "priceLabel": "起",
            }
        )
    return items


def _train_items(records: list[dict[str, Any]]) -> list[dict[str, object]]:
    """抽取火车票卡片条目，价格取最低的非空席别。"""
    items: list[dict[str, object]] = []
    for record in records:
        number = _text(record, "trainNum")
        departure = _text(record, "departureTime")
        arrival = _text(record, "arrivalTime")
        if not (number and departure and arrival):
            continue
        price, seat = _lowest_train_price(record.get("price"))
        items.append(
            {
                "code": number,
                "fromStation": _text(record, "departStationName"),
                "toStation": _text(record, "destStationName"),
                "departureTime": departure,
                "arrivalTime": arrival,
                "duration": _text(record, "duration"),
                "cabin": seat,
                "transport": "直达" if _text(record, "trainType") == "direct" else "",
                "price": price,
                "priceLabel": "起",
            }
        )
    return items


def _hotel_items(payload: dict[str, Any]) -> list[dict[str, object]]:
    """抽取酒店卡片条目；最低价统一渲染为「¥XXX起」。"""
    hotels = payload.get("hotels")
    if not isinstance(hotels, list):
        return []
    items: list[dict[str, object]] = []
    for hotel in hotels:
        if not isinstance(hotel, dict):
            continue
        name = _text(hotel, "hotelName")
        if not name:
            continue
        score = hotel.get("commentScore")
        items.append(
            {
                "code": name,
                "fromStation": _text(hotel, "business") or _text(hotel, "address"),
                "toStation": _text(hotel, "brandName"),
                "cabin": _text(hotel, "starName"),
                "transport": _text(hotel, "roomName"),
                "seats": _text(hotel, "meal"),
                "duration": f"{score} 分" if isinstance(score, (int, float)) else "",
                "detail": _text(hotel, "commentDigest"),
                "price": _hotel_price(hotel.get("lowestPrice")),
                "priceLabel": "起",
            }
        )
    return items


def _hotel_price(value: object) -> str:
    """把酒店最低价格式化为带千分位的整数字符串。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return f"{int(value):,}"


def _lowest_train_price(price: object) -> tuple[str, str]:
    """返回最低非空席别的价格与席别名。"""
    if not isinstance(price, dict):
        return "", ""
    lowest: tuple[float, str, str] | None = None
    for key, label in _TRAIN_SEAT_LABELS:
        raw = price.get(key)
        if raw in (None, "", 0):
            continue
        try:
            amount = float(str(raw))
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        if lowest is None or amount < lowest[0]:
            lowest = (amount, _trim_amount(amount), label)
    if lowest is None:
        return "", ""
    return lowest[1], lowest[2]


def _trim_amount(amount: float) -> str:
    """去掉无意义的小数尾巴（476.5 保留，476.0 变 476）。"""
    return str(int(amount)) if amount.is_integer() else f"{amount:g}"


def _text(record: dict[str, Any], key: str) -> str:
    """读取字符串字段，缺失或非字符串时返回空串。"""
    value = record.get(key)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _trim_amount(float(value))
    return value.strip() if isinstance(value, str) else ""


def _loads(value: object) -> object:
    """尽力解析内嵌 JSON 字符串，失败时原样返回。"""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class ToolResultCardMiddleware(AgentMiddleware):
    """工具执行成功后按需发布结果卡片事件；不影响工具结果本身。"""

    def __init__(self, context_getter: Callable[[], AgentContext | None]) -> None:
        """保存当前请求上下文读取函数。"""
        super().__init__()
        self._context_getter = context_getter

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        """执行工具并在成功且可识别时发布 result_card 事件。"""
        result = await handler(request)
        tool_name = _tool_call_name(request)
        if tool_name in _CARD_TOOLS:
            card = build_result_card(tool_name, _result_payload(result))
            if card is not None:
                await self._publish(card)
        return result

    async def _publish(self, card: dict[str, object]) -> None:
        """经诊断回调发布卡片事件；发布失败不影响工具执行。"""
        context = self._context_getter()
        callback = getattr(context, "diagnostic_callback", None) if context else None
        if callback is None:
            return
        try:
            await callback("result_card", card)
        except Exception:
            return


def build_result_card_middleware(
    context_getter: Callable[[], AgentContext | None],
) -> list[AgentMiddleware]:
    """返回结果卡片中间件列表。"""
    return [ToolResultCardMiddleware(context_getter)]


def _tool_call_name(request: ToolCallRequest) -> str:
    """从工具调用请求中读取工具名。"""
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        if isinstance(name, str):
            return name
    return str(getattr(getattr(request, "tool", None), "name", "") or "")


def _result_payload(result: Any) -> object:
    """把工具结果转换为可解析的对象（ToolMessage 文本或原始字典）。"""
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return _loads(content)
    return content
