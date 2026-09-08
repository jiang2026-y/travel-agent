// 本文件提供前端测试共享常量。
// 导出 testCorrelationHeaders，用于模拟 API 调用关联标识；导出 testSession，用于模拟前端会话状态。

export const testCorrelationHeaders = {
  'X-Trace-Id': 'trace_test_001',
  'X-Request-Id': 'request_test_001',
  'X-Run-Id': 'run_test_001',
  'X-Thread-Id': 'thread_test_001',
} as const

export const testSession = {
  userId: 'user_001',
  role: 'user',
} as const
