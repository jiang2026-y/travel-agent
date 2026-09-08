# 本文件提供 prompts 包内 Markdown 提示词的安全加载和 include 展开能力。
# 定义 load_prompt 及路径校验函数，拒绝路径穿越、非 Markdown 和循环 include。
from __future__ import annotations

import re
from importlib.resources import files
from pathlib import PurePosixPath

_INCLUDE_PATTERN = re.compile(r"\{\{include:([^}]+)\}\}")
_PROMPT_ROOT = PurePosixPath("prompts")


def load_prompt(relative_path: str) -> str:
    """读取 prompts 包内 Markdown 并递归展开受限 include 引用。"""
    return _load_prompt(_validate_prompt_path(relative_path), ())


def _load_prompt(path: PurePosixPath, stack: tuple[PurePosixPath, ...]) -> str:
    """读取单个 Markdown，并拒绝循环引用和空提示词。"""
    if path in stack:
        raise ValueError(f"prompt_include_cycle:{path}")
    content = files("travel_agent_agent").joinpath(str(path)).read_text(encoding="utf-8")
    if not content.strip():
        raise ValueError(f"prompt_empty:{path}")

    def replace_include(match: re.Match[str]) -> str:
        """将当前提示词同目录下的受限 Markdown 片段展开。"""
        target = _validate_include_path(path.parent, match.group(1).strip())
        return _load_prompt(target, (*stack, path))

    return _INCLUDE_PATTERN.sub(replace_include, content)


def _validate_prompt_path(value: str) -> PurePosixPath:
    """限制入口只能读取 prompts 根目录内的 Markdown 文件。"""
    path = PurePosixPath(value)
    if path.is_absolute() or path.suffix != ".md" or path.parts[:1] != _PROMPT_ROOT.parts:
        raise ValueError(f"prompt_path_invalid:{value}")
    if ".." in path.parts:
        raise ValueError(f"prompt_path_invalid:{value}")
    return path


def _validate_include_path(base: PurePosixPath, value: str) -> PurePosixPath:
    """限制 include 只能指向 prompts 根目录内的相对 Markdown 文件。"""
    candidate = base.joinpath(value)
    if ".." in candidate.parts or candidate.suffix != ".md":
        raise ValueError(f"prompt_include_invalid:{value}")
    if candidate.parts[:1] != _PROMPT_ROOT.parts:
        raise ValueError(f"prompt_include_invalid:{value}")
    return candidate
