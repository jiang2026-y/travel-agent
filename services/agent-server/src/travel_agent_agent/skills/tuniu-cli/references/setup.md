# 途牛 CLI 环境准备与版本维护

> 本文档为 tuniu-cli 技能的**环境/安装/版本维护**参考，仅在首次使用或遇到 CLI 不可用/版本过低时按需加载。

## 运行环境要求

**运行环境必须安装 Node.js 18+ 与 tuniu-cli**，否则无法调用服务。

### 一条命令完成自检与安装（唯一入口）

**不要手工逐条执行 `node --version` / `npm install -g` 等命令**，直接执行技能自带脚本：

```bash
bash scripts/setup.sh
```

> 路径相对于 `load_skill_through_path` 返回的「技能工作目录」。若该相对路径不可达，改用仓库内路径：
> `bash gogo-agent/src/main/resources/skills/tuniu-cli/scripts/setup.sh`

脚本幂等，可重复执行：校验 Node.js/npm → 检查 `tuniu` 版本 → 不达标则全流程安装（自动规避 EACCES，无需 `sudo`）→ 修正 PATH 使裸命令 `tuniu` 可用 → 输出结论行 `RESULT=...`。

### 按脚本结果处置

脚本最后一行形如 `RESULT=OK version=1.0.7 command=tuniu`，据此决定下一步：

| 退出码 | 输出 | 处置 |
| --- | --- | --- |
| 0 | `RESULT=OK` | 环境就绪，直接继续业务调用 `tuniu call ...` |
| 10 | `RESULT=FAIL reason=node_missing` / `node_too_old` | **不要**尝试安装 tuniu-cli；告知用户先安装或升级 Node.js 18+ |
| 11 | `RESULT=FAIL reason=npm_missing` | 告知用户安装 Node.js/npm 后重新执行脚本 |
| 12 | `RESULT=FAIL reason=npm_install_failed` 等 | 安装失败；**不要用 `sudo` 重试**，把脚本输出原样反馈给用户 |
| 13 | `RESULT=PARTIAL` | CLI 已装好但当前进程 PATH 解析不到裸命令；告知用户重启承载 Agent 的服务进程，或在其启动环境中追加脚本输出提示的 bin 目录 |

### 调用形式约束（重要）

- 必须以**裸命令** `tuniu` 开头，例如 `tuniu call <server> <tool> -a '<JSON>'`。
- **禁止**使用绝对路径（如 `/Users/xxx/.npm-global/bin/tuniu`）——不在命令白名单内，会被安全校验拒绝。
- **禁止**使用 `npx tuniu-cli ...` —— 会绕过 Agent 的 API Key 注入，导致 `108 ApiKeyRequiredError`。
- 一条命令中不要出现 `&&`、`||`、`|`、`;` 等分隔符，需分多次执行。

> 脚本内 `MIN_CLI_VERSION` 需与 SKILL.md 头部的 `minCliVersion` 保持一致，升级要求时两处同步修改。

## Skill 版本与更新说明

`tuniu-cli` 提供 **skill** 子命令，用于维护本助手在各 AI Agent 目录下的安装与版本查看，与业务调用（`tuniu call`）相互独立。

### CLI 与 Skill 兼容性

本 skill 依赖 `tuniu-cli` 版本不低于 SKILL.md 头部声明的 `minCliVersion`。使用时必须遵循：

1. 若 `tuniu --version` 低于 `minCliVersion`，执行 `bash scripts/setup.sh` 自动更新 CLI（脚本内部会跑 `npm install -g tuniu-cli@latest`）。
2. 脚本返回 `RESULT=OK` 后再执行 `tuniu skill install` 更新本地 skill。
3. 若全局 npm 安装无权限，脚本会自动将前缀切到 `~/.npm-global`；仍失败时不要用 `sudo`，也不要继续调用低版本 CLI 中不存在的命令。
4. 若更新失败，明确告知用户当前 CLI 版本与 skill 不兼容，部分操作可能失效。

**使用场景简述**

- **`tuniu skill version`**：在已配置多台 Agent（如 Cursor、Claude 等）时，检查各目录下已安装的 skill 版本、来源与安装时间。
- **`tuniu skill install`**：需要**安装或更新**本 skill 时使用。默认仅写入 `~/.agents/skills/tuniu-cli/`；通过 `--agent` 可指定单个、多个（逗号分隔）或 `all`；`--dir` 可额外指定自定义 skills 根目录。
- **`npm install` / `npm ci`**：安装 `tuniu-cli` 时若启用脚本，**postinstall** 可能已自动复制内置 skill；若需与线上一致或显式更新，仍建议执行 `tuniu skill install`。

更完整的参数与示例见：`tuniu skill install --help`。
