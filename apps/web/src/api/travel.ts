// 文件职责：封装"我的差旅"页面读取差旅单与预订记录的请求。
// 定义 listTravelOrders 与 listBookings，均为只读查询。

import type { BookingRecord, TravelOrder } from "../types/preferences";

const apiBaseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

interface ErrorResponse {
  error?: { message?: string };
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as ErrorResponse;
    throw new Error(body.error?.message ?? "请求暂时无法完成，请稍后重试");
  }
  return (await response.json()) as T;
}

export async function listTravelOrders(): Promise<TravelOrder[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/travel-orders`, {
    credentials: "include",
  });
  return ((await readJson<{ orders: TravelOrder[] }>(response)).orders ?? []);
}

export async function listBookings(): Promise<BookingRecord[]> {
  const response = await fetch(`${apiBaseUrl}/api/v1/bookings`, {
    credentials: "include",
  });
  return ((await readJson<{ bookings: BookingRecord[] }>(response)).bookings ?? []);
}
