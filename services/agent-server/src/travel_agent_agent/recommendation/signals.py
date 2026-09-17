# 文件职责：定义问题推荐与快速续跑共用的固定关键词词表。
# 定义 ContinuationSignals 及 continuation_signals，提供分组提示词、标准化和校验功能。
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ContinuationSignals:
    """保存确认/修改/取消三类快速续跑关键词，并提供安全匹配方法。"""

    groups: MappingProxyType[str, tuple[str, ...]]

    @property
    def values(self) -> frozenset[str]:
        """返回去重后的全部规范化关键词。"""
        return frozenset(value for values in self.groups.values() for value in values)

    def normalize(self, value: str) -> str:
        """清理首尾空白并将拉丁字母关键词统一为小写。"""
        return value.strip().lower()

    def is_valid(self, value: str) -> bool:
        """判断输入是否为完整的单个快速操作关键词。"""
        return self.normalize(value) in self.values

    def group_for(self, value: str) -> str | None:
        """返回关键词所属分组，未知关键词返回空值。"""
        normalized = self.normalize(value)
        for group, values in self.groups.items():
            if normalized in values:
                return group
        return None

    def as_grouped_prompt(self) -> str:
        """按固定顺序生成注入推荐模型的中文分组提示词。"""
        return "\n".join(
            f"- {group}：{'、'.join(values)}" for group, values in self.groups.items()
        )


continuation_signals = ContinuationSignals(
    MappingProxyType(
        {
            "确认/继续类": (
                "确定",
                "确认",
                "提交",
                "继续",
                "是的",
                "好的",
                "对",
                "好",
                "ok",
                "yes",
            ),
            "修改/补充类": ("修改", "补充", "修改一下", "补充一下"),
            "取消/重新类": ("不对", "取消", "重新", "重新来", "再来", "再"),
        }
    )
)
