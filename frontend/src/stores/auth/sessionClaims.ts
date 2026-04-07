export type Role = "superadmin" | "admin" | "geek" | "family" | "child" | "elder" | "guest" | "user";

export interface AuthSessionResponse {
  authenticated: boolean;
  sub?: string | null;
  username?: string | null;
  role?: string | null;
  tenant_id?: string | null;
  scopes?: string[];
  ai_route_preference?: string | null;
  exp?: number | null;
}

export interface SessionClaims {
  sub: string;
  username: string | null;
  role: string | null;
  tenant_id: string | null;
  scopes: string[];
  ai_route_preference: string;
  exp: number | null;
}

export interface AuthPayload {
  sub: string;
  username?: string;
  role?: string;
  tenant_id?: string;
  scopes?: string[];
  ai_route_preference?: string;
  exp?: number;
}

export function normalizeRole(rawRole: string | null | undefined): Role {
  const role = (rawRole ?? "user").toLowerCase();
  if (role === "superadmin") return "superadmin";
  if (role === "admin") return "admin";
  if (role === "geek") return "geek";
  if (role === "elder" || role === "family_elder") return "elder";
  if (role === "family") return "family";
  if (role === "child" || role === "family_child") return "child";
  if (role === "guest") return "guest";
  return "user";
}

function normalizeScopes(rawScopes: unknown): string[] {
  return Array.isArray(rawScopes)
    ? rawScopes.filter((scope): scope is string => typeof scope === "string" && scope.length > 0)
    : [];
}

export function extractSessionClaims(source: AuthPayload | null): SessionClaims | null {
  if (!source) {
    return null;
  }
  const rawSub = typeof source.sub === "string" ? source.sub.trim() : "";
  if (!rawSub) {
    return null;
  }
  return {
    sub: rawSub,
    username: typeof source.username === "string" ? source.username : null,
    role: typeof source.role === "string" ? source.role : null,
    tenant_id: typeof source.tenant_id === "string" ? source.tenant_id : null,
    scopes: normalizeScopes(source.scopes),
    ai_route_preference:
      typeof source.ai_route_preference === "string" && source.ai_route_preference.length > 0
        ? source.ai_route_preference
        : "auto",
    exp: typeof source.exp === "number" && Number.isFinite(source.exp) ? source.exp : null,
  };
}

export function sessionClaimsToPayload(claims: SessionClaims | null): AuthPayload | null {
  if (!claims) {
    return null;
  }
  return {
    sub: claims.sub,
    username: claims.username ?? undefined,
    role: claims.role ?? undefined,
    tenant_id: claims.tenant_id ?? undefined,
    scopes: claims.scopes,
    ai_route_preference: claims.ai_route_preference,
    exp: claims.exp ?? undefined,
  };
}

export function claimsFromSessionResponse(response: AuthSessionResponse): SessionClaims | null {
  if (!response.authenticated) {
    return null;
  }
  const sub = typeof response.sub === "string" ? response.sub : "";
  return extractSessionClaims({
    sub,
    username: response.username ?? undefined,
    role: response.role ?? undefined,
    tenant_id: response.tenant_id ?? undefined,
    scopes: response.scopes ?? [],
    ai_route_preference: response.ai_route_preference ?? undefined,
    exp: response.exp ?? undefined,
  });
}
