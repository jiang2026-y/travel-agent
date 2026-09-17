// 文件职责：按类型渲染航班/火车/酒店查询结果卡片。
// 定义 ResultCard 组件与条目渲染辅助函数。

import { Tag } from "antd";
import type { ResultCardItem, ResultCardPayload } from "../types/conversations";

const KIND_ICONS: Record<string, string> = {
  flight: "✈️",
  train: "🚄",
  hotel: "🏨",
};

export function ResultCard({ card }: { card: ResultCardPayload }) {
  return (
    <div className={`result-card result-card-${card.kind}`}>
      <div className="result-card-head">
        <span>
          {KIND_ICONS[card.kind] ?? "📄"} {card.title}
        </span>
        <span className="result-card-count">{card.count} 条结果</span>
      </div>
      <div className="result-card-body">
        {card.items.map((item, index) => (
          <ResultCardRow key={`${item.code}-${index}`} kind={card.kind} item={item} />
        ))}
      </div>
    </div>
  );
}

function ResultCardRow({ kind, item }: { kind: string; item: ResultCardItem }) {
  const departure = formatClock(item.departureTime);
  const arrival = formatClock(item.arrivalTime);
  const departureDate = formatDate(item.departureTime);
  const arrivalDate = formatDate(item.arrivalTime);
  return (
    <div className="result-card-row">
      <div className="result-card-row-head">
        <span className="result-card-code">{item.code}</span>
        {item.transport ? <Tag color="green">{item.transport}</Tag> : null}
        {item.price ? (
          <span className="result-card-price">
            ¥{item.price}
            {item.priceLabel ?? ""}
          </span>
        ) : null}
      </div>
      {kind === "hotel" ? (
        <div className="result-card-hotel">
          {item.fromStation ? <span>{item.fromStation}</span> : null}
          {item.cabin ? <span>{item.cabin}</span> : null}
          {item.duration ? <span>{item.duration}</span> : null}
          {item.seats ? <span>{item.seats}</span> : null}
          {item.detail ? <span className="result-card-detail">{item.detail}</span> : null}
        </div>
      ) : (
        <div className="result-card-route">
          <div className="result-card-end">
            <strong>{departure || "—"}</strong>
            <span>{item.fromStation || "—"}</span>
            {departureDate ? <em>{departureDate}</em> : null}
          </div>
          <div className="result-card-mid">
            <span className="result-card-line" />
            {item.duration ? <span className="result-card-duration">{item.duration}</span> : null}
            {item.airline ? <span className="result-card-duration">{item.airline}</span> : null}
            {item.cabin ? <span className="result-card-duration">{item.cabin}</span> : null}
          </div>
          <div className="result-card-end">
            <strong>{arrival || "—"}</strong>
            <span>{item.toStation || "—"}</span>
            {arrivalDate ? <em>{arrivalDate}</em> : null}
          </div>
        </div>
      )}
    </div>
  );
}

function formatClock(value?: string): string {
  if (!value) return "";
  const match = value.match(/(\d{1,2}:\d{2})/);
  return match ? match[1] : value;
}

function formatDate(value?: string): string {
  if (!value) return "";
  const match = value.match(/(\d{4})-(\d{2})-(\d{2})/);
  return match ? `${match[2]}-${match[3]}` : "";
}
