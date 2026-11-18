import Foundation
import XCTest

@testable import Vault

/// Checks the iOS code against bytes produced by the PC program (tools/make_mobile_vectors.py).
/// The same vectors are used by the Android and the TypeScript tests.
final class VaultTests: XCTestCase {
  private var vectors: [String: Any] = [:]
  private var master = Data()

  override func setUpWithError() throws {
    let file = URL(fileURLWithPath: #filePath)
      .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
      .deletingLastPathComponent().appendingPathComponent("test-vectors.json")
    let parsed = try JSONSerialization.jsonObject(with: Data(contentsOf: file))
    vectors = try XCTUnwrap(parsed as? [String: Any])
    master = try XCTUnwrap(Vault.unhex(try text("master")))
  }

  private func text(_ key: String, in dict: [String: Any]? = nil) throws -> String {
    try XCTUnwrap((dict ?? vectors)[key] as? String, key)
  }

  private func chunks() throws -> [[String: Any]] {
    try XCTUnwrap(vectors["chunks"] as? [[String: Any]])
  }

  func testKeyCheckAndRecoveryKeyMatchThePc() throws {
    XCTAssertEqual(Vault.keyCheck(master), try text("key_check"))
    XCTAssertEqual(Vault.encodeRecoveryKey(master), try text("recovery_key"))
    XCTAssertEqual(try Vault.decodeRecoveryKey(try text("recovery_key")), master)
    let sloppy = try text("recovery_key").lowercased().replacingOccurrences(of: "-", with: " ")
    XCTAssertEqual(try Vault.decodeRecoveryKey(sloppy), master)
    let bad = try XCTUnwrap(vectors["bad_recovery_keys"] as? [String: String])
    for (name, key) in bad {
      XCTAssertThrowsError(try Vault.decodeRecoveryKey(key), name) { error in
        XCTAssertFalse(((error as? RecoveryKeyError)?.message ?? "").isEmpty)
      }
    }
  }

  func testOpensEveryBlobThePcWrote() throws {
    for chunk in try chunks() {
      let label = try text("label", in: chunk)
      let blob = try XCTUnwrap(Vault.unhex(try text("blob", in: chunk)))
      let data = try Vault.openChunk(master: master, name: try text("name", in: chunk), blob: blob)
      XCTAssertEqual(Vault.hex(data), try text("data", in: chunk), label)
    }
  }

  func testWritesTheSameBlobsAsThePc() throws {
    for chunk in try chunks() {
      let label = try text("label", in: chunk)
      let data = try XCTUnwrap(Vault.unhex(try text("data", in: chunk)))
      let compressed = try XCTUnwrap(chunk["compressed"] as? Bool)
      let sealed = try Vault.sealChunk(master: master, data: data)
      XCTAssertEqual(sealed.blob[4] == 1, compressed, label)
      if !compressed {  // compressed bytes may differ between zlib builds
        XCTAssertEqual(sealed.name, try text("name", in: chunk), label)
        XCTAssertEqual(Vault.hex(sealed.blob), try text("blob", in: chunk), label)
      }
      XCTAssertEqual(try Vault.openChunk(master: master, name: sealed.name, blob: sealed.blob), data, label)
      // identical content gives the identical blob (that is what makes later backups small)
      XCTAssertEqual(try Vault.sealChunk(master: master, data: data).name, sealed.name, label)
    }
  }

  func testBlobsPassedAsSlicesWork() throws {
    let chunk = try chunks()[1]
    let blob = try XCTUnwrap(Vault.unhex(try text("blob", in: chunk)))
    var padded = Data([1, 2, 3])
    padded.append(blob)
    let slice = padded.dropFirst(3)  // indices do not start at zero
    XCTAssertEqual(Vault.hex(try Vault.openChunk(master: master, name: try text("name", in: chunk), blob: slice)),
                   try text("data", in: chunk))
  }

  func testSnapshotsReadAndWriteLikeThePc() throws {
    let snap = try XCTUnwrap(vectors["snapshot"] as? [String: Any])
    let json = try text("json", in: snap)
    let raw = try XCTUnwrap(Vault.unhex(try text("raw", in: snap)))
    XCTAssertEqual(String(decoding: try Vault.openSnapshot(master: master, raw: raw), as: UTF8.self), json)
    let mine = try Vault.sealSnapshot(master: master, json: Data(json.utf8))
    XCTAssertEqual(String(decoding: try Vault.openSnapshot(master: master, raw: mine), as: UTF8.self), json)
    XCTAssertNotEqual(mine, try Vault.sealSnapshot(master: master, json: Data(json.utf8)))
  }

  func testLargeCompressibleDataRoundTrips() throws {
    var data = Data()
    for i in 0..<200_000 { data.append(contentsOf: Array("line \(i % 977)\n".utf8)) }
    let sealed = try Vault.sealChunk(master: master, data: data)
    XCTAssertEqual(sealed.blob[4], 1)
    XCTAssertLessThan(sealed.blob.count, data.count / 2)
    XCTAssertEqual(try Vault.openChunk(master: master, name: sealed.name, blob: sealed.blob), data)
  }

  func testDamageAndWrongKeysAreNoticed() throws {
    let chunk = try chunks()[1]
    let name = try text("name", in: chunk)
    let blob = try XCTUnwrap(Vault.unhex(try text("blob", in: chunk)))
    var flipped = blob
    flipped[flipped.count - 1] ^= 1
    let other = Data(repeating: 7, count: 32)
    let snap = try XCTUnwrap(Vault.unhex(try text("raw", in: try XCTUnwrap(vectors["snapshot"] as? [String: Any]))))
    let attempts: [() throws -> Data] = [
      { try Vault.openChunk(master: self.master, name: name, blob: flipped) },  // does not match its name
      { try Vault.openChunk(master: self.master, name: Vault.hex(Vault.sha256(flipped)), blob: flipped) },  // renamed to match
      { try Vault.openChunk(master: other, name: name, blob: blob) },  // another key
      { try Vault.openSnapshot(master: other, raw: snap) },
      { try Vault.openSnapshot(master: self.master, raw: Data(count: 40)) }
    ]
    for attempt in attempts {
      XCTAssertThrowsError(try attempt()) { error in
        XCTAssertFalse(((error as? IntegrityError)?.message ?? "").isEmpty)
      }
    }
  }

  func testRequestSignatureMatchesTheServer() throws {
    let s = try XCTUnwrap(vectors["signature"] as? [String: Any])
    XCTAssertEqual(Vault.hex(Vault.sha256(Data(try text("body", in: s).utf8))), try text("body_sha256", in: s))
    let time = try XCTUnwrap(s["time"] as? Int)
    let message = [try text("method", in: s), try text("target", in: s), String(time), try text("body_sha256", in: s)]
      .joined(separator: "\n")
    let secret = try XCTUnwrap(Vault.unhex(try text("secret", in: s)))
    XCTAssertEqual(Vault.hex(Vault.hmac(secret, Data(message.utf8))), try text("sign", in: s))
  }
}
