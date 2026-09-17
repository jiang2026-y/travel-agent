// 文件职责：只读展示当前用户的差旅单与预订记录，并在切回该页签时自动刷新。
// 定义 MyTravelPanel 组件；定义 orderStatusLabels、approvalStatusLabels、
// orderStatusColor 与 formatDateRange 等展示辅助常量与函数。
import { Alert, Button, Card, Empty, Space, Table, Tag } from "antd";
import { useCallback, useEffect, useState } from "react";
import * as travelApi from "../api/travel";
import type { BookingRecord, TravelOrder } from "../types/preferences";

interface Props {
  /** 页签每次被激活时递增，用于强制刷新，避免看到过期列表。 */
  reloadKey?: number;
}

/** 差旅单状态的中文文案。 */
const orderStatusLabels: Record<string, string> = {
  DRAFT: "草稿",
  SUBMITTED: "已提交",
  APPROVED: "已通过",
  REJECTED: "已驳回",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
};

/** 审批实例状态的中文文案。 */
const approvalStatusLabels: Record<string, string> = {
  PENDING: "审批中",
  APPROVED: "已通过",
  REJECTED: "已驳回",
  CANCELLED: "已取消",
};

/** 单据与审批状态共用的标签颜色。 */
function orderStatusColor(status: string): string {
  if (status === "APPROVED" || status === "COMPLETED") return "green";
  if (status === "REJECTED" || status === "CANCELLED") return "red";
  if (status === "SUBMITTED" || status === "PENDING") return "blue";
  return "default";
}

/** 把起止日期渲染为区间，缺项时给出占位文案。 */
function formatDateRange(departure: string | null, back: string | null): string {
  if (!departure && !back) return "—";
  return `${departure ?? "待定"} 至 ${back ?? "待定"}`;
}

export function MyTravelPanel({ reloadKey = 0 }: Props) {
  const [orders, setOrders] = useState<TravelOrder[]>([]);
  const [bookings, setBookings] = useState<BookingRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextOrders, nextBookings] = await Promise.all([
        travelApi.listTravelOrders(),
        travelApi.listBookings(),
      ]);
      setOrders(nextOrders);
      setBookings(nextBookings);
    } catch (e) {
      setError(e instanceof Error ? e.message : "差旅数据读取失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }, []);

  // 首次进入以及每次重新激活“我的差旅”页签时都重新拉取一次。
  useEffect(() => {
    void reload();
  }, [reload, reloadKey]);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error ? (
        <Alert
          closable
          showIcon
          type="error"
          message={error}
          onClose={() => setError(null)}
        />
      ) : null}
      <Card
        size="small"
        title="我的差旅单"
        extra={
          <Button size="small" loading={loading} onClick={() => void reload()}>
            刷新
          </Button>
        }
      >
        <Table
          rowKey="order_id"
          size="small"
          loading={loading}
          pagination={orders.length > 5 ? { pageSize: 5 } : false}
          dataSource={orders}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无差旅申请单，可在“对话”中直接提交申请"
              />
            ),
          }}
          columns={[
            {
              title: "单号",
              dataIndex: "order_id",
              render: (value: string) => <code>{value}</code>,
            },
            {
              title: "行程",
              key: "route",
              render: (_: unknown, row: TravelOrder) =>
                `${row.departure_city ?? "待定"} → ${row.destination ?? "待定"}`,
            },
            {
              title: "日期",
              key: "dates",
              render: (_: unknown, row: TravelOrder) =>
                formatDateRange(row.departure_date, row.return_date),
            },
            {
              title: "事由",
              dataIndex: "purpose",
              render: (value: string | null) => value ?? "—",
            },
            {
              title: "单据状态",
              dataIndex: "status",
              render: (value: string) => (
                <Tag color={orderStatusColor(value)}>{orderStatusLabels[value] ?? value}</Tag>
              ),
            },
            {
              title: "审批状态",
              dataIndex: "approval_status",
              render: (value: string | null) =>
                value ? (
                  <Tag color={orderStatusColor(value)}>
                    {approvalStatusLabels[value] ?? value}
                  </Tag>
                ) : (
                  "—"
                ),
            },
          ]}
        />
      </Card>
      <Card size="small" title="我的预订记录">
        <Table
          rowKey="booking_id"
          size="small"
          pagination={bookings.length > 5 ? { pageSize: 5 } : false}
          dataSource={bookings}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description="暂无预订记录，可在“对话”中查询航班、火车或酒店"
              />
            ),
          }}
          columns={[
            { title: "类型", dataIndex: "biz_type" },
            { title: "平台单号", dataIndex: "external_order_no" },
            { title: "状态", dataIndex: "status" },
            { title: "支付状态", dataIndex: "payment_status" },
            { title: "金额", dataIndex: "total_amount" },
            {
              title: "支付链接",
              dataIndex: "payment_url",
              render: (value: string | null) =>
                value ? (
                  <a href={value} target="_blank" rel="noreferrer">
                    去支付
                  </a>
                ) : (
                  "—"
                ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
