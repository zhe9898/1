package io.zen70.healthgateway

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Bootstrap configuration received from the gateway bootstrap receipt endpoint.
 *
 * Serializes to/from the JSON contract emitted by backend/api/nodes.py
 * (NodeBootstrapReceipt). Field names match the backend snake_case convention.
 *
 * Security: [nodeToken] must be stored in the Android Keystore
 * (via EncryptedSharedPreferences or BiometricPrompt-wrapped keystore entry),
 * never in plain SharedPreferences or disk. [gatewayCaFile] must be loaded
 * as a custom X509TrustManager in OkHttpClient to enforce certificate pinning
 * — see GatewayHttpClient for the TLS pinning implementation.
 */
@Serializable
data class GatewayBootstrapConfig(
    @SerialName("tenant_id") val tenantId: String,
    @SerialName("node_id") val nodeId: String,
    @SerialName("node_token") val nodeToken: String,
    @SerialName("gateway_base_url") val gatewayBaseUrl: String,
    @SerialName("gateway_ca_file") val gatewayCaFile: String,
)
