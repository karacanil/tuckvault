// swift-tools-version:5.9
// Tests the iOS storage-format code on a Mac without Xcode projects or a simulator:
//
//     cp ../ios/Vault.swift Sources/Vault/ && swift test
//
// Vault.swift is copied in (not moved here) because the app builds it from ../ios.
import PackageDescription

let package = Package(
  name: "VaultTests",
  platforms: [.macOS(.v12)],
  targets: [
    .target(name: "Vault", path: "Sources/Vault"),
    .testTarget(name: "VaultTests", dependencies: ["Vault"], path: "Tests/VaultTests")
  ]
)
