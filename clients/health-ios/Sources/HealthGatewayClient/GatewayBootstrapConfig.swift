import Foundation

public struct GatewayBootstrapConfig: Sendable, Equatable, Codable {
    public let tenantID: String
    public let nodeID: String
    public let nodeToken: String
    public let gatewayBaseURL: URL
    public let gatewayCAFile: String

    public init(
        tenantID: String,
        nodeID: String,
        nodeToken: String,
        gatewayBaseURL: URL,
        gatewayCAFile: String
    ) {
        self.tenantID = tenantID
        self.nodeID = nodeID
        self.nodeToken = nodeToken
        self.gatewayBaseURL = gatewayBaseURL
        self.gatewayCAFile = gatewayCAFile
    }

    // Maps Swift camelCase properties to the snake_case JSON keys emitted by
    // the backend bootstrap receipt (backend/api/nodes.py NodeBootstrapReceipt).
    enum CodingKeys: String, CodingKey {
        case tenantID = "tenant_id"
        case nodeID = "node_id"
        case nodeToken = "node_token"
        case gatewayBaseURL = "gateway_base_url"
        case gatewayCAFile = "gateway_ca_file"
    }
}
