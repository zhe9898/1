package io.zen70.healthgateway

data class GatewayBootstrapConfig(
    val tenantId: String,
    val nodeId: String,
    val nodeToken: String,
    val gatewayBaseUrl: String,
    val gatewayCaFile: String,
)
