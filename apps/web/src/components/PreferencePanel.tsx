// 文件职责：展示并保存用户差旅偏好，偏好统一存于百炼长期记忆。
// 定义 PreferencePanel 组件，支持单选/多选勾选、保存与记忆原文查看。

import { Alert, Button, Card, Checkbox, Collapse, Empty, Radio, Space, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import * as preferenceApi from "../api/preferences";
import type { PreferenceCategory } from "../types/preferences";

const { Paragraph, Text, Title } = Typography;

export function PreferencePanel() {
  const [categories, setCategories] = useState<PreferenceCategory[]>([]);
  const [selected, setSelected] = useState<Record<string, string[]>>({});
  const [memorySummary, setMemorySummary] = useState("");
  const [available, setAvailable] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [options, current] = await Promise.all([
        preferenceApi.listPreferenceOptions(),
        preferenceApi.getPreferences(),
      ]);
      setCategories(options);
      setSelected(current.preferences ?? {});
      setMemorySummary(current.memory_summary ?? "");
      setAvailable(current.available);
    } catch (e) {
      setError(e instanceof Error ? e.message : "偏好读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const updateSingle = (key: string, value: string) =>
    setSelected((current) => ({ ...current, [key]: [value] }));
  const updateMulti = (key: string, values: string[]) =>
    setSelected((current) => ({ ...current, [key]: values }));

  const save = async () => {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const result = await preferenceApi.savePreferences(selected);
      setMessage(
        result.saved
          ? `已保存 ${result.count} 项偏好，Agent 会在后续对话中优先参考。`
          : result.message || "长期记忆未启用，本次偏好未保存。",
      );
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "偏好保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error ? <Alert closable showIcon type="error" message={error} onClose={() => setError(null)} /> : null}
      {message ? <Alert showIcon type="success" message={message} /> : null}
      {!available ? (
        <Alert
          showIcon
          type="warning"
          message="长期记忆未启用，偏好暂时无法保存或回显。"
        />
      ) : null}
      <Card
        size="small"
        title="差旅偏好设置"
        extra={
          <Space>
            <Button size="small" loading={loading} onClick={() => void reload()}>
              重新读取
            </Button>
            <Button size="small" type="primary" loading={saving} onClick={() => void save()}>
              保存偏好
            </Button>
          </Space>
        }
      >
        {categories.length === 0 ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="偏好目录不可用" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            {categories.map((category) => (
              <Card
                key={category.category}
                size="small"
                type="inner"
                title={`${category.icon} ${category.label}`}
              >
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  {category.items.map((item) => (
                    <div key={item.key} className="preference-row">
                      <Text strong>{item.label}</Text>
                      {item.type === "single" ? (
                        <Radio.Group
                          value={selected[item.key]?.[0]}
                          onChange={(e) => updateSingle(item.key, e.target.value as string)}
                        >
                          <Space wrap>
                            {item.options.map((option) => (
                              <Radio key={option} value={option}>
                                {option}
                              </Radio>
                            ))}
                          </Space>
                        </Radio.Group>
                      ) : (
                        <Checkbox.Group
                          value={selected[item.key] ?? []}
                          onChange={(values) => updateMulti(item.key, values as string[])}
                        >
                          <Space wrap>
                            {item.options.map((option) => (
                              <Checkbox key={option} value={option}>
                                {option}
                              </Checkbox>
                            ))}
                          </Space>
                        </Checkbox.Group>
                      )}
                    </div>
                  ))}
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </Card>
      {memorySummary ? (
        <Collapse
          ghost
          items={[
            {
              key: "memory",
              label: "长期记忆原文",
              children: <Paragraph>{memorySummary}</Paragraph>,
            },
          ]}
        />
      ) : (
        <Title level={5} type="secondary">
          暂无已保存的偏好记忆
        </Title>
      )}
    </Space>
  );
}
