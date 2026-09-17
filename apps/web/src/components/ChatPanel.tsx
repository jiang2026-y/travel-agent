// 文件职责：展示对话消息、反馈按钮、交互卡片、推荐问题与输入区域。
// 定义 ChatPanel 组件。

import { Alert, Button, Card, Collapse, Divider, Input, Space, Tag, Timeline, Typography } from "antd";
import { useEffect, useState } from "react";
import type {
  ChatMessage,
  PendingInteraction,
  RunEvent,
  RunSummary,
} from "../types/conversations";
import { buildTurnFlow, statusLabels, terminalStatuses, type TurnFlow } from "../lib/diagnostics";
import { InteractionCard } from "./InteractionCard";
import { Markdown } from "./Markdown";
import { ResultCard } from "./ResultCard";

const { Paragraph, Text, Title } = Typography;

interface Props {
  run: RunSummary | null;
  messages: ChatMessage[];
  recommendations: string[];
  input: string;
  loading: boolean;
  error: string | null;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onDismissError: () => void;
  onDecision: (
    decision: "approve" | "reject" | "edit" | "respond",
    message?: string,
  ) => Promise<void>;
  onRecommendation: (item: string) => void;
  onCancelRun: () => void;
  onFeedback: (messageId: string, feedback: "up" | "down" | null) => void;
  events: RunEvent[];
  streamingReply: string;
}

export function ChatPanel({
  run,
  messages,
  recommendations,
  input,
  loading,
  error,
  onInputChange,
  onSend,
  onDismissError,
  onDecision,
  onRecommendation,
  onCancelRun,
  onFeedback,
  events,
  streamingReply,
}: Props) {
  const running = run !== null && !terminalStatuses.includes(run.status);
  const flow = buildTurnFlow(events, !running);
  const lastAssistantId = [...messages].reverse().find((item) => item.role === "assistant")
    ?.message_id;
  // 本轮尚未产出最终消息时，用一条「进行中」气泡承载实时步骤、卡片与流式正文。
  const lastMessage = messages[messages.length - 1];
  const awaitingReply = !lastMessage || lastMessage.role === "user";
  const showLiveBubble =
    awaitingReply && (flow.steps.length > 0 || flow.cards.length > 0 || streamingReply.length > 0);
  // 执行中把步骤逐条揭示，避免同一批到达的步骤"整片刷出"。
  const revealedSteps = useRevealedSteps(flow.steps.length, showLiveBubble);
  const liveFlow: TurnFlow = { ...flow, steps: flow.steps.slice(0, revealedSteps) };
  return (
    <Card
      className="run-card"
      title={<Title level={3}>旅行助手对话</Title>}
      extra={run ? <Tag color={run.status === "failed" ? "error" : "blue"}>{statusLabels[run.status] ?? run.status}</Tag> : null}
    >
      {error ? (
        <Alert closable showIcon type="error" message={error} onClose={onDismissError} />
      ) : null}
      {run?.status === "failed" && awaitingReply ? (
        <Alert
          showIcon
          type="warning"
          message="本轮未能完成"
          description="请重试，或换一种问法（例如补充出发城市、日期或换个服务再问一次）。"
        />
      ) : null}
      <div className="message-list">
        {messages.length === 0 ? (
          <Paragraph type="secondary">输入一个旅行需求开始验证。</Paragraph>
        ) : (
          messages.map((item) => {
            const isAssistant = item.role === "assistant";
            const showTrace = isAssistant && item.message_id === lastAssistantId;
            return (
              <div className={`message-bubble ${item.role}`} key={item.message_id}>
                <Text strong>{isAssistant ? "助手" : "我"}</Text>
                {isAssistant ? (
                  <Markdown content={item.content} />
                ) : (
                  <Paragraph>{item.content}</Paragraph>
                )}
                {showTrace ? <TurnTrace flow={flow} /> : null}
                {isAssistant && isPersistedMessage(item.message_id) ? (
                  <Space size={4} className="message-actions">
                    <Button
                      size="small"
                      type={item.feedback === "up" ? "primary" : "default"}
                      onClick={() =>
                        onFeedback(item.message_id, item.feedback === "up" ? null : "up")
                      }
                    >
                      👍 有用
                    </Button>
                    <Button
                      size="small"
                      danger={item.feedback === "down"}
                      onClick={() =>
                        onFeedback(item.message_id, item.feedback === "down" ? null : "down")
                      }
                    >
                      👎 待改进
                    </Button>
                  </Space>
                ) : null}
              </div>
            );
          })
        )}
        {showLiveBubble ? (
          <div className="message-bubble assistant streaming">
            <Text strong>助手</Text>
            <TurnTrace flow={liveFlow} live />
            {streamingReply ? <Markdown content={streamingReply} /> : null}
            <span className="stream-caret" />
          </div>
        ) : null}
      </div>
      {run?.pending_interaction ? (
        <InteractionCard interaction={run.pending_interaction} onDecision={onDecision} />
      ) : null}
      {recommendations.length ? (
        <Card size="small" title="推荐下一步" className="recommendation-card">
          <Space wrap>
            {recommendations.map((item) => (
              <Button key={item} onClick={() => onRecommendation(item)}>
                {item}
              </Button>
            ))}
          </Space>
        </Card>
      ) : null}
      <Divider />
      <Space.Compact block>
        <Input.TextArea
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="输入差旅需求或补充信息，Enter 发送"
        />
        <Button type="primary" loading={loading} onClick={onSend}>
          发送
        </Button>
      </Space.Compact>
      {run ? (
        <Paragraph type="secondary" className="run-meta">
          Run: {run.run_id} · Thread: {run.thread_id}
        </Paragraph>
      ) : null}
      {running ? (
        <Button danger onClick={onCancelRun}>
          停止生成
        </Button>
      ) : null}
    </Card>
  );
}

/** 只有真实落库的消息才能反馈；本地乐观消息与 SSE 派生消息在重新拉取前没有真实 ID。 */
function isPersistedMessage(messageId: string): boolean {
  return messageId.startsWith("msg_");
}

/**
 * 执行中按固定节奏逐条揭示步骤：同一批到达的步骤不会整片出现；
 * 本轮结束后立即全部展示，避免为动画延迟信息。
 */
function useRevealedSteps(total: number, live: boolean, stepMs = 200): number {
  const [revealed, setRevealed] = useState(live ? 0 : total);
  useEffect(() => {
    if (!live) {
      setRevealed(total);
      return;
    }
    if (revealed > total) {
      // 新一轮开始（步骤数回落）时重置计数。
      setRevealed(0);
      return;
    }
    if (revealed >= total) return;
    const timer = window.setTimeout(() => {
      setRevealed((value) => Math.min(value + 1, total));
    }, stepMs);
    return () => window.clearTimeout(timer);
  }, [live, total, revealed, stepMs]);
  return live ? Math.min(revealed, total) : total;
}

export type { PendingInteraction };

/** 助手消息内的执行过程与结果卡片；live 为真时展开并高亮进行中步骤。 */
function TurnTrace({ flow, live = false }: { flow: TurnFlow; live?: boolean }) {
  if (flow.steps.length === 0 && flow.cards.length === 0) return null;
  const runningStep = flow.steps.find((step) => step.status === "running");
  const title = flow.hasFailed
    ? `查看 ${flow.steps.length} 个步骤（含失败）`
    : `查看 ${flow.steps.length} 个步骤`;
  return (
    <div className="turn-trace">
      {flow.steps.length ? (
        <Collapse
          ghost
          size="small"
          defaultActiveKey={live || flow.hasRunning ? ["trace"] : []}
          items={[
            {
              key: "trace",
              label: live || flow.hasRunning
                ? `执行中：${runningStep?.label ?? "处理中"}`
                : title,
              children: (
                <Timeline
                  items={flow.steps.map((step) => ({
                    key: step.key,
                    color:
                      step.status === "failed"
                        ? "red"
                        : step.status === "running"
                          ? "blue"
                          : "green",
                    children: (
                      <>
                        <span className={`trace-step trace-step-${step.status}`}>
                          {step.label}
                        </span>
                        {step.status === "running" ? <Tag color="blue">进行中</Tag> : null}
                        <span className="trace-detail">{step.detail}</span>
                      </>
                    ),
                  }))}
                />
              ),
            },
          ]}
        />
      ) : null}
      {flow.cards.map((card, index) => (
        <ResultCard key={`${card.kind}-${index}`} card={card} />
      ))}
    </div>
  );
}
