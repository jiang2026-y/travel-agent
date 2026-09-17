// 文件职责：组合工作台各面板（对话、执行进展、我的差旅、偏好设置、管理）。
// 定义 WorkbenchPage：维护会话/Run 状态、SSE 订阅与用户操作，并分发到子组件。
import { Button, Layout, Space, Tabs, Tag } from "antd";
import { useCallback, useEffect, useRef, useState } from "react";
import * as adminApi from "../api/admin";
import * as capabilityApi from "../api/capabilities";
import * as conversationApi from "../api/conversations";
import { AdminPanel } from "../components/AdminPanel";
import { CapabilityPanel, DiagnosticsPanel } from "../components/DiagnosticsPanel";
import { ChatPanel } from "../components/ChatPanel";
import { ConversationSidebar } from "../components/ConversationSidebar";
import { MyTravelPanel } from "../components/MyTravelPanel";
import { PreferencePanel } from "../components/PreferencePanel";
import { terminalStatuses } from "../lib/diagnostics";
import { useAuthStore } from "../stores/auth-store";
import type {
  AdminAuditEntry,
  AgentCapability,
  ChatMessage,
  ConversationSummary,
  PendingInteraction,
  RunEvent,
  RunSummary,
} from "../types/conversations";

const { Content, Header: AppHeader, Sider } = Layout;

/** 明确的确认/否定说法，用于把用户在待确认时的文字回复映射为结构化决定。 */
const CONFIRM_WORDS = ["确定", "确认", "好的", "好", "可以", "同意", "提交", "是", "yes", "ok"];
const REJECT_WORDS = ["取消", "不用", "不要", "拒绝", "否", "no", "不确认"];

/**
 * 判断用户文字应该转换成哪种结构化决定：
 * - 澄清类交互：任何文字都作为补充信息回复（不重复做意图识别）。
 * - 审批类交互：只有明确的确认/否定说法才映射为 approve/reject，其余仍走普通消息。
 */
function decisionForText(
  text: string,
  interaction: PendingInteraction,
): "approve" | "reject" | "respond" | null {
  if (interaction.kind === "clarification") {
    return interaction.allowed_decisions.includes("respond") ? "respond" : null;
  }
  const normalized = text.trim().toLowerCase();
  const matches = (words: string[]) => words.some((word) => normalized === word);
  if (matches(CONFIRM_WORDS) && interaction.allowed_decisions.includes("approve")) {
    return "approve";
  }
  if (matches(REJECT_WORDS) && interaction.allowed_decisions.includes("reject")) {
    return "reject";
  }
  return null;
}

export function WorkbenchPage() {
  const { loading: authLoading, logout, user } = useAuthStore();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamingReply, setStreamingReply] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AdminAuditEntry[]>([]);
  const [adminStatus, setAdminStatus] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([]);
  const [activeTab, setActiveTab] = useState("chat");
  // 每次切到“我的差旅”就递增一次，触发该面板重新拉取最新申请单与预订记录。
  const [travelReloadKey, setTravelReloadKey] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await conversationApi.listConversations());
    } catch (e) {
      setError(e instanceof Error ? e.message : "会话列表读取失败");
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void refreshConversations();
      void capabilityApi
        .listAgentCapabilities()
        .then(setCapabilities)
        .catch(() => setCapabilities([]));
      if (user?.role === "admin") {
        void adminApi
          .getAdminStatus()
          .then((r) => setAdminStatus(r.status))
          .catch(() => setAdminStatus(null));
        void adminApi
          .listAuditEntries()
          .then(setAudit)
          .catch(() => setAudit([]));
      }
    });
  }, [user?.role, refreshConversations]);

  useEffect(() => {
    abortRef.current?.abort();
    if (!activeRun) return;
    // 新一轮开始时清空上一轮事件，保证执行过程只归属当前轮次。
    setEvents([]);
    setStreamingReply("");
    const controller = new AbortController();
    abortRef.current = controller;
    let lastEventId: string | null = null;
    const seen = new Set<string>();
    let mounted = true;
    let refreshedTerminal = false;

    const poll = async () => {
      try {
        const next = await conversationApi.getRun(activeRun.run_id);
        if (mounted) setActiveRun((current) => (current ? { ...current, ...next } : next));
        // 本轮已落定（含澄清/待审批）时收起流式气泡：要么最终消息已到，要么问题在交互卡片里。
        if (mounted && next.status !== "running" && next.status !== "queued") {
          setStreamingReply("");
        }
        if (mounted && terminalStatuses.includes(next.status) && !refreshedTerminal) {
          refreshedTerminal = true;
          void refreshConversations();
          // 终态后重新拉取消息，使助手消息拿到真实 message_id（反馈按钮依赖它）。
          void conversationApi
            .listMessages(next.conversation_id)
            .then((items) => {
              if (mounted) setMessages(items);
            })
            .catch(() => undefined);
        }
      } catch {
        /* 保留上一次安全快照 */
      }
    };

    const stream = async () => {
      while (mounted && !controller.signal.aborted) {
        try {
          await conversationApi.streamRunEvents(
            activeRun.run_id,
            lastEventId,
            (event, eventId) => {
              const key = eventId ?? event.event_id ?? `${event.type}:${JSON.stringify(event.data)}`;
              if (seen.has(key)) return;
              seen.add(key);
              if (eventId) lastEventId = eventId;
              setEvents((current) => [...current, event]);
              if (event.type === "token" && typeof event.data.text === "string") {
                setStreamingReply((current) => current + String(event.data.text));
              }
              const assistantContent = event.data.content;
              if (event.type === "assistant_message" && typeof assistantContent === "string") {
                setStreamingReply("");
                setMessages((current) => [
                  ...current.filter(
                    (item) => !(item.role === "assistant" && item.run_id === activeRun.run_id),
                  ),
                  {
                    message_id: event.event_id ?? `event-${key}`,
                    run_id: activeRun.run_id,
                    role: "assistant",
                    content: assistantContent,
                    created_at: new Date().toISOString(),
                  },
                ]);
              }
              if (event.type === "recommendations" && Array.isArray(event.data.items)) {
                setRecommendations(
                  event.data.items.filter((item): item is string => typeof item === "string"),
                );
              }
              if (event.type === "conversation_title") void refreshConversations();
              if (event.type === "interrupted") {
                setRecommendations([]);
                setActiveRun((current) =>
                  current ? { ...current, status: "cancelled", pending_interaction: null } : current,
                );
              }
            },
            controller.signal,
          );
        } catch {
          if (!controller.signal.aborted) {
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
          }
        }
        await poll();
        if (terminalStatuses.includes(activeRun.status)) break;
      }
    };

    void conversationApi
      .listMessages(activeRun.conversation_id)
      .then((items) => {
        if (mounted) setMessages(items);
      })
      .catch(() => undefined);
    void poll();
    void stream();
    const interval = window.setInterval(() => void poll(), 2000);
    return () => {
      mounted = false;
      controller.abort();
      window.clearInterval(interval);
    };
    // Run 切换时重建 SSE 订阅。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeRun?.run_id]);

  const resetConversation = () => {
    abortRef.current?.abort();
    setActiveRun(null);
    setMessages([]);
    setRecommendations([]);
    setEvents([]);
    setStreamingReply("");
    setError(null);
    setInput("");
  };

  const runAction = async (action: () => Promise<RunSummary>) => {
    setLoading(true);
    setError(null);
    try {
      setActiveRun(await action());
      setRecommendations([]);
      await refreshConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setLoading(false);
    }
  };

  /** 发送一条用户消息：同一轮未结束时续跑当前 Run，已结束则在同一会话内开启新轮。 */
  const sendUserMessage = async (rawValue: string) => {
    const value = rawValue.trim();
    if (!value) return;
    const interaction = activeRun?.pending_interaction;
    if (interaction && activeRun) {
      // 有待交互时，用户输入的是「对本次提问的回答」，直接恢复图执行，
      // 不再重复做意图识别与路由（与交互卡片按钮走同一条结构化通道）。
      const decision = decisionForText(value, interaction);
      if (decision !== null) {
        setMessages((current) => [
          ...current,
          {
            message_id: `local-${Date.now()}`,
            run_id: activeRun.run_id,
            role: "user",
            content: value,
            created_at: new Date().toISOString(),
          },
        ]);
        await runAction(() =>
          conversationApi.resumeRun(activeRun.run_id, {
            kind: "decision",
            interaction_id: interaction.interaction_id,
            decision,
            message: value,
            confirmation_token: interaction.confirmation_token,
          }),
        );
        return;
      }
    }
    if (activeRun && !terminalStatuses.includes(activeRun.status)) {
      setMessages((current) => [
        ...current,
        {
          message_id: `local-${Date.now()}`,
          run_id: activeRun.run_id,
          role: "user",
          content: value,
          created_at: new Date().toISOString(),
        },
      ]);
      await runAction(() =>
        conversationApi.resumeRun(activeRun.run_id, { kind: "message", message: value }),
      );
      return;
    }
    setMessages((current) => [
      ...current,
      {
        message_id: `local-${Date.now()}`,
        run_id: activeRun?.run_id ?? null,
        role: "user",
        content: value,
        created_at: new Date().toISOString(),
      },
    ]);
    await runAction(() => conversationApi.startRun(value, activeRun?.conversation_id));
  };

  const submitMessage = async () => {
    const value = input.trim();
    if (!value) return;
    setInput("");
    await sendUserMessage(value);
  };

  const submitDecision = async (
    decision: "approve" | "reject" | "edit" | "respond",
    message?: string,
  ) => {
    if (!activeRun?.pending_interaction) return;
    const interaction = activeRun.pending_interaction;
    await runAction(() =>
      conversationApi.resumeRun(activeRun.run_id, {
        kind: "decision",
        interaction_id: interaction.interaction_id,
        decision,
        message,
        confirmation_token: interaction.confirmation_token,
      }),
    );
  };

  const selectConversation = async (conversation: ConversationSummary) => {
    try {
      const items = await conversationApi.listMessages(conversation.conversation_id);
      setMessages(items);
      const candidate = [...items].reverse().find((item) => item.run_id);
      setActiveRun(candidate?.run_id ? await conversationApi.getRun(candidate.run_id) : null);
      setActiveTab("chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "会话读取失败");
    }
  };

  const renameConversation = async (conversation: ConversationSummary) => {
    const next = window.prompt("请输入新的会话标题（最多 64 字）", conversation.title);
    if (next === null) return;
    const title = next.trim();
    if (!title || title === conversation.title) return;
    try {
      await conversationApi.renameConversation(conversation.conversation_id, title);
      await refreshConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "会话改名失败");
    }
  };

  const deleteConversation = async (conversation: ConversationSummary) => {
    if (!window.confirm("确认删除该会话？删除后不再显示在会话列表中。")) return;
    try {
      await conversationApi.deleteConversation(conversation.conversation_id);
      if (activeRun?.conversation_id === conversation.conversation_id) resetConversation();
      await refreshConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "会话删除失败");
    }
  };

  const sendFeedback = async (messageId: string, feedback: "up" | "down" | null) => {
    const conversationId = activeRun?.conversation_id;
    if (!conversationId) return;
    try {
      await conversationApi.setMessageFeedback(conversationId, messageId, feedback);
      setMessages((current) =>
        current.map((item) =>
          item.message_id === messageId ? { ...item, feedback } : item,
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "反馈提交失败");
    }
  };

  return (
    <Layout className="app-shell">
      <AppHeader className="app-header">
        <span>企业级智能旅行助手 · 验证工作台</span>
        <Space className="logout-button">
          <Tag color="blue">{user?.account}</Tag>
          <Tag color="green">{user?.role === "admin" ? "管理员" : "普通用户"}</Tag>
          <Button loading={authLoading} onClick={() => void logout()}>
            退出
          </Button>
        </Space>
      </AppHeader>
      <Layout>
        <Sider width={270} theme="light" className="conversation-sider">
          <ConversationSidebar
            conversations={conversations}
            activeConversationId={activeRun?.conversation_id ?? null}
            onSelect={(item) => void selectConversation(item)}
            onCreate={resetConversation}
            onRename={(item) => void renameConversation(item)}
            onDelete={(item) => void deleteConversation(item)}
          />
        </Sider>
        <Content className="app-content">
          <Tabs
            activeKey={activeTab}
            onChange={(key) => {
              setActiveTab(key);
              if (key === "travel") setTravelReloadKey((value) => value + 1);
            }}
            items={[
              {
                key: "chat",
                label: "对话",
                children: (
                  <div className="workbench-columns">
                    <main>
                      <ChatPanel
                        run={activeRun}
                        messages={messages}
                        recommendations={recommendations}
                        input={input}
                        loading={loading}
                        error={error}
                        onInputChange={setInput}
                        onSend={() => void submitMessage()}
                        onDismissError={() => setError(null)}
                        onDecision={submitDecision}
                        onRecommendation={(item) => void sendUserMessage(item)}
                        onCancelRun={() =>
                          activeRun &&
                          void runAction(() => conversationApi.cancelRun(activeRun.run_id))
                        }
                        onFeedback={(messageId, feedback) =>
                          void sendFeedback(messageId, feedback)
                        }
                        events={events}
                        streamingReply={streamingReply}
                      />
                    </main>
                    <aside>
                      <DiagnosticsPanel
                        status={activeRun?.status ?? "queued"}
                        diagnostics={activeRun?.diagnostics}
                      />
                      <CapabilityPanel capabilities={capabilities} />
                    </aside>
                  </div>
                ),
              },
              {
                key: "travel",
                label: "我的差旅",
                children: <MyTravelPanel reloadKey={travelReloadKey} />,
              },
              { key: "preferences", label: "偏好设置", children: <PreferencePanel /> },
              ...(user?.role === "admin"
                ? [
                    {
                      key: "admin",
                      label: "管理",
                      children: (
                        <AdminPanel
                          audit={audit}
                          adminStatus={adminStatus}
                          onRefreshAudit={() =>
                            void adminApi.listAuditEntries().then(setAudit)
                          }
                        />
                      ),
                    },
                  ]
                : []),
            ]}
          />
        </Content>
      </Layout>
    </Layout>
  );
}
