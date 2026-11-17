import ExpoModulesCore
import Foundation

/// Encryption and file reading for the Tuckvault app, off the JavaScript thread.
/// File content never passes through JavaScript: a piece of a file is read,
/// encrypted and written to a temporary file here, and uploaded from there.
public class TuckvaultCoreModule: Module {
  public func definition() -> ModuleDefinition {
    Name("TuckvaultCore")

    // MARK: small, synchronous

    Function("newMasterKey") { () -> String in
      Vault.hex(Vault.randomBytes(Vault.keyLen))
    }
    Function("randomHex") { (bytes: Int) -> String in
      Vault.hex(Vault.randomBytes(min(max(bytes, 1), 64)))
    }
    Function("keyCheck") { (master: String) -> String in
      Vault.keyCheck(try key(master))
    }
    Function("hmacHex") { (keyHex: String, message: String) -> String in
      guard let key = Vault.unhex(keyHex) else { throw failure("ERR_ARGUMENT", "Bad key.") }
      return Vault.hex(Vault.hmac(key, Data(message.utf8)))
    }
    Function("sha256Hex") { (text: String) -> String in
      Vault.hex(Vault.sha256(Data(text.utf8)))
    }
    Function("encodeRecoveryKey") { (master: String) -> String in
      Vault.encodeRecoveryKey(try key(master))
    }
    Function("decodeRecoveryKey") { (text: String) -> String in
      do {
        return Vault.hex(try Vault.decodeRecoveryKey(text))
      } catch let error as RecoveryKeyError {
        throw failure("ERR_RECOVERY_KEY", error.message)
      }
    }

    // True while the phone has a Wi-Fi address, also when a VPN such as Tailscale is active on top of it.
    Function("onWifi") { () -> Bool in
      var list: UnsafeMutablePointer<ifaddrs>?
      guard getifaddrs(&list) == 0 else { return false }
      defer { freeifaddrs(list) }
      var cursor = list
      while let entry = cursor {
        let flags = Int32(entry.pointee.ifa_flags)
        if let address = entry.pointee.ifa_addr, Int32(address.pointee.sa_family) == AF_INET,
           String(cString: entry.pointee.ifa_name) == "en0",
           flags & IFF_UP != 0, flags & IFF_RUNNING != 0 {
          return true
        }
        cursor = entry.pointee.ifa_next
      }
      return false
    }

    // MARK: files

    AsyncFunction("statFile") { (uri: String) -> [String: Any?] in
      try guarded {
        let url = try fileURL(uri)
        return try scoped(url) {
          let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey, .isRegularFileKey])
          guard values.isRegularFile == true else { throw failure("ERR_NOT_FOUND", "\(url.lastPathComponent) is gone") }
          return [
            "size": Double(values.fileSize ?? 0),
            "mtime": values.contentModificationDate.map { ($0.timeIntervalSince1970 * 1000).rounded() },
            "name": url.lastPathComponent
          ]
        }
      }
    }

    AsyncFunction("listTree") { (uri: String) -> [[String: Any?]] in
      try guarded {
        // Symbolic links are resolved on both sides (/var is /private/var on iOS), or no file
        // would be recognised as lying inside the folder.
        let root = try fileURL(uri).resolvingSymlinksInPath()
        return try scoped(root) {
          var out = [[String: Any?]]()
          let keys: [URLResourceKey] = [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey, .contentModificationDateKey]
          var listingError: Error?
          guard let walker = FileManager.default.enumerator(
            at: root, includingPropertiesForKeys: keys, options: [],
            errorHandler: { _, error in
              listingError = error  // a subfolder that cannot be listed must not look empty
              return false
            }) else {
            throw failure("ERR_NOT_FOUND", "\(root.lastPathComponent) is gone")
          }
          let prefix = root.path.hasSuffix("/") ? root.path : root.path + "/"
          for case let url as URL in walker {
            guard let values = try? url.resourceValues(forKeys: Set(keys)),
                  values.isRegularFile == true, values.isSymbolicLink != true else { continue }
            let path = url.resolvingSymlinksInPath().path
            guard path.hasPrefix(prefix) else {
              throw failure("ERR_IO", "A file in the folder could not be placed: " + url.lastPathComponent)
            }
            out.append([
              "uri": url.absoluteString,
              "path": String(path.dropFirst(prefix.count)),
              "size": Double(values.fileSize ?? 0),
              "mtime": values.contentModificationDate.map { ($0.timeIntervalSince1970 * 1000).rounded() }
            ])
          }
          if let listingError {
            throw failure("ERR_IO", "The folder could not be listed: " + listingError.localizedDescription)
          }
          return out
        }
      }
    }

    // Read up to `length` bytes at `offset`, encrypt them and write the blob to `outPath`.
    AsyncFunction("sealChunk") { (master: String, uri: String, offset: Double, length: Int, outPath: String) -> [String: Any?] in
      try guarded {
        let masterKey = try key(master)
        let url = try fileURL(uri)
        let data: Data = try scoped(url) {
          let handle = try FileHandle(forReadingFrom: url)
          defer { try? handle.close() }
          try handle.seek(toOffset: UInt64(max(offset, 0)))
          var collected = Data()
          while collected.count < length {
            guard let more = try handle.read(upToCount: length - collected.count), !more.isEmpty else { break }
            collected.append(more)
          }
          return collected
        }
        if data.isEmpty {
          return ["read": 0, "name": nil, "size": 0]
        }
        let sealed = try Vault.sealChunk(master: masterKey, data: data)
        try sealed.blob.write(to: try fileURL(outPath))
        return ["read": data.count, "name": sealed.name, "size": sealed.blob.count]
      }
    }

    // Check and decrypt the blob in `blobPath`; write (or append) the content to `outPath`.
    AsyncFunction("openChunk") { (master: String, name: String, blobPath: String, outPath: String, append: Bool) -> Int in
      try guarded {
        let blob = try Data(contentsOf: try fileURL(blobPath))
        let data = try Vault.openChunk(master: try key(master), name: name, blob: blob)
        let out = try fileURL(outPath)
        if append, FileManager.default.fileExists(atPath: out.path) {
          let handle = try FileHandle(forWritingTo: out)
          defer { try? handle.close() }
          try handle.seekToEnd()
          try handle.write(contentsOf: data)
        } else {
          try data.write(to: out)
        }
        return data.count
      }
    }

    AsyncFunction("sealSnapshot") { (master: String, json: String, outPath: String) -> [String: Any?] in
      try guarded {
        let raw = try Vault.sealSnapshot(master: try key(master), json: Data(json.utf8))
        try raw.write(to: try fileURL(outPath))
        return ["size": raw.count, "sha256": Vault.hex(Vault.sha256(raw))]
      }
    }

    AsyncFunction("openSnapshot") { (master: String, path: String) -> String in
      try guarded {
        let raw = try Data(contentsOf: try fileURL(path))
        let json = try Vault.openSnapshot(master: try key(master), raw: raw)
        return String(decoding: json, as: UTF8.self)
      }
    }

    AsyncFunction("sha256File") { (path: String) -> String in
      try guarded {
        Vault.hex(Vault.sha256(try Data(contentsOf: try fileURL(path), options: .mappedIfSafe)))
      }
    }
  }
}

private func failure(_ code: String, _ message: String) -> Exception {
  Exception(name: "TuckvaultException", description: message, code: code)
}

private func key(_ hex: String) throws -> Data {
  guard let master = Vault.unhex(hex), master.count == Vault.keyLen else {
    throw failure("ERR_ARGUMENT", "Bad key.")
  }
  return master
}

private func fileURL(_ pathOrUri: String) throws -> URL {
  if pathOrUri.hasPrefix("file:") {
    guard let url = URL(string: pathOrUri) else { throw failure("ERR_ARGUMENT", "Bad file address.") }
    return url
  }
  guard pathOrUri.hasPrefix("/") else { throw failure("ERR_ARGUMENT", "Bad file address.") }
  return URL(fileURLWithPath: pathOrUri)
}

/// Files and folders the user picked outside the app's own storage need this around every access.
private func scoped<T>(_ url: URL, _ body: () throws -> T) rethrows -> T {
  let accessing = url.startAccessingSecurityScopedResource()
  defer { if accessing { url.stopAccessingSecurityScopedResource() } }
  return try body()
}

private func guarded<T>(_ body: () throws -> T) throws -> T {
  do {
    return try body()
  } catch let error as Exception {
    throw error
  } catch let error as IntegrityError {
    throw failure("ERR_INTEGRITY", error.message)
  } catch let error as CocoaError where error.code == .fileReadNoSuchFile || error.code == .fileNoSuchFile {
    throw failure("ERR_NOT_FOUND", "The file is gone.")
  } catch let error as CocoaError where error.code == .fileReadNoPermission {
    throw failure("ERR_NO_ACCESS", "No permission to read the file.")
  } catch {
    throw failure("ERR_IO", error.localizedDescription)
  }
}
