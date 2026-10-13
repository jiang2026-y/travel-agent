// 本文件提供浏览器端 CSRF Cookie 读取和请求头构造。
// 定义 readCookie，用于读取非 HttpOnly Cookie；定义 csrfHeaders，用于写请求携带 CSRF Token。

export function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const item = document.cookie.split("; ").find((value) => value.startsWith(prefix));
  return item ? decodeURIComponent(item.slice(prefix.length)) : null;
}

export function csrfHeaders(): Record<string, string> {
  const cookieName = import.meta.env.VITE_CSRF_COOKIE_NAME ?? "travel_agent_csrf";
  const headerName = import.meta.env.VITE_CSRF_HEADER_NAME ?? "X-CSRF-Token";
  const token = readCookie(cookieName);
  return token ? { [headerName]: token } : {};
}
