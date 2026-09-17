// 文件职责：管理员面板，包含审批决策、调试直达智能体与脱敏审计。
// 定义 AdminPanel 组件。

import {
  Alert,
  Button,
  Card,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useState } from "react";
import * as adminApi from "../api/admin";
import type { AdminAuditEntry } from "../types/conversations";
import type { ApprovalRecord, DebugAgent } from "../types/preferences";

const { Paragraph, Text } = Typography;

interface Props {
  audit: AdminAuditEntry[];
  adminStatus: string | null;
  onRefreshAudit: () => void;
}

export function AdminPanel({ audit, adminStatus, onRefreshAudit }: Props) {
  const [approvals, setApprovals] = useState<ApprovalRecord[]>([]);
  const [agents, setAgents] = useState<DebugAgent[]>([]);
  const [debugAgentName, setDebugAgentName] = useState<string>("");
  const [debugInput, setDebugInput] = useState("");
  const [debugReply, setDebugReply] = useState("");
  const [remark, setRemark] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [nextApprovals, nextAgents] = await Promise.all([
        adminApi.listApprovals("PENDING"),
        adminApi.listDebugAgents(),
      ]);
      setApprovals(nextApprovals);
      setAgents(nextAgents);
      setDebugAgentName((current) => current || nextAgents[0]?.name || "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "管理员数据读取失败");
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const decide = async (processInstanceId: string, decision: "approve" | "reject") => {
    setBusy(true);
    setError(null);
    try {
      await adminApi.decideApproval(processInstanceId, decision, remark || undefined);
      setRemark("");
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "审批决策失败");
    } finally {
      setBusy(false);
    }
  };

  const runDebug = async () => {
    if (!debugAgentName || !debugInput.trim()) return;
    setBusy(true);
    setError(null);
    setDebugReply("");
    try {
      const result = await adminApi.debugAgent(debugAgentName, debugInput.trim());
      setDebugReply(result.assistant_reply || "（该智能体未返回文本）");
    } catch (e) {
      setError(e instanceof Error ? e.message : "调试调用失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Alert
        showIcon
        type={adminStatus === "authorized" ? "success" : "warning"}
        message={`管理员状态：${adminStatus ?? "不可用"}`}
      />
      {error ? <Alert closable showIcon type="error" message={error} onClose={() => setError(null)} /> : null}
      <Card
        size="small"
        title="待处理审批"
        extra={
          <Button size="small" onClick={() => void reload()}>
            刷新
          </Button>
        }
      >
        <Input
          value={remark}
          onChange={(e) => setRemark(e.target.value)}
          placeholder="审批备注（可选，会随决策一起记录）"
          style={{ marginBottom: 8 }}
        />
        <Table
          rowKey="process_instance_id"
          size="small"
          pagination={{ pageSize: 5 }}
          dataSource={approvals}
          locale={{ emptyText: "暂无待处理审批" }}
          columns={[
            { title: "流程实例", dataIndex: "process_instance_id" },
            { title: "差旅单", dataIndex: "order_id" },
            { title: "标题", dataIndex: "title" },
            { title: "状态", dataIndex: "status", render: (value: string) => <Tag>{value}</Tag> },
            {
              title: "操作",
              render: (_: unknown, row: ApprovalRecord) => (
                <Space>
                  <Button
                    size="small"
                    type="primary"
                    loading={busy}
                    onClick={() => void decide(row.process_instance_id, "approve")}
                  >
                    通过
                  </Button>
                  <Button
                    size="small"
                    danger
                    loading={busy}
                    onClick={() => void decide(row.process_instance_id, "reject")}
                  >
                    驳回
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Card size="small" title="调试直达 Agent">
        <Space direction="vertical" size="small" style={{ width: "100%" }}>
          <Space wrap>
            <Select
              value={debugAgentName || undefined}
              style={{ minWidth: 200 }}
              options={agents.map((agent) => ({
                value: agent.name,
                label: `${agent.name}${agent.enabled ? "" : "（未启用）"}`,
              }))}
              onChange={setDebugAgentName}
              placeholder="选择智能体"
            />
            <Input
              value={debugInput}
              onChange={(e) => setDebugInput(e.target.value)}
              placeholder="输入要直接投递给该智能体的消息"
              style={{ width: 360 }}
            />
            <Button type="primary" loading={busy} onClick={() => void runDebug()}>
              直达调用
            </Button>
          </Space>
          <Text type="secondary">绕过意图识别与主控，直接把消息交给所选智能体，用于排查单个智能体行为。</Text>
          {debugReply ? <Paragraph className="debug-reply">{debugReply}</Paragraph> : null}
        </Space>
      </Card>
      <Card
        size="small"
        title="脱敏审计"
        extra={
          <Button size="small" onClick={onRefreshAudit}>
            刷新审计
          </Button>
        }
      >
        <Table
          rowKey={(row) => `${row.created_at}-${row.event_type}-${row.request_id}`}
          size="small"
          pagination={{ pageSize: 5 }}
          dataSource={audit}
          columns={[
            { title: "事件", dataIndex: "event_type" },
            { title: "结果", dataIndex: "outcome" },
            { title: "Run", dataIndex: "run_id" },
            {
              title: "时间",
              dataIndex: "created_at",
              render: (value: string) => new Date(value).toLocaleString(),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
