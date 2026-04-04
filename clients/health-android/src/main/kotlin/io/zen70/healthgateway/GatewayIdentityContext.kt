package io.zen70.healthgateway

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * JWT identity context decoded from the gateway-issued access token.
 *
 * Claim names match the backend JWT encoder (backend/core/auth.py):
 * sub, tenant_id, scopes, iat, exp.
 *
 * [expiresAt] is a Unix epoch second (Long) to match the JWT `exp` claim type.
 * Callers must check [expiresAt] before using the context and trigger token
 * refresh via the gateway /v1/auth/refresh endpoint when within 60 seconds
 * of expiry.
 */
@Serializable
data class GatewayIdentityContext(
    @SerialName("sub") val subject: String,
    @SerialName("tenant_id") val tenantId: String,
    @SerialName("scopes") val scopes: List<String>,
    @SerialName("iat") val issuedAt: Long,
    @SerialName("exp") val expiresAt: Long,
)
