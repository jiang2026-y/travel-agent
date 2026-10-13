// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件定义差旅业务在 Web 层使用的订单和预订类型。
// 定义 TravelOrder 和 BookingRecord，对应 API Server 的只读序列化字段。

export interface TravelOrder {
  order_id: string;
  destination: string | null;
  departure_city: string | null;
  departure_date: string | null;
  return_date: string | null;
  purpose: string | null;
  status: string;
  approval_id: string | null;
  approval_status: string | null;
  submitted_at: string | null;
}

export interface BookingRecord {
  booking_id: string;
  travel_order_id: string | null;
  biz_type: string;
  platform: string;
  external_order_no: string | null;
  status: string;
  external_status: string | null;
  payment_status: string | null;
  title: string | null;
  total_amount: string | null;
  payment_url: string | null;
}

