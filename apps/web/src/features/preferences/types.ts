// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件定义差旅偏好业务的目录和保存结果类型。
// 定义 PreferenceOption、PreferenceCategory、PreferenceState，严格对应偏好 API 返回结构。

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

