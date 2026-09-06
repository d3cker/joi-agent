// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "VoiceAgent",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "VoiceAgentCore", targets: ["VoiceAgentCore"]),
        .executable(name: "VoiceAgent", targets: ["VoiceAgent"]),
    ],
    targets: [
        .target(name: "VoiceAgentCore"),
        .executableTarget(
            name: "VoiceAgent",
            dependencies: ["VoiceAgentCore"]
        ),
        .testTarget(
            name: "VoiceAgentCoreTests",
            dependencies: ["VoiceAgentCore"]
        ),
    ]
)
