// 文件职责：展示历史会话列表，并提供新建、选中、改名与删除入口。
// 定义 ConversationSidebar 组件。

import { Button, List, Space, Typography } from "antd";
import type { ConversationSummary } from "../types/conversations";

const { Text } = Typography;

interface Props {
  conversations: ConversationSummary[];
  activeConversationId: string | null;
  onSelect: (conversation: ConversationSummary) => void;
  onCreate: () => void;
  onRename: (conversation: ConversationSummary) => void;
  onDelete: (conversation: ConversationSummary) => void;
}

export function ConversationSidebar({
  conversations,
  activeConversationId,
  onSelect,
  onCreate,
  onRename,
  onDelete,
}: Props) {
  return (
    <div className="conversation-sidebar">
      <div className="sider-title">
        历史会话
        <Button size="small" type="primary" onClick={onCreate}>
          新建对话
        </Button>
      </div>
      <List
        size="small"
        dataSource={conversations}
        locale={{ emptyText: "暂无会话" }}
        renderItem={(item) => (
          <List.Item
            className={
              item.conversation_id === activeConversationId
                ? "conversation-item active"
                : "conversation-item"
            }
            onClick={() => onSelect(item)}
          >
            <List.Item.Meta
              title={item.title || "新对话"}
              description={new Date(item.updated_at).toLocaleString()}
            />
            <Space size={4}>
              <Button
                size="small"
                type="link"
                onClick={(e) => {
                  e.stopPropagation();
                  onRename(item);
                }}
              >
                改名
              </Button>
              <Button
                size="small"
                type="link"
                danger
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete(item);
                }}
              >
                删除
              </Button>
            </Space>
            <Text type="secondary" className="conversation-id">
              {item.conversation_id.slice(-6)}
            </Text>
          </List.Item>
        )}
      />
    </div>
  );
}
