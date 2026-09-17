// 文件职责：展示待用户补充信息或确认执行的交互卡片。
// 定义 InteractionCard 组件。

import { Button, Card, Input, Space, Typography } from "antd";
import { useState } from "react";
import { interactionQuestion } from "../api/conversations";
import type { PendingInteraction } from "../types/conversations";

const { Paragraph } = Typography;

interface Props {
  interaction: PendingInteraction;
  onDecision: (
    decision: "approve" | "reject" | "edit" | "respond",
    message?: string,
  ) => Promise<void>;
}

export function InteractionCard({ interaction, onDecision }: Props) {
  const [message, setMessage] = useState("");
  const isApproval = interaction.kind === "approval";
  return (
    <Card
      size="small"
      type="inner"
      title={isApproval ? "需要确认" : "需要补充信息"}
      className="interaction-card"
    >
      <Paragraph>{interactionQuestion(interaction)}</Paragraph>
      {interaction.summary.options?.length ? (
        <Space wrap>
          {interaction.summary.options.map((option) => (
            <Button key={option} onClick={() => void onDecision("respond", option)}>
              {option}
            </Button>
          ))}
        </Space>
      ) : null}
      {!isApproval ? (
        <Space.Compact block>
          <Input
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="填写补充信息"
          />
          <Button type="primary" onClick={() => void onDecision("respond", message)}>
            提交
          </Button>
        </Space.Compact>
      ) : (
        <Space>
          <Button
            type="primary"
            disabled={!interaction.confirmation_token}
            onClick={() => void onDecision("approve")}
          >
            确认执行
          </Button>
          <Button onClick={() => void onDecision("reject")}>拒绝</Button>
          <Button onClick={() => void onDecision("edit", message)}>修改</Button>
        </Space>
      )}
    </Card>
  );
}
