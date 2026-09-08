# 本文件检查项目 Python、TypeScript 与 TSX 源文件的中文职责注释。
# 定义 iter_source_files，用于枚举需检查的源文件。
# 定义 validate_source_header，用于校验文件头；定义 main，用于输出检查结果和退出码。
from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

_CHINESE_CHARACTER = re.compile(r"[\u4e00-\u9fff]")
_SOURCE_SUFFIXES = {".py", ".ts", ".tsx"}
_DEFAULT_SOURCE_DIRECTORIES = ("apps/web/src", "services")


def iter_source_files(project_root: Path, source_directories: Iterable[Path]) -> Iterable[Path]:
    """枚举前端和服务端源码，跳过缓存、构建产物与第三方依赖目录。"""
    ignored_directories = {"__pycache__", "node_modules", "dist", ".venv"}
    for source_directory in source_directories:
        if not source_directory.exists():
            continue
        for source_file in source_directory.rglob("*"):
            if (
                source_file.is_file()
                and source_file.suffix in _SOURCE_SUFFIXES
                and not any(part in ignored_directories for part in source_file.parts)
            ):
                yield source_file.relative_to(project_root)


def validate_source_header(source_file: Path) -> str | None:
    """检查首个非空行是否为带中文职责说明的对应语言注释。"""
    expected_prefix = "#" if source_file.suffix == ".py" else "//"
    for line in source_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith(expected_prefix):
            return "首个非空行不是职责注释"
        if not _CHINESE_CHARACTER.search(stripped):
            return "职责注释不含中文说明"
        return None
    return "源文件为空"


def main() -> int:
    """执行源码头检查；发现任一不合规文件时返回非零退出码。"""
    parser = argparse.ArgumentParser(
        description="检查 Python、TypeScript 和 TSX 源文件中文职责注释"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--path", type=Path, action="append")
    arguments = parser.parse_args()
    project_root = arguments.root.resolve()
    source_directories = (
        [project_root / relative_path for relative_path in arguments.path]
        if arguments.path
        else [project_root / relative_path for relative_path in _DEFAULT_SOURCE_DIRECTORIES]
    )
    failures = [
        f"{source_file}: {reason}"
        for source_file in iter_source_files(project_root, source_directories)
        if (reason := validate_source_header(project_root / source_file)) is not None
    ]
    if failures:
        print("源码文件头检查失败：", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 1
    print("源码文件头检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
