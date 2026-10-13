// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件组合聊天工作台的业务面板、会话操作和 Run 实时状态。
// 定义 WorkbenchPage，负责页面布局与业务 Hook 编排，不直接实现 SSE 或 Run 生命周期。

import { Button, Layout, Space, Tabs, Tag } from "antd";
import { useCallback, useEffect, useState } from "react";
import * as adminApi from "../../admin/api";
import { AdminPanel } from "../../admin/AdminPanel";
import type { AdminAuditEntry } from "../../admin/types";
import * as capabilityApi from "../../diagnostics/api";
import { CapabilityPanel, DiagnosticsPanel } from "../../diagnostics/DiagnosticsPanel";
import type { AgentCapability } from "../../diagnostics/types";
import { PreferencePanel } from "../../preferences/PreferencePanel";
import { MyTravelPanel } from "../../travel/MyTravelPanel";
import { useAuthStore } from "../../auth/auth-store";
import { ChatPanel } from "../components/ChatPanel";
import { ConversationSidebar } from "../components/ConversationSidebar";
import { useConversationActions } from "../hooks/useConversationActions";
import { useRunStream } from "../hooks/useRunStream";
import * as conversationApi from "../api";
import type { ConversationSummary, RunSummary } from "../types";

const { Content, Header: AppHeader, Sider } = Layout;

export function WorkbenchPage() {
  const { loading: authLoading, logout, user } = useAuthStore();
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeRun, setActiveRun] = useState<RunSummary | null>(null);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [audit, setAudit] = useState<AdminAuditEntry[]>([]);
  const [adminStatus, setAdminStatus] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<AgentCapability[]>([]);
  const [activeTab, setActiveTab] = useState("chat");
  const [travelReloadKey, setTravelReloadKey] = useState(0);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await conversationApi.listConversations());
    } catch (e) {
      setError(e instanceof Error ? e.message : "会话列表读取失败");
    }
  }, []);

  useEffect(() => {
    void refreshConversations();
    void capabilityApi.listAgentCapabilities().then(setCapabilities).catch(() => setCapabilities([]));
    if (user?.role === "admin") {
      void adminApi.getAdminStatus().then((result) => setAdminStatus(result.status)).catch(() => setAdminStatus(null));
      void adminApi.listAuditEntries().then(setAudit).catch(() => setAudit([]));
    }
  }, [refreshConversations, user?.role]);

  const { messages, setMessages, events, streamingReply, recommendations } = useRunStream({
    run: activeRun,
    setRun: setActiveRun,
    onConversationsChanged: refreshConversations,
  });

  const actions = useConversationActions({
    activeRun,
    setActiveRun,
    setMessages,
    refreshConversations,
    setError,
    setLoading,
  });

  const submitMessage = async () => {
    const value = input.trim();
    if (!value) return;
    setInput("");
    await actions.sendUserMessage(value);
  };

  const submitDecision = async (
    decision: "approve" | "reject" | "edit" | "respond",
    message?: string,
  ) => actions.submitDecision(decision, message);

  return (
    <Layout className="app-shell">
      <AppHeader className="app-header">
        <span>企业级智能旅行助手 · 验证工作台</span>
        <Space className="logout-button">
          <Tag color="blue">{user?.account}</Tag>
          <Tag color="green">{user?.role === "admin" ? "管理员" : "普通用户"}</Tag>
          <Button loading={authLoading} onClick={() => void logout()}>退出</Button>
        </Space>
      </AppHeader>
      <Layout>
        <Sider width={270} theme="light" className="conversation-sider">
          <ConversationSidebar
            conversations={conversations}
            activeConversationId={activeRun?.conversation_id ?? null}
            onSelect={(item) => void actions.selectConversation(item)}
            onCreate={actions.resetConversation}
            onRename={(item) => void actions.renameConversation(item)}
            onDelete={(item) => void actions.deleteConversation(item)}
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
                        onRecommendation={(item) => void actions.sendUserMessage(item)}
                        onCancelRun={() => activeRun && void actions.runAction(() => conversationApi.cancelRun(activeRun.run_id))}
                        onFeedback={(messageId, feedback) => void actions.sendFeedback(messageId, feedback)}
                        events={events}
                        streamingReply={streamingReply}
                      />
                    </main>
                    <aside>
                      <DiagnosticsPanel status={activeRun?.status ?? "queued"} diagnostics={activeRun?.diagnostics} />
                      <CapabilityPanel capabilities={capabilities} />
                    </aside>
                  </div>
                ),
              },
              { key: "travel", label: "我的差旅", children: <MyTravelPanel reloadKey={travelReloadKey} /> },
              { key: "preferences", label: "偏好设置", children: <PreferencePanel /> },
              ...(user?.role === "admin" ? [{
                key: "admin",
                label: "管理",
                children: <AdminPanel audit={audit} adminStatus={adminStatus} onRefreshAudit={() => void adminApi.listAuditEntries().then(setAudit)} />,
              }] : []),
            ]}
          />
        </Content>
      </Layout>
    </Layout>
  );
}






