// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "HealthGatewayClient",
    platforms: [
        .iOS(.v17),
    ],
    products: [
        .library(
            name: "HealthGatewayClient",
            targets: ["HealthGatewayClient"]
        ),
    ],
    targets: [
        .target(
            name: "HealthGatewayClient",
            path: "Sources/HealthGatewayClient"
        ),
    ]
)
