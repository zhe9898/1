/**
 * 用户与 RBAC 相关类型，与后端 /v1/auth/users 契约对齐。
 */

export interface WebAuthnCredential {
  id: string;
  name?: string;
}

export interface UserSummary {
  id: number;
  username: string;
  display_name?: string;
  tenant_id: string;
  role: string;
  has_password: boolean;
  webauthn_credentials: WebAuthnCredential[];
}
