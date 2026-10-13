// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装差旅偏好目录、读取和保存接口。
// 定义 listPreferenceOptions、getPreferences 和 savePreferences，对应 API Server 偏好路由。

import { requestJson } from "../../shared/api/client";
import type { PreferenceCategory, PreferenceState } from "./types";

export async function listPreferenceOptions(): Promise<PreferenceCategory[]> {
  const body = await requestJson<{ categories: PreferenceCategory[] }>(
    "/api/v1/preferences/options",
  );
  return body.categories ?? [];
}

export async function getPreferences(): Promise<PreferenceState> {
  return requestJson<PreferenceState>("/api/v1/preferences");
}

export async function savePreferences(
  preferences: Record<string, string[]>,
): Promise<{ saved: boolean; count: number; message: string }> {
  return requestJson<{ saved: boolean; count: number; message: string }>(
    "/api/v1/preferences",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ preferences }),
    },
  );
}

