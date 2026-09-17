// 文件职责：定义偏好设置与我的差旅页面使用的类型。
// 定义 PreferenceOption、PreferenceCategory、PreferenceState、TravelOrder、BookingRecord。

export interface PreferenceOption {
  key: string;
  label: string;
  type: "single" | "multi";
  options: string[];
}

export interface PreferenceCategory {
  category: string;
  label: string;
  icon: string;
  items: PreferenceOption[];
}

export interface PreferenceState {
  available: boolean;
  memory_summary: string;
  preferences: Record<string, string[]>;
  message: string;
}

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

export interface ApprovalRecord {
  process_instance_id: string;
  order_id: string | null;
  title: string | null;
  status: string;
  remark: string | null;
  submit_time: string | null;
  update_time: string | null;
}

export interface DebugAgent {
  name: string;
  provider_key: string | null;
  enabled: boolean;
}
