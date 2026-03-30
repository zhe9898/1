package io.zen70.healthgateway

import java.time.Instant

data class GatewayIdentityContext(
    val subject: String,
    val tenantId: String,
    val scopes: List<String>,
    val issuedAt: Instant,
    val expiresAt: Instant,
)
