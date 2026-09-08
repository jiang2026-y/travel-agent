// 本文件定义受保护的旅行工作台页面；定义 WorkbenchPage，用于展示当前身份和安全退出入口。

import { Alert, Button, Card, Layout, Space, Tag, Typography } from "antd";

import { useAuthStore } from "../stores/auth-store";

const { Content, Header } = Layout;
const { Paragraph, Title } = Typography;

export function WorkbenchPage() {
  // 渲染当前用户的受保护工作台，不显示其他用户或内部 Token。
  const { loading, logout, user } = useAuthStore();

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        企业级智能旅行助手
        <Button className="logout-button" loading={loading} onClick={() => void logout()}>
          退出登录
        </Button>
      </Header>
      <Content className="app-content">
        <Card>
          <Space direction="vertical" size="middle">
            <Title level={2}>旅行工作台</Title>
            <Paragraph>当前登录账号：{user?.account}</Paragraph>
            <Space>
              <Tag color="blue">{user?.role === "admin" ? "管理员" : "普通用户"}</Tag>
              <Tag color="green">Cookie 会话</Tag>
            </Space>
            <Alert
              type="info"
              showIcon
              message="US1 已完成：账号与资源边界"
              description="会话、旅行对话与行程功能将在后续阶段逐步启用；当前不连接真实旅行供应商。"
            />
          </Space>
        </Card>
      </Content>
    </Layout>
  );
}
