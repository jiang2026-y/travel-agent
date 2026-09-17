#!/usr/bin/env bash
# tuniu-cli 环境自检与自动安装（幂等，可重复执行）
#
# 用法：bash <技能目录>/scripts/setup.sh
#
# 做四件事：
#   1. 校验 Node.js / npm 是否满足要求
#   2. 检查 tuniu 命令是否可用且版本达标
#   3. 不达标则自动完成全流程安装（自动规避 EACCES，全程无需 sudo）
#   4. 修正 PATH 可见性，确保后续可直接以裸命令 `tuniu` 调用
#
# 退出码：0=可用  10=Node 缺失/过低  11=npm 缺失  12=安装失败  13=已安装但裸命令不可用
set -uo pipefail

# 记录进入脚本时的原始 PATH，用于最终判定「裸命令是否真的可被外部进程解析」
ORIG_PATH="$PATH"

MIN_CLI_VERSION="1.0.7"
MIN_NODE_MAJOR=18
# 优先把可执行文件软链到这些目录（需同时满足：在 PATH 中、当前用户可写）
PREFERRED_LINK_DIRS="/opt/homebrew/bin /usr/local/bin"

log() { printf '%s\n' "$*"; }

# 版本比较：ver_ge A B —— A >= B 返回 0
ver_ge() {
  [ "$1" = "$2" ] && return 0
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n1)" = "$2" ]
}

# 提取 `tuniu --version` 输出中的 x.y.z（输出形如 "tuniu version 1.0.7"）
cli_version() {
  "$1" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1
}

# 定位可执行的 tuniu：先按 PATH 找，再按 npm 全局前缀兜底
find_tuniu() {
  if command -v tuniu >/dev/null 2>&1; then
    command -v tuniu
    return 0
  fi
  local p
  p="$(npm prefix -g 2>/dev/null)/bin/tuniu"
  if [ -x "$p" ]; then printf '%s\n' "$p"; return 0; fi
  if [ -x "$HOME/.npm-global/bin/tuniu" ]; then printf '%s\n' "$HOME/.npm-global/bin/tuniu"; return 0; fi
  return 1
}

# ---------- 1. Node.js ----------
if ! command -v node >/dev/null 2>&1; then
  log "RESULT=FAIL reason=node_missing"
  log "未检测到 Node.js。请先安装 Node.js ${MIN_NODE_MAJOR}+（https://nodejs.org）后重新执行本脚本；不要尝试安装 tuniu-cli。"
  exit 10
fi
NODE_VER="$(node --version 2>/dev/null | tr -d 'v')"
NODE_MAJOR="${NODE_VER%%.*}"
if ! [ "${NODE_MAJOR:-0}" -ge "$MIN_NODE_MAJOR" ] 2>/dev/null; then
  log "RESULT=FAIL reason=node_too_old node_version=${NODE_VER}"
  log "Node.js 版本过低（当前 ${NODE_VER}，需要 ${MIN_NODE_MAJOR}+）。请升级后重新执行本脚本。"
  exit 10
fi

# ---------- 2. npm ----------
if ! command -v npm >/dev/null 2>&1; then
  log "RESULT=FAIL reason=npm_missing"
  log "未检测到 npm。请安装 Node.js/npm 后重新执行本脚本。"
  exit 11
fi

# ---------- 3. 检查现状，必要时安装 ----------
TUNIU_BIN="$(find_tuniu || true)"
CUR_VER=""
[ -n "$TUNIU_BIN" ] && CUR_VER="$(cli_version "$TUNIU_BIN")"

if [ -n "$CUR_VER" ] && ver_ge "$CUR_VER" "$MIN_CLI_VERSION"; then
  log "已安装 tuniu-cli ${CUR_VER}（>= ${MIN_CLI_VERSION}），跳过安装。"
else
  if [ -n "$CUR_VER" ]; then
    log "当前 tuniu-cli ${CUR_VER} 低于要求的 ${MIN_CLI_VERSION}，开始更新。"
  else
    log "未安装 tuniu-cli，开始安装。"
  fi

  # 全局前缀不可写时切到用户目录，避免 EACCES；切勿使用 sudo
  PREFIX="$(npm prefix -g 2>/dev/null)"
  if [ -z "$PREFIX" ] || [ ! -w "$PREFIX" ]; then
    PREFIX="$HOME/.npm-global"
    mkdir -p "$PREFIX"
    if ! npm config set prefix "$PREFIX" >/dev/null 2>&1; then
      log "RESULT=FAIL reason=npm_config_failed"
      log "无法设置 npm 全局前缀，请检查 ~/.npmrc 权限。"
      exit 12
    fi
    log "npm 全局前缀已切换到用户可写目录：$PREFIX"
  fi

  if ! npm install -g tuniu-cli@latest; then
    log "RESULT=FAIL reason=npm_install_failed"
    log "npm 安装失败。若为 EACCES 权限错误，不要用 sudo（会留下 root 属主文件），确认 npm prefix 指向用户可写目录后重试。"
    exit 12
  fi
  TUNIU_BIN="$PREFIX/bin/tuniu"
fi

if [ ! -x "$TUNIU_BIN" ]; then
  log "RESULT=FAIL reason=binary_not_found path=${TUNIU_BIN}"
  exit 12
fi

# ---------- 4. 修正 PATH 可见性（关键）----------
# 非交互 shell 不加载 ~/.zshrc，且脚本内 export PATH 随进程结束失效，
# 因此把可执行文件软链到「已在当前 PATH 中且可写」的目录，
# 使后续任意进程（含 Agent 的 sh -c）都能直接解析裸命令 tuniu。
BIN_DIR="$(cd "$(dirname "$TUNIU_BIN")" && pwd)"

path_contains() {
  case ":$PATH:" in *":$1:"*) return 0;; *) return 1;; esac
}

if ! command -v tuniu >/dev/null 2>&1; then
  LINK_DIR=""
  for d in $PREFERRED_LINK_DIRS; do
    if [ "$d" != "$BIN_DIR" ] && [ -d "$d" ] && [ -w "$d" ] && path_contains "$d"; then
      LINK_DIR="$d"
      break
    fi
  done
  if [ -z "$LINK_DIR" ]; then
    OLD_IFS="$IFS"; IFS=':'
    for d in $PATH; do
      [ -n "$d" ] || continue
      if [ "$d" != "$BIN_DIR" ] && [ -d "$d" ] && [ -w "$d" ]; then LINK_DIR="$d"; break; fi
    done
    IFS="$OLD_IFS"
  fi

  if [ -n "$LINK_DIR" ]; then
    if ln -sf "$TUNIU_BIN" "$LINK_DIR/tuniu"; then
      log "已软链到 PATH 目录，裸命令即可调用：$LINK_DIR/tuniu -> $TUNIU_BIN"
    else
      log "软链创建失败：$LINK_DIR/tuniu"
    fi
  else
    log "当前 PATH 中没有可写目录，无法创建软链。"
  fi
fi

# 若 bin 目录本不在 PATH 中，顺带写入交互式 shell 配置（仅影响人工终端，幂等）
if ! path_contains "$BIN_DIR"; then
  case "$(basename "${SHELL:-/bin/bash}")" in
    zsh) RC_FILE="$HOME/.zshrc" ;;
    *)   RC_FILE="$HOME/.bash_profile" ;;
  esac
  touch "$RC_FILE" 2>/dev/null
  if [ -w "$RC_FILE" ] && ! grep -qF "$BIN_DIR" "$RC_FILE" 2>/dev/null; then
    printf 'export PATH=%s:$PATH\n' "$BIN_DIR" >> "$RC_FILE"
    log "已将 $BIN_DIR 追加到 $RC_FILE（对已运行进程无效，仅影响新开终端）"
  fi
fi

# ---------- 5. 最终校验 ----------
hash -r 2>/dev/null || true
FINAL_VER="$(cli_version "$TUNIU_BIN")"

if [ -z "$FINAL_VER" ]; then
  log "RESULT=FAIL reason=verify_failed path=${TUNIU_BIN}"
  exit 12
fi

# 用原始 PATH（不含脚本内追加）判定裸命令可用性，等价于 Agent 后续调用时的环境
if ( PATH="$ORIG_PATH"; hash -r 2>/dev/null; command -v tuniu >/dev/null 2>&1 ); then
  log "RESULT=OK version=${FINAL_VER} command=tuniu"
  log "环境就绪：可直接执行 tuniu call <server> <tool> -a '<JSON>'。"
  exit 0
fi

log "RESULT=PARTIAL version=${FINAL_VER} path=${TUNIU_BIN}"
log "tuniu-cli 已安装，但当前进程的 PATH 中仍解析不到裸命令 tuniu。"
log "请重启承载 Agent 的服务进程（使其继承包含 ${BIN_DIR} 的 PATH），或在其启动环境中显式追加该目录。"
exit 13
