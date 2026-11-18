import CryptoKit
import Foundation
import zlib

/// Stored data is damaged, tampered with, or was written with another key.
public struct IntegrityError: Error {
  public let message: String
  public init(_ message: String) { self.message = message }
}

/// A recovery key that cannot be right; `message` is fit to show the user.
public struct RecoveryKeyError: Error {
  public let message: String
  public init(_ message: String) { self.message = message }
}

/// The Tuckvault storage format, byte for byte the same as tuckvault/crypto.py:
/// a backup made on a phone can be restored by the PC program and the other way round.
///
/// No UIKit or Expo here, so it is unit tested with `swift test` (see swift-tests/).
public enum Vault {
  static let blobMagic = Data("SKB1".utf8)
  static let snapMagic = Data("SKS1".utf8)
  public static let keyLen = 32
  static let nonceLen = 12
  static let tagLen = 16

  // MARK: small helpers

  public static func hex(_ data: Data) -> String {
    let digits = Array("0123456789abcdef".utf8)
    var out = [UInt8]()
    out.reserveCapacity(data.count * 2)
    for byte in data {
      out.append(digits[Int(byte >> 4)])
      out.append(digits[Int(byte & 0x0F)])
    }
    return String(decoding: out, as: UTF8.self)
  }

  public static func unhex(_ text: String) -> Data? {
    let chars = Array(text.utf8)
    guard chars.count % 2 == 0 else { return nil }
    func value(_ c: UInt8) -> UInt8? {
      switch c {
      case 48...57: return c - 48
      case 97...102: return c - 87
      case 65...70: return c - 55
      default: return nil
      }
    }
    var out = Data(capacity: chars.count / 2)
    var i = 0
    while i < chars.count {
      guard let hi = value(chars[i]), let lo = value(chars[i + 1]) else { return nil }
      out.append(hi << 4 | lo)
      i += 2
    }
    return out
  }

  public static func sha256(_ data: Data) -> Data {
    Data(SHA256.hash(data: data))
  }

  public static func hmac(_ key: Data, _ parts: Data...) -> Data {
    var mac = HMAC<SHA256>(key: SymmetricKey(data: key))
    for part in parts { mac.update(data: part) }
    return Data(mac.finalize())
  }

  public static func randomBytes(_ count: Int) -> Data {
    var generator = SystemRandomNumberGenerator()
    return Data((0..<count).map { _ in UInt8.random(in: 0...255, using: &generator) })
  }

  /// "safekeep" is the program's first name; the label is part of the storage format.
  static func derive(_ master: Data, _ label: String) -> Data {
    precondition(master.count == keyLen, "master key must be 32 bytes")
    return hmac(master, Data("safekeep/v1/\(label)".utf8))
  }

  public static func keyCheck(_ master: Data) -> String {
    hex(derive(master, "key-check"))
  }

  /// zlib-format compression, the same as Python's zlib.compress. `data` must not be empty.
  static func zDeflate(_ data: Data, level: Int32) -> Data {
    let source = [UInt8](data)
    var length = compressBound(uLong(source.count))
    var dest = [UInt8](repeating: 0, count: Int(length))
    let status = compress2(&dest, &length, source, uLong(source.count), level)
    precondition(status == Z_OK, "zlib compress failed")
    return Data(dest[0..<Int(length)])
  }

  static func zInflate(_ data: Data, what: String, limit: Int = 1 << 30) throws -> Data {
    guard !data.isEmpty else { throw IntegrityError("\(what) is damaged") }
    var stream = z_stream()
    guard inflateInit_(&stream, ZLIB_VERSION, Int32(MemoryLayout<z_stream>.size)) == Z_OK else {
      throw IntegrityError("\(what) is damaged")
    }
    defer { inflateEnd(&stream) }
    var source = [UInt8](data)
    var buffer = [UInt8](repeating: 0, count: 64 * 1024)
    var out = Data()
    var failed = false
    source.withUnsafeMutableBufferPointer { input in
      stream.next_in = input.baseAddress
      stream.avail_in = uInt(input.count)
      while true {
        var status: Int32 = Z_OK
        let produced: Int = buffer.withUnsafeMutableBufferPointer { output in
          stream.next_out = output.baseAddress
          stream.avail_out = uInt(output.count)
          status = inflate(&stream, Z_NO_FLUSH)
          return output.count - Int(stream.avail_out)
        }
        if status != Z_OK && status != Z_STREAM_END {
          failed = true
          return
        }
        out.append(contentsOf: buffer[0..<produced])
        if status == Z_STREAM_END { return }
        if out.count > limit || (produced == 0 && stream.avail_in == 0) {  // endless or cut short
          failed = true
          return
        }
      }
    }
    if failed { throw IntegrityError("\(what) is damaged") }
    return out
  }

  // MARK: file data

  /// Encrypt one piece of a file. Returns the blob's name and the blob.
  public static func sealChunk(master: Data, data: Data) throws -> (name: String, blob: Data) {
    var flag: UInt8 = 0
    var body = data
    if data.count >= 512 {
      let sample = data.prefix(65536)
      if Double(zDeflate(Data(sample), level: 1).count) < 0.9 * Double(sample.count) {
        let compressed = zDeflate(data, level: 3)
        if compressed.count < data.count {
          flag = 1
          body = compressed
        }
      }
    }
    var header = blobMagic
    header.append(flag)
    let nonce = hmac(derive(master, "chunk-nonce"), header, body).prefix(nonceLen)
    let box = try AES.GCM.seal(body, using: SymmetricKey(data: derive(master, "chunk-encryption")),
                               nonce: try AES.GCM.Nonce(data: nonce), authenticating: header)
    var blob = header
    blob.append(nonce)
    blob.append(box.ciphertext)
    blob.append(box.tag)
    return (hex(sha256(blob)), blob)
  }

  public static func openChunk(master: Data, name: String, blob input: Data) throws -> Data {
    let blob = Data(input)  // indices from zero, whatever slice was passed in
    let short = String(name.prefix(12))
    guard hex(sha256(blob)) == name else {
      throw IntegrityError("blob \(short)… does not match its hash")
    }
    guard blob.count >= 5 + nonceLen + tagLen, blob.prefix(4) == blobMagic else {
      throw IntegrityError("blob \(short)… is not a Tuckvault blob")
    }
    let header = blob.subdata(in: 0..<5)
    let body: Data
    do {
      let box = try AES.GCM.SealedBox(
        nonce: try AES.GCM.Nonce(data: blob.subdata(in: 5..<(5 + nonceLen))),
        ciphertext: blob.subdata(in: (5 + nonceLen)..<(blob.count - tagLen)),
        tag: blob.subdata(in: (blob.count - tagLen)..<blob.count))
      body = try AES.GCM.open(box, using: SymmetricKey(data: derive(master, "chunk-encryption")),
                              authenticating: header)
    } catch {
      throw IntegrityError("blob \(short)… failed authentication (wrong key?)")
    }
    if header[4] == 1 {
      return try zInflate(body, what: "blob \(short)…")
    }
    return body
  }

  // MARK: manifests

  public static func sealSnapshot(master: Data, json: Data, nonce: Data? = nil) throws -> Data {
    let body = zDeflate(json, level: 6)
    let nonce = nonce ?? randomBytes(nonceLen)
    let box = try AES.GCM.seal(body, using: SymmetricKey(data: derive(master, "snapshot-encryption")),
                               nonce: try AES.GCM.Nonce(data: nonce), authenticating: snapMagic)
    var raw = snapMagic
    raw.append(nonce)
    raw.append(box.ciphertext)
    raw.append(box.tag)
    return raw
  }

  public static func openSnapshot(master: Data, raw input: Data) throws -> Data {
    let raw = Data(input)
    guard raw.count >= 4 + nonceLen + tagLen, raw.prefix(4) == snapMagic else {
      throw IntegrityError("not a Tuckvault snapshot")
    }
    let body: Data
    do {
      let box = try AES.GCM.SealedBox(
        nonce: try AES.GCM.Nonce(data: raw.subdata(in: 4..<(4 + nonceLen))),
        ciphertext: raw.subdata(in: (4 + nonceLen)..<(raw.count - tagLen)),
        tag: raw.subdata(in: (raw.count - tagLen)..<raw.count))
      body = try AES.GCM.open(box, using: SymmetricKey(data: derive(master, "snapshot-encryption")),
                              authenticating: snapMagic)
    } catch {
      throw IntegrityError("snapshot failed authentication (damaged, or wrong key)")
    }
    return try zInflate(body, what: "snapshot")
  }

  // MARK: recovery key (what the user writes down)

  static let base32 = Array("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567".utf8)

  public static func encodeRecoveryKey(_ master: Data) -> String {
    precondition(master.count == keyLen, "master key must be 32 bytes")
    var raw = master
    raw.append(sha256(master).prefix(3))
    var out = [UInt8]()
    var buffer: UInt32 = 0
    var bits: UInt32 = 0
    for byte in raw {
      buffer = (buffer << 8 | UInt32(byte)) & 0xFFFFF
      bits += 8
      while bits >= 5 {
        out.append(base32[Int((buffer >> (bits - 5)) & 31)])
        bits -= 5
      }
    }
    if bits > 0 { out.append(base32[Int((buffer << (5 - bits)) & 31)]) }
    var groups = [String]()
    var i = 0
    while i < out.count {
      groups.append(String(decoding: out[i..<min(i + 4, out.count)], as: UTF8.self))
      i += 4
    }
    return groups.joined(separator: "-")
  }

  public static func decodeRecoveryKey(_ text: String) throws -> Data {
    let clean = text.uppercased().utf8.filter { ($0 >= 65 && $0 <= 90) || ($0 >= 48 && $0 <= 57) }
    var raw = Data()
    var buffer: UInt32 = 0
    var bits: UInt32 = 0
    for c in clean {
      guard let value = base32.firstIndex(of: c) else {
        throw RecoveryKeyError("That is not a valid recovery key.")
      }
      buffer = (buffer << 5 | UInt32(value)) & 0xFFFFF
      bits += 5
      if bits >= 8 {
        raw.append(UInt8((buffer >> (bits - 8)) & 0xFF))
        bits -= 8
      }
    }
    guard clean.count == 56, raw.count == keyLen + 3 else {
      throw RecoveryKeyError("That is not a valid recovery key.")
    }
    let master = raw.subdata(in: 0..<keyLen)
    guard sha256(master).prefix(3) == raw.subdata(in: keyLen..<(keyLen + 3)) else {
      throw RecoveryKeyError("That recovery key has a typo in it (checksum mismatch).")
    }
    return master
  }
}
