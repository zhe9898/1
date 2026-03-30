import Foundation

public struct HealthIngestEnvelope: Sendable, Equatable {
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
}
