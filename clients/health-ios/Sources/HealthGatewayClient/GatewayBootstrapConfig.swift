import Foundation

public struct GatewayBootstrapConfig: Sendable, Equatable {
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
}
