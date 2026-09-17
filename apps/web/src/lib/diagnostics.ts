// 文件职责：集中定义运行状态、事件标签、错误文案与进展时间线的推导逻辑。
// 定义 statusLabels、stageLabels、toolLabels、providerErrorMessages、formatDiagnosticData、
// buildProgressSteps 与 latestDiagnosticError，供工作台各组件复用。

import type {
  ResultCardPayload,
  RunDiagnostics,
  RunEvent,
} from "../types/conversations";

export const terminalStatuses = ["completed", "failed", "cancelled"];

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  created: "已创建",
  running: "运行中",
  clarifying: "等待补充",
  proposed: "方案待确认",
  awaiting_approval: "等待审批",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

export const stageLabels: Record<string, string> = {
  run_started: "已收到消息",
  intent_recognition: "意图识别",
  query_rewrite: "问题改写",
  route_selected: "路由决策",
  master_started: "主智能体开始",
  master_completed: "主智能体完成",
  sub_agent_started: "子智能体开始",
  sub_agent_completed: "子智能体完成",
  tool_started: "工具调用开始",
  tool_completed: "工具调用完成",
  tool_summary: "工具调用摘要",
  run_error: "执行错误",
  recommendation_started: "推荐开始",
  recommendation_completed: "推荐完成",
  knowledge_search_started: "知识库检索开始",
  knowledge_search_completed: "知识库检索完成",
  knowledge_search_failed: "知识库检索失败",
  conversation_title: "会话标题",
  token_usage: "Token 用量",
  interrupted: "已停止生成",
  result_card: "结果卡片",
};

/** 工具名到中文说明的映射，未登记的工具直接显示原名。 */
export const toolLabels: Record<string, string> = {
  query_weather: "查询目的地天气",
  query_destination_news: "查询目的地资讯",
  retrieve_knowledge: "检索企业知识库",
  query_travel_policy: "查询差旅政策",
  check_travel_policy: "校验差旅政策",
  quick_visa_check: "快速签证检查",
  check_visa_requirement: "查询签证要求",
  check_transit_visa: "查询过境签",
  compare_destinations: "对比多国签证",
  get_recent_changes: "查询签证政策变更",
  get_coverage_stats: "查询签证覆盖范围",
  query_travel_order: "查询差旅单",
  query_travel_orders: "查询差旅单列表",
  query_travel_order_by_order_id: "按单号查询差旅单",
  query_approval_status: "查询审批状态",
  check_travel_order_approval: "校验差旅单审批",
  check_travel_time_validity: "校验出行时间",
  submit_travel_approval: "提交差旅审批",
  cancel_travel_order: "取消差旅单",
  modify_travel_order: "修改差旅单",
  query_booking_record: "查询预订记录",
  cancel_booking: "取消预订",
  query_user_base_location: "查询常驻城市",
  search_tuniu_flight: "查询航班",
  search_tuniu_train: "查询火车票",
  search_tuniu_hotel: "查询酒店",
  create_tuniu_flight_order: "创建机票订单",
  create_tuniu_train_order: "创建火车票订单",
  create_tuniu_hotel_order: "创建酒店订单",
  load_skill_through_path: "加载技能说明",
  execute_shell_command: "执行技能命令",
  record_to_memory: "记录长期偏好",
  retrieve_from_memory: "召回长期偏好",
};

export const providerErrorMessages: Record<string, string> = {
  dashscope_quota_exhausted: "百炼模型额度已耗尽，请在百炼控制台恢复额度或关闭「仅使用免费额度」后重试。",
  dashscope_auth_failed: "百炼服务认证失败，请检查服务端 DashScope Key 是否有效。",
  dashscope_model_unavailable: "当前模型不存在或账号无权访问，请在百炼控制台检查模型权限。",
  dashscope_upstream_unavailable: "百炼上游服务暂不可用，请稍后重试。",
  bailian_knowledge_not_configured: "百炼知识库未启用，请检查服务端知识库配置。",
  bailian_secret_unavailable: "百炼知识库凭据不可用，请检查服务端 Secret。",
  bailian_secret_invalid: "百炼知识库凭据格式无效，请检查服务端 Secret。",
  bailian_retrieve_failed: "百炼知识库检索失败，请检查知识库权限或稍后重试。",
  bailian_permission_denied: "百炼知识库权限不足（服务端 RAM 账号缺少 sfm:Retrieve 授权），请联系管理员配置。",
  knowledge_search_failed: "知识库暂不可用，请稍后重试。",
};

export function toolLabel(tool: unknown): string {
  const name = typeof tool === "string" ? tool : "";
  return toolLabels[name] ?? (name || "工具调用");
}

export function formatDiagnosticData(data: Record<string, unknown>): string {
  return Object.entries(data)
    .filter(([key]) => key !== "diagnostics" && key !== "error_type")
    .map(([key, value]) => {
      if (key === "error_code" && typeof value === "string") {
        return `错误：${providerErrorMessages[value] ?? value}`;
      }
      if (key === "tool") {
        return `工具：${toolLabel(value)}`;
      }
      return `${key}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`;
    })
    .join(" · ");
}

/** 面向用户的步骤说明：只输出中文可读信息，不暴露内部字段名与错误码。 */
export function describeEvent(type: string, data: Record<string, unknown>): string {
  const text = (key: string): string =>
    typeof data[key] === "string" ? String(data[key]) : "";
  if (type === "run_started") return "正在处理本次请求";
  if (type === "master_started") return "正在理解并安排处理方式";
  if (type === "master_completed") return "";
  if (type === "intent_recognition") {
    const intent = text("intent_code");
    const source = text("source");
    const sourceLabel =
      source === "RULE" ? "关键词匹配" : source === "VECTOR" ? "语义匹配" : "模型识别";
    return intent ? `识别诉求：${intentLabel(intent)}（${sourceLabel}）` : "";
  }
  if (type === "query_rewrite") {
    return text("status") === "completed" ? "已结合上下文补全问题" : "";
  }
  if (type === "route_selected") {
    const agent = text("target_agent");
    return agent ? `交由：${agentLabel(agent)}` : "";
  }
  if (type === "sub_agent_started" || type === "sub_agent_completed") {
    const agent = text("agent");
    return agent ? agentLabel(agent) : "";
  }
  if (type === "tool_started" || type === "tool_completed") {
    const tool = text("tool");
    const duration = typeof data.duration_ms === "number" ? `${data.duration_ms}ms` : "";
    return [toolLabel(tool), duration].filter(Boolean).join(" · ");
  }
  if (type === "token_usage") {
    const total = data.total_tokens;
    const calls = data.model_calls;
    if (typeof total !== "number") return "";
    return typeof calls === "number" ? `${total} tokens · ${calls} 次模型调用` : `${total} tokens`;
  }
  if (type === "knowledge_search_completed") {
    const count = data.count;
    return typeof count === "number" ? `命中 ${count} 条知识片段` : "";
  }
  if (type === "result_card") return "";
  return "";
}

/** 意图 code → 中文名，未登记时回退为原文。 */
const INTENT_LABELS: Record<string, string> = {
  travel_application: "差旅申请",
  travel_cancel: "取消出差",
  travel_modify: "修改差旅",
  approval_query: "审批进度查询",
  travel_order_query: "差旅单查询",
  itinerary_planning: "行程规划",
  flight_search: "航班查询",
  train_search: "火车票查询",
  hotel_search: "酒店查询",
  booking: "预订处理",
  reimbursement: "报销咨询",
  policy_query: "差旅政策查询",
  attractions_query: "目的地游览信息",
  general_info: "通用出行信息",
  greeting: "问候",
  unknown: "待确认诉求",
};

/** Agent 名 → 中文名。 */
const AGENT_LABELS: Record<string, string> = {
  masterAgent: "智能差旅助手",
  itineraryManageAgent: "行程管理",
  bookingAgent: "预订助手",
  infoAgent: "信息查询",
  itineraryPlanAgent: "行程规划",
  itineraryReviewAgent: "行程审核",
};

export function intentLabel(code: string): string {
  return INTENT_LABELS[code] ?? code;
}

export function agentLabel(name: string): string {
  return AGENT_LABELS[name] ?? name;
}

export function latestDiagnosticError(stages: RunDiagnostics["stages"]): string | null {
  for (const stage of [...stages].reverse()) {
    if (stage.type !== "run_error" && stage.type !== "knowledge_search_failed") continue;
    const code = stage.data.error_code;
    if (typeof code === "string") return providerErrorMessages[code] ?? `执行失败（错误码：${code}）`;
  }
  return null;
}

export interface ProgressStep {
  key: string;
  label: string;
  detail: string;
  status: "running" | "done" | "failed";
  timestamp?: string;
}

export interface TurnFlow {
  steps: ProgressStep[];
  cards: ResultCardPayload[];
  hasRunning: boolean;
  hasFailed: boolean;
}

/** 把一轮事件拆成"执行步骤 + 结果卡片"，供助手消息内联展示。 */
export function buildTurnFlow(events: RunEvent[], terminal: boolean): TurnFlow {
  const steps = buildProgressSteps(events, terminal);
  const cards: ResultCardPayload[] = [];
  for (const event of events) {
    if (event.type !== "result_card") continue;
    const data = event.data as unknown as ResultCardPayload;
    if (data && typeof data.kind === "string" && Array.isArray(data.items)) {
      cards.push(data);
    }
  }
  return {
    steps,
    cards,
    hasRunning: steps.some((step) => step.status === "running"),
    hasFailed: steps.some((step) => step.status === "failed"),
  };
}

/**
 * 把 Run 事件流折叠成一条可读的进展时间线：
 * 同一工具的 started/completed 合并为一步，未配对的 started 标记为进行中。
 */
export function buildProgressSteps(events: RunEvent[], terminal: boolean): ProgressStep[] {
  const steps: ProgressStep[] = [];
  const toolIndex = new Map<string, number>();
  for (const event of events) {
    const type = event.type;
    const data = event.data ?? {};
    if (type === "result_card") continue;
    if (type === "tool_started") {
      const key = `${String(data.agent ?? "")}:${String(data.tool ?? "")}`;
      toolIndex.set(key, steps.length);
      steps.push({
        key: `${key}:${steps.length}`,
        label: toolLabel(data.tool),
        detail: String(data.agent ?? ""),
        status: terminal ? "done" : "running",
        timestamp: event.timestamp,
      });
      continue;
    }
    if (type === "tool_completed") {
      const key = `${String(data.agent ?? "")}:${String(data.tool ?? "")}`;
      const index = toolIndex.get(key);
      const failed = data.status === "failed";
      const duration = typeof data.duration_ms === "number" ? `${data.duration_ms}ms` : "";
      if (index === undefined) {
        steps.push({
          key: `${key}:${steps.length}`,
          label: toolLabel(data.tool),
          detail: duration,
          status: failed ? "failed" : "done",
          timestamp: event.timestamp,
        });
      } else {
        const step = steps[index];
        step.status = failed ? "failed" : "done";
        step.detail = [step.detail, duration].filter(Boolean).join(" · ");
      }
      continue;
    }
    if (!(type in stageLabels)) continue;
    if (type === "master_completed" && !terminal) continue;
    steps.push({
      key: `${type}:${steps.length}`,
      label: stageLabels[type],
      detail: describeEvent(type, data),
      status:
        type === "run_error" || type === "knowledge_search_failed"
          ? "failed"
          : type === "interrupted"
            ? "failed"
            : "done",
      timestamp: event.timestamp,
    });
  }
  return steps;
}
