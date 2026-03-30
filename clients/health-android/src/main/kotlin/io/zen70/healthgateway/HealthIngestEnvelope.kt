package io.zen70.healthgateway

import java.time.Instant

data class HealthIngestEnvelope(
    val connectorId: String,
    val tenantId: String,
    val nodeId: String,
    val sampleType: String,
    val submittedAt: Instant,
    val payloadRevision: Int,
)
