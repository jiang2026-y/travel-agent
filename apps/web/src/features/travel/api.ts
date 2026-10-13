// 文件职责：业务模块，聚集本文件中的提供的函数或类型定义。
// 本文件封装“我的差旅”业务的只读接口。
// 定义 listTravelOrders 和 listBookings，对应 API Server 的差旅单与预订记录查询。

import { requestJson } from "../../shared/api/client";
import type { BookingRecord, TravelOrder } from "./types";

export async function listTravelOrders(): Promise<TravelOrder[]> {
  const body = await requestJson<{ orders: TravelOrder[] }>("/api/v1/travel-orders");
  return body.orders ?? [];
}

export async function listBookings(): Promise<BookingRecord[]> {
  const body = await requestJson<{ bookings: BookingRecord[] }>("/api/v1/bookings");
  return body.bookings ?? [];
}

