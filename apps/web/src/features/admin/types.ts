// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件定义管理员业务使用的审批、审计和 Agent 调试类型。
// 定义 ApprovalRecord、DebugAgent 和 AdminAuditEntry，对应管理员 API 的公开字段。

export interface ApprovalRecord {
  process_instance_id: string;
  order_id: string | null;
  title: string | null;
  status: string;
  remark: string | null;
  submit_time: string | null;
  update_time: string | null;
}

export interface DebugAgent {
  name: string;
  provider_key: string | null;
  enabled: boolean;
}

export interface AdminAuditEntry {
  event_type: string;
  actor_user_id: string | null;
  outcome: string;
  trace_id: string;
  request_id: string;
  run_id: string | null;
  thread_id: string | null;
  created_at: string;
}

