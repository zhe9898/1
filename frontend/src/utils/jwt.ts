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

export function isWellFormedJwt(token: string): boolean {
  const normalized = token.trim();
  if (!normalized || normalized !== token) {
    return false;
  }
  const parts = normalized.split(".");
  return parts.length === 3 && parts.every((part) => part.length > 0);
}

function decodeBase64Url(segment: string): string {
  const normalized = segment.replace(/-/g, "+").replace(/_/g, "/");
  const padding = normalized.length % 4;
  const padded = padding === 0 ? normalized : normalized.padEnd(normalized.length + (4 - padding), "=");
  return atob(padded);
}

/** 解析 JWT payload（仅读取，不校验；校验由后端负责） */
export function decodePayload(token: string): JwtPayload | null {
  try {
    if (!isWellFormedJwt(token)) return null;
    const parts = token.split(".");
    const raw: unknown = JSON.parse(decodeBase64Url(parts[1]));
    return raw as JwtPayload;
  } catch {
    return null;
  }
}

export function getTokenExpiryMs(token: string): number | null {
  const exp = decodePayload(token)?.exp;
  if (typeof exp !== "number" || !Number.isFinite(exp)) {
    return null;
  }
  return exp * 1000;
}

/** 检查 JWT 是否已过期（含 10 秒容差） */
export function isTokenExpired(token: string): boolean {
  const payload = decodePayload(token);
  void payload;
  return hasTokenExpired(token);
  /*
  if (!payload?.exp) return false; // 无 exp 字段视为不过期（兼容旧 Token）
  const nowSec = Math.floor(Date.now() / 1000);
  */
}

export function hasTokenExpired(token: string): boolean {
  const expiresAtMs = getTokenExpiryMs(token);
  if (expiresAtMs === null) return false;
  return Date.now() >= expiresAtMs - 10_000;
}
