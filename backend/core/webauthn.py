"""
ZEN70 WebAuthn 注册与认证流程（服务端）。

基于 webauthn 库：生成注册/登录选项、验证客户端凭证。
RP_ID 取自环境变量 DOMAIN（如 home.zen70.cn 或 localhost）。
"""

from __future__ import annotations

import os

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

_domain = os.getenv("DOMAIN")
if not _domain:
    raise RuntimeError("DOMAIN env var is structurally required for WebAuthn RP_ID")
RP_ID = _domain.split(":")[0]
RP_NAME = "ZEN70"


def generate_registration_challenge(
    username: str,
    display_name: str,
    user_id: bytes,
    challenge: bytes | None = None,
) -> tuple[bytes, str, str]:
    """
    生成注册选项与挑战。
    返回 (challenge_bytes, challenge_base64url, options_json_str)。
    challenge_base64url 用于 Redis 键；options_json_str 返回给前端。
    """
    options = generate_registration_options(
        rp_id=RP_ID,
        rp_name=RP_NAME,
        user_id=user_id,
        user_name=username,
        user_display_name=display_name or username,
        challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    options_json = options_to_json(options)
    challenge_b64 = bytes_to_base64url(options.challenge)
    return options.challenge, challenge_b64, options_json


def verify_registration(
    credential: dict[str, object],
    expected_challenge: bytes,
    origin: str,
) -> object:
    """
    验证注册响应。
    返回 VerifiedRegistration（含 credential_id, credential_public_key, sign_count）。
    """
    return verify_registration_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=RP_ID,
        expected_origin=origin,
        require_user_verification=True,
    )


def generate_authentication_challenge(
    allow_credentials: list[dict[str, object]],
    challenge: bytes | None = None,
) -> tuple[bytes, str, str]:
    """
    生成认证选项与挑战。
    allow_credentials: [{"id": base64url_credential_id, "type": "public-key", "transports": [...]}, ...]
    返回 (challenge_bytes, challenge_base64url, options_json_str)。
    """
    descriptors: list[PublicKeyCredentialDescriptor] = []
    for ac in allow_credentials:
        cred_id = ac.get("id")
        if isinstance(cred_id, str):
            cred_id_b = base64url_to_bytes(cred_id)
        else:
            continue
        descriptors.append(
            PublicKeyCredentialDescriptor(
                id=cred_id_b,
                transports=ac.get("transports"),
            )
        )
    options = generate_authentication_options(
        rp_id=RP_ID,
        challenge=challenge,
        allow_credentials=descriptors if descriptors else None,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    options_json = options_to_json(options)
    challenge_b64 = bytes_to_base64url(options.challenge)
    return options.challenge, challenge_b64, options_json


def verify_authentication(
    credential: dict[str, object],
    expected_challenge: bytes,
    origin: str,
    credential_public_key: bytes,
    credential_current_sign_count: int,
) -> object:
    """
    验证认证响应。
    返回 VerifiedAuthentication（含 credential_id, new_sign_count）。
    """
    return verify_authentication_response(
        credential=credential,
        expected_challenge=expected_challenge,
        expected_rp_id=RP_ID,
        expected_origin=origin,
        credential_public_key=credential_public_key,
        credential_current_sign_count=credential_current_sign_count,
        require_user_verification=True,
    )
