# 本文件提供模型 JSON 响应的最小兼容解析工具。
# 定义 parse_model_json，兼容纯 JSON 与 ```json/``` 代码块包裹的 JSON。
from __future__ import annotations

import json
from typing import Any


def parse_model_json(raw: str) -> Any:
    """去除模型响应首尾代码块标记后解析 JSON。"""
    content = raw.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    return json.loads(content)
