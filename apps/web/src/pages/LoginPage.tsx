// 本文件定义企业账号密码登录页；定义 LoginPage，用于提交凭据并显示安全错误提示。

import { Alert, Button, Card, Form, Input, Space, Typography } from "antd";

import { useAuthStore } from "../stores/auth-store";
import type { LoginPayload } from "../types/auth";

const { Paragraph, Title } = Typography;

export function LoginPage() {
  // 渲染不存储令牌的企业预置账号登录表单。
  const [form] = Form.useForm<LoginPayload>();
  const { error, loading, login, clearError } = useAuthStore();

  const onFinish = async (payload: LoginPayload) => {
    // 提交登录并在每次新尝试前清除旧错误。
    clearError();
    await login(payload);
  };

  return (
    <main className="login-page">
      <Card className="login-card">
        <Space direction="vertical" size="large" className="full-width">
          <div>
            <Title level={2}>企业级智能旅行助手</Title>
            <Paragraph type="secondary">请使用企业内部预置账号登录。</Paragraph>
          </div>
          {error ? <Alert type="error" showIcon message={error} /> : null}
          <Form<LoginPayload> form={form} layout="vertical" onFinish={onFinish} requiredMark={false}>
            <Form.Item name="account" label="账号" rules={[{ required: true, message: "请输入账号" }]}>
              <Input autoComplete="username" maxLength={64} placeholder="请输入企业账号" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: "请输入密码" }]}>
              <Input.Password autoComplete="current-password" maxLength={256} placeholder="请输入密码" />
            </Form.Item>
            <Button block htmlType="submit" loading={loading} type="primary">
              登录
            </Button>
          </Form>
        </Space>
      </Card>
    </main>
  );
}
