/**
 * @description WebAuthn (FIDO2) 前端鉴权服务打样
 *
 * 符合法典 6.1.4: 采用 WebAuthn 协议，结合设备芯片实现生物特征识别
 */

import { logError } from "@/utils/logger";

/**
 * 校验浏览器是否支持 WebAuthn
 */
export const isWebAuthnSupported = (): boolean => {
  return (
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof window.PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable === "function"
  );
};

/**
 * 发起 WebAuthn 注册流程 (打样)
 * 实际调用需后端提供 publicKeyCredentialCreationOptions
 */
export const registerWebAuthn = async (creationOptions: PublicKeyCredentialCreationOptions): Promise<Credential | null> => {
  if (!isWebAuthnSupported()) {
    throw new Error("当前设备/浏览器不支持 WebAuthn");
  }

  try {
    const credential = await navigator.credentials.create({
      publicKey: creationOptions,
    });
    return credential;
  } catch (error: unknown) {
    logError("WebAuthn 注册失败", error);
    throw error;
  }
};

/**
 * 发起 WebAuthn 登录验证流程 (打样)
 * 实际调用需后端提供 publicKeyCredentialRequestOptions
 */
export const authenticateWebAuthn = async (requestOptions: PublicKeyCredentialRequestOptions): Promise<Credential | null> => {
  if (!isWebAuthnSupported()) {
    throw new Error("当前设备/浏览器不支持 WebAuthn");
  }

  try {
    const assertion = await navigator.credentials.get({
      publicKey: requestOptions,
    });
    return assertion;
  } catch (error: unknown) {
    logError("WebAuthn 验证失败", error);
    throw error;
  }
};
