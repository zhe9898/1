import Foundation

public struct HealthIngestEnvelope: Sendable, Equatable, Codable {
    public let connectorID: String
    public let tenantID: String
    public let nodeID: String
    public let sampleType: String
    public let submittedAt: Date
    public let payloadRevision: Int

    public init(
        connectorID: String,
        tenantID: String,
        nodeID: String,
        sampleType: String,
        submittedAt: Date,
        payloadRevision: Int
    ) {
        self.connectorID = connectorID
        self.tenantID = tenantID
        self.nodeID = nodeID
        self.sampleType = sampleType
        self.submittedAt = submittedAt
        self.payloadRevision = payloadRevision
    }

    // Maps to the ingest envelope JSON contract (backend/api/health.py HealthIngestPayload).
    enum CodingKeys: String, CodingKey {
        case connectorID = "connector_id"
        case tenantID = "tenant_id"
        case nodeID = "node_id"
        case sampleType = "sample_type"
        case submittedAt = "submitted_at"
        case payloadRevision = "payload_revision"
    }
}
