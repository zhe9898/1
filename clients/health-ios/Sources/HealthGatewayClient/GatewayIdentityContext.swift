import Foundation

public struct GatewayIdentityContext: Sendable, Equatable, Codable {
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

    // JWT claim names as emitted by the backend JWT encoder (backend/core/auth.py).
    enum CodingKeys: String, CodingKey {
        case subject = "sub"
        case tenantID = "tenant_id"
        case scopes
        case issuedAt = "iat"
        case expiresAt = "exp"
    }
}
