package io.zen70.healthgateway

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Ingest envelope submitted to the gateway health ingest endpoint.
 *
 * Field names match the backend ingest contract:
 * backend/api/health.py HealthIngestPayload.
 *
 * [submittedAt] is a Unix epoch second (Long). The backend validates that
 * it falls within ±5 minutes of server time to prevent replay attacks.
 */
@Serializable
data class HealthIngestEnvelope(
    @SerialName("connector_id") val connectorId: String,
    @SerialName("tenant_id") val tenantId: String,
    @SerialName("node_id") val nodeId: String,
    @SerialName("sample_type") val sampleType: String,
    @SerialName("submitted_at") val submittedAt: Long,
    @SerialName("payload_revision") val payloadRevision: Int,
)
