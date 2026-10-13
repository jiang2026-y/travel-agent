// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装会话和 Run 的用户操作。
// 定义 useConversationActions，负责创建、恢复、取消 Run，会话选择、重命名、删除和消息反馈。

import { useCallback, type Dispatch, type SetStateAction } from "react";
import * as chatApi from "../api";
import type {
  ChatMessage,
  ConversationSummary,
  PendingInteraction,
  ResumeRunPayload,
  RunCreateResponse,
  RunSummary,
} from "../types";

interface Options {
  activeRun: RunSummary | null;
  setActiveRun: (run: RunSummary | null) => void;
  setMessages: Dispatch<SetStateAction<ChatMessage[]>>;
  refreshConversations: () => Promise<void>;
  setError: (message: string | null) => void;
  setLoading: (loading: boolean) => void;
}

const CONFIRM_WORDS = ["确定", "确认", "好的", "好", "可以", "同意", "提交", "是", "yes", "ok"];
const REJECT_WORDS = ["取消", "不用", "不要", "拒绝", "否", "no", "不确认"];

export function useConversationActions({
  activeRun,
  setActiveRun,
  setMessages,
  refreshConversations,
  setError,
  setLoading,
}: Options) {
  const runAction = useCallback(async (action: () => Promise<RunSummary | RunCreateResponse>) => {
    setLoading(true);
    setError(null);
    try {
      const next = await action();
      setActiveRun(toRunSummary(next));
      await refreshConversations();
    } catch (error) {
      setError(error instanceof Error ? error.message : "操作失败");
    } finally {
      setLoading(false);
    }
  }, [refreshConversations, setActiveRun, setError, setLoading]);

  const resetConversation = useCallback(() => {
    setActiveRun(null);
    setMessages([]);
    setError(null);
  }, [setActiveRun, setError, setMessages]);

  const decisionForText = useCallback((text: string, interaction: PendingInteraction) => {
    if (interaction.kind === "clarification") {
      return interaction.allowed_decisions.includes("respond") ? "respond" as const : null;
    }
    const normalized = text.trim().toLowerCase();
    if (CONFIRM_WORDS.includes(normalized) && interaction.allowed_decisions.includes("approve")) return "approve" as const;
    if (REJECT_WORDS.includes(normalized) && interaction.allowed_decisions.includes("reject")) return "reject" as const;
    return null;
  }, []);

  const sendUserMessage = useCallback(async (rawValue: string) => {
    const value = rawValue.trim();
    if (!value) return;
    const interaction = activeRun?.pending_interaction;
    if (interaction && activeRun) {
      const decision = decisionForText(value, interaction);
      if (decision) {
        setMessages((current) => [...current, localUserMessage(activeRun.run_id, value)]);
        await runAction(() => chatApi.resumeRun(activeRun.run_id, {
          kind: "decision",
          interaction_id: interaction.interaction_id,
          decision,
          message: value,
          confirmation_token: interaction.confirmation_token,
        }));
        return;
      }
    }
    if (activeRun && !["completed", "failed", "cancelled"].includes(activeRun.status)) {
      setMessages((current) => [...current, localUserMessage(activeRun.run_id, value)]);
      await runAction(() => chatApi.resumeRun(activeRun.run_id, { kind: "message", message: value }));
      return;
    }
    setMessages((current) => [...current, localUserMessage(activeRun?.run_id ?? null, value)]);
    await runAction(() => chatApi.startRun(value, activeRun?.conversation_id));
  }, [activeRun, decisionForText, runAction, setMessages]);

  const submitDecision = useCallback(async (decision: "approve" | "reject" | "edit" | "respond", message?: string, editedArgs?: Record<string, unknown>) => {
    if (!activeRun?.pending_interaction) return;
    const interaction = activeRun.pending_interaction;
    const payload: ResumeRunPayload = {
      kind: "decision",
      interaction_id: interaction.interaction_id,
      decision,
      message,
      confirmation_token: interaction.confirmation_token,
      edited_args: editedArgs,
    };
    await runAction(() => chatApi.resumeRun(activeRun.run_id, payload));
  }, [activeRun, runAction]);

  const selectConversation = useCallback(async (conversation: ConversationSummary) => {
    try {
      const items = await chatApi.listMessages(conversation.conversation_id);
      setMessages(items);
      const candidate = [...items].reverse().find((item) => item.run_id);
      setActiveRun(candidate?.run_id ? await chatApi.getRun(candidate.run_id) : null);
    } catch (error) {
      setError(error instanceof Error ? error.message : "会话读取失败");
    }
  }, [setActiveRun, setError, setMessages]);

  const renameConversation = useCallback(async (conversation: ConversationSummary) => {
    const next = window.prompt("请输入新的会话标题（最多 64 字）", conversation.title);
    if (next === null) return;
    const title = next.trim();
    if (!title || title === conversation.title) return;
    try {
      await chatApi.renameConversation(conversation.conversation_id, title);
      await refreshConversations();
    } catch (error) {
      setError(error instanceof Error ? error.message : "会话改名失败");
    }
  }, [refreshConversations, setError]);

  const deleteConversation = useCallback(async (conversation: ConversationSummary) => {
    if (!window.confirm("确认删除该会话？删除后不再显示在会话列表中。")) return;
    try {
      await chatApi.deleteConversation(conversation.conversation_id);
      if (activeRun?.conversation_id === conversation.conversation_id) resetConversation();
      await refreshConversations();
    } catch (error) {
      setError(error instanceof Error ? error.message : "会话删除失败");
    }
  }, [activeRun?.conversation_id, refreshConversations, resetConversation, setError]);

  const sendFeedback = useCallback(async (messageId: string, feedback: "up" | "down" | null) => {
    if (!activeRun?.conversation_id) return;
    try {
      await chatApi.setMessageFeedback(activeRun.conversation_id, messageId, feedback);
      setMessages((current) => current.map((item) => item.message_id === messageId ? { ...item, feedback } : item));
    } catch (error) {
      setError(error instanceof Error ? error.message : "反馈提交失败");
    }
  }, [activeRun?.conversation_id, setError, setMessages]);

  return { runAction, resetConversation, sendUserMessage, submitDecision, selectConversation, renameConversation, deleteConversation, sendFeedback };
}

function localUserMessage(runId: string | null, content: string): ChatMessage {
  return { message_id: `local-${Date.now()}`, run_id: runId, role: "user", content, created_at: new Date().toISOString() };
}

function toRunSummary(value: RunSummary | RunCreateResponse): RunSummary {
  return {
    ...value,
    pending_interaction: "pending_interaction" in value ? value.pending_interaction : null,
    diagnostics: "diagnostics" in value ? value.diagnostics : undefined,
  };
}

