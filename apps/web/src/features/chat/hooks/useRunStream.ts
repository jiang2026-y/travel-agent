// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件管理聊天 Run 的消息加载、SSE 订阅、轮询和断线恢复。
// 定义 useRunStream，负责 AbortController、Last-Event-ID、事件去重和终态刷新。

import { useEffect, useRef, useState } from "react";
import * as chatApi from "../api";
import { terminalStatuses } from "../../diagnostics/diagnostics";
import type { ChatMessage, RunEvent, RunSummary } from "../types";

interface Options {
  run: RunSummary | null;
  setRun: (run: RunSummary) => void;
  onConversationsChanged: () => void;
}

export function useRunStream({ run, setRun, onConversationsChanged }: Options) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [streamingReply, setStreamingReply] = useState("");
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    abortRef.current?.abort();
    if (!run) {
      setMessages([]);
      setEvents([]);
      setStreamingReply("");
      setRecommendations([]);
      return;
    }

    const runId = run.run_id;
    const conversationId = run.conversation_id;
    const controller = new AbortController();
    abortRef.current = controller;
    let mounted = true;
    let lastEventId: string | null = null;
    let refreshedTerminal = false;
    const latestStatus = { current: "queued" as RunSummary["status"] };
    const seen = new Set<string>();

    setEvents([]);
    setStreamingReply("");
    setRecommendations([]);

    const poll = async (): Promise<RunSummary | null> => {
      try {
        const next = await chatApi.getRun(runId);
        if (!mounted) return next;
        latestStatus.current = next.status;
        setRun(next);
        if (next.status !== "running" && next.status !== "queued") setStreamingReply("");
        if (terminalStatuses.includes(next.status) && !refreshedTerminal) {
          refreshedTerminal = true;
          onConversationsChanged();
          void chatApi.listMessages(conversationId).then((items) => {
            if (mounted) setMessages(items);
          });
          controller.abort();
        }
        return next;
      } catch {
        return null;
      }
    };

    const stream = async () => {
      while (mounted && !controller.signal.aborted) {
        try {
          await chatApi.streamRunEvents(
            runId,
            lastEventId,
            (event, eventId) => {
              const key = eventId ?? event.event_id ?? `${event.type}:${JSON.stringify(event.data)}`;
              if (seen.has(key)) return;
              seen.add(key);
              if (eventId) lastEventId = eventId;
              setEvents((current) => [...current, event]);
              if (event.type === "token" && typeof event.data.text === "string") {
                setStreamingReply((current) => current + event.data.text);
              }
              if (event.type === "assistant_message" && typeof event.data.content === "string") {
                setStreamingReply("");
                setMessages((current) => [
                  ...current.filter((item) => !(item.role === "assistant" && item.run_id === runId)),
                  {
                    message_id: event.event_id ?? `event-${key}`,
                    run_id: runId,
                    role: "assistant",
                    content: event.data.content,
                    created_at: new Date().toISOString(),
                  },
                ]);
              }
              if (event.type === "recommendations" && Array.isArray(event.data.items)) {
                setRecommendations(event.data.items.filter((item): item is string => typeof item === "string"));
              }
              if (event.type === "interrupted") setRecommendations([]);
              if (event.type === "conversation_title") onConversationsChanged();
            },
            controller.signal,
          );
        } catch {
          if (!controller.signal.aborted) {
            await new Promise((resolve) => window.setTimeout(resolve, 1000));
          }
        }
        await poll();
        if (terminalStatuses.includes(latestStatus.current)) break;
      }
    };

    void chatApi.listMessages(conversationId).then((items) => {
      if (mounted) setMessages(items);
    });
    void poll();
    void stream();
    const interval = window.setInterval(() => void poll(), 2000);

    return () => {
      mounted = false;
      controller.abort();
      window.clearInterval(interval);
      if (abortRef.current === controller) abortRef.current = null;
    };
  }, [run?.run_id, run?.conversation_id, onConversationsChanged, setRun]);

  return { messages, setMessages, events, streamingReply, recommendations };
}
