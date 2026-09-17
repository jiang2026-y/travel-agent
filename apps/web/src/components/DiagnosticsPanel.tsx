// 文件职责：展示面向用户的运行摘要（状态、识别诉求、用量、错误提示）与能力清单。
// 定义 DiagnosticsPanel 与 CapabilityPanel 组件；不暴露任何内部字段名或错误码。

import { Card, Descriptions, List, Space, Tag, Typography } from "antd";
import { intentLabel, latestDiagnosticError } from "../lib/diagnostics";
import type { AgentCapability, RunDiagnostics } from "../types/conversations";

const { Text } = Typography;

interface DiagnosticsProps {
  status: string;
  diagnostics?: RunDiagnostics;
}

export function DiagnosticsPanel({ status, diagnostics }: DiagnosticsProps) {
  const error = diagnostics?.stages?.length ? latestDiagnosticError(diagnostics.stages) : null;
  const intent = diagnostics?.stages?.find((stage) => stage.type === "intent_recognition");
  const intentCode = intent?.data.intent_code;
  const usage = diagnostics?.token_usage;
  const usageText =
    usage && typeof usage.total_tokens === "number"
      ? `${usage.total_tokens} tokens / ${usage.model_calls ?? 0} 次模型调用`
      : "";
  return (
    <Card size="small" title="本次运行" className="diagnostics-card">
      <Descriptions column={1} size="small" colon={false}>
        <Descriptions.Item label="状态">{statusLabel(status)}</Descriptions.Item>
        {typeof intentCode === "string" && intentCode ? (
          <Descriptions.Item label="识别诉求">{intentLabel(intentCode)}</Descriptions.Item>
        ) : null}
        {usageText ? (
          <Descriptions.Item label="模型用量">{usageText}</Descriptions.Item>
        ) : null}
      </Descriptions>
      {error ? <Text type="danger">{error}</Text> : null}
    </Card>
  );
}

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    queued: "排队中",
    created: "已创建",
    running: "处理中",
    clarifying: "等待您补充信息",
    proposed: "方案待确认",
    awaiting_approval: "等待审批",
    completed: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status] ?? status;
}

export function CapabilityPanel({ capabilities }: { capabilities: AgentCapability[] }) {
  return (
    <Card size="small" title="已实现 Agent 能力" className="capability-panel">
      <List
        size="small"
        dataSource={capabilities}
        locale={{ emptyText: "能力清单不可用" }}
        renderItem={(agent) => (
          <List.Item>
            <List.Item.Meta
              title={
                <Space>
                  {agent.display_name}
                  <Tag
                    color={
                      agent.status === "enabled"
                        ? "green"
                        : agent.status === "readonly"
                          ? "orange"
                          : "default"
                    }
                  >
                    {agent.status === "enabled"
                      ? "已启用"
                      : agent.status === "readonly"
                        ? "只读"
                        : "未启用"}
                  </Tag>
                </Space>
              }
              description={agent.tool_categories.join(" / ") || "暂无工具"}
            />
          </List.Item>
        )}
      />
    </Card>
  );
}
