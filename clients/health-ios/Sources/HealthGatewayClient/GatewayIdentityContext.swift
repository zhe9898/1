import Foundation

public struct GatewayIdentityContext: Sendable, Equatable {
    public let subject: String
    public let tenantID: String
    public let scopes: [String]
    public let issuedAt: Date
    public let expiresAt: Date

    public init(
        subject: String,
        tenantID: String,
        scopes: [String],
        issuedAt: Date,
        expiresAt: Date
    ) {
        self.subject = subject
        self.tenantID = tenantID
        self.scopes = scopes
        self.issuedAt = issuedAt
        self.expiresAt = expiresAt
    }
}
