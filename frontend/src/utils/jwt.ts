/**
 * @description JWT payload 解析（仅读取，不校验；校验由后端负责）
 */

/** JWT payload 可读字段，与后端签发契约一致 */
export interface JwtPayload {
  role?: string;
  sub?: string;
  username?: string;
  display_name?: string;
  ai_route_preference?: string;
  /** JWT 过期时间（Unix 秒） */
  exp?: number;
}

/** 解析 JWT payload（仅读取，不校验；校验由后端负责） */
export function decodePayload(token: string): JwtPayload | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    const raw: unknown = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
    return raw as JwtPayload;
  } catch {
    return null;
  }
}

/** 检查 JWT 是否已过期（含 10 秒容差） */
export function isTokenExpired(token: string): boolean {
  const payload = decodePayload(token);
  if (!payload?.exp) return false; // 无 exp 字段视为不过期（兼容旧 Token）
  const nowSec = Math.floor(Date.now() / 1000);
  return nowSec >= payload.exp - 10;
}
