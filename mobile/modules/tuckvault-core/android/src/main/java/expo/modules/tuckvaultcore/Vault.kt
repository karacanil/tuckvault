package expo.modules.tuckvaultcore

import java.io.ByteArrayOutputStream
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.zip.DataFormatException
import java.util.zip.Deflater
import java.util.zip.Inflater
import javax.crypto.AEADBadTagException
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/** Stored data is damaged, tampered with, or was written with another key. */
class IntegrityException(message: String) : Exception(message)

/**
 * The Tuckvault storage format, byte for byte the same as tuckvault/crypto.py:
 * a backup made on a phone can be restored by the PC program and the other way round.
 *
 * Plain JVM code (no Android classes), so it is unit tested on the build machine.
 */
object Vault {
  private val BLOB_MAGIC = "SKB1".toByteArray(Charsets.US_ASCII)
  private val SNAP_MAGIC = "SKS1".toByteArray(Charsets.US_ASCII)
  const val KEY_LEN = 32
  private const val NONCE_LEN = 12
  private const val TAG_LEN = 16
  private val random = SecureRandom()

  // -- small helpers -------------------------------------------------------
  fun hex(bytes: ByteArray): String {
    val out = StringBuilder(bytes.size * 2)
    for (b in bytes) {
      out.append(Character.forDigit((b.toInt() shr 4) and 0xF, 16))
      out.append(Character.forDigit(b.toInt() and 0xF, 16))
    }
    return out.toString()
  }

  fun unhex(text: String): ByteArray {
    require(text.length % 2 == 0) { "bad hex" }
    return ByteArray(text.length / 2) { i ->
      val hi = Character.digit(text[2 * i], 16)
      val lo = Character.digit(text[2 * i + 1], 16)
      require(hi >= 0 && lo >= 0) { "bad hex" }
      ((hi shl 4) or lo).toByte()
    }
  }

  fun sha256(vararg parts: ByteArray): ByteArray {
    val md = MessageDigest.getInstance("SHA-256")
    for (p in parts) md.update(p)
    return md.digest()
  }

  fun hmac(key: ByteArray, vararg parts: ByteArray): ByteArray {
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(key, "HmacSHA256"))
    for (p in parts) mac.update(p)
    return mac.doFinal()
  }

  fun randomBytes(n: Int): ByteArray = ByteArray(n).also { random.nextBytes(it) }

  /** "safekeep" is the program's first name; the label is part of the storage format. */
  private fun derive(master: ByteArray, label: String): ByteArray {
    require(master.size == KEY_LEN) { "master key must be 32 bytes" }
    return hmac(master, ("safekeep/v1/$label").toByteArray(Charsets.US_ASCII))
  }

  fun keyCheck(master: ByteArray): String = hex(derive(master, "key-check"))

  private fun gcm(mode: Int, key: ByteArray, nonce: ByteArray, aad: ByteArray, data: ByteArray,
                  offset: Int, length: Int): ByteArray {
    val cipher = Cipher.getInstance("AES/GCM/NoPadding")
    cipher.init(mode, SecretKeySpec(key, "AES"), GCMParameterSpec(TAG_LEN * 8, nonce))
    cipher.updateAAD(aad)
    return cipher.doFinal(data, offset, length)
  }

  private fun deflate(data: ByteArray, length: Int, level: Int): ByteArray {
    val deflater = Deflater(level)
    try {
      deflater.setInput(data, 0, length)
      deflater.finish()
      val out = ByteArrayOutputStream(length / 2 + 64)
      val buf = ByteArray(64 * 1024)
      while (!deflater.finished()) out.write(buf, 0, deflater.deflate(buf))
      return out.toByteArray()
    } finally {
      deflater.end()
    }
  }

  private fun inflate(data: ByteArray, what: String, limit: Int = 1 shl 30): ByteArray {
    val inflater = Inflater()
    try {
      inflater.setInput(data)
      val out = ByteArrayOutputStream(data.size * 2 + 64)
      val buf = ByteArray(64 * 1024)
      while (!inflater.finished()) {
        val n = inflater.inflate(buf)
        if (n == 0 && (inflater.needsInput() || inflater.needsDictionary())) {
          throw IntegrityException("$what is damaged")
        }
        out.write(buf, 0, n)
        if (out.size() > limit) throw IntegrityException("$what is damaged")
      }
      return out.toByteArray()
    } catch (e: DataFormatException) {
      throw IntegrityException("$what is damaged")
    } finally {
      inflater.end()
    }
  }

  // -- file data -------------------------------------------------------------
  /** Encrypt the first [length] bytes of [data]. Returns the blob's name and the blob. */
  fun sealChunk(master: ByteArray, data: ByteArray, length: Int = data.size): Pair<String, ByteArray> {
    var flag = 0
    var body = data
    var bodyLen = length
    if (length >= 512) {
      val sample = minOf(length, 65536)
      if (deflate(data, sample, 1).size < 0.9 * sample) {
        val comp = deflate(data, length, 3)
        if (comp.size < length) {
          flag = 1
          body = comp
          bodyLen = comp.size
        }
      }
    }
    val header = BLOB_MAGIC + byteArrayOf(flag.toByte())
    val mac = Mac.getInstance("HmacSHA256")
    mac.init(SecretKeySpec(derive(master, "chunk-nonce"), "HmacSHA256"))
    mac.update(header)
    mac.update(body, 0, bodyLen)
    val nonce = mac.doFinal().copyOf(NONCE_LEN)
    val sealed = gcm(Cipher.ENCRYPT_MODE, derive(master, "chunk-encryption"), nonce, header, body, 0, bodyLen)
    val blob = ByteArray(header.size + NONCE_LEN + sealed.size)
    System.arraycopy(header, 0, blob, 0, header.size)
    System.arraycopy(nonce, 0, blob, header.size, NONCE_LEN)
    System.arraycopy(sealed, 0, blob, header.size + NONCE_LEN, sealed.size)
    return Pair(hex(sha256(blob)), blob)
  }

  fun openChunk(master: ByteArray, name: String, blob: ByteArray): ByteArray {
    val short = name.take(12)
    if (hex(sha256(blob)) != name) throw IntegrityException("blob $short… does not match its hash")
    if (blob.size < 5 + NONCE_LEN + TAG_LEN || !blob.copyOf(4).contentEquals(BLOB_MAGIC)) {
      throw IntegrityException("blob $short… is not a Tuckvault blob")
    }
    val header = blob.copyOf(5)
    val nonce = blob.copyOfRange(5, 5 + NONCE_LEN)
    val body = try {
      gcm(Cipher.DECRYPT_MODE, derive(master, "chunk-encryption"), nonce, header, blob,
          5 + NONCE_LEN, blob.size - 5 - NONCE_LEN)
    } catch (e: AEADBadTagException) {
      throw IntegrityException("blob $short… failed authentication (wrong key?)")
    }
    return if (header[4].toInt() == 1) inflate(body, "blob $short…") else body
  }

  // -- manifests ---------------------------------------------------------------
  fun sealSnapshot(master: ByteArray, json: ByteArray, nonce: ByteArray = randomBytes(NONCE_LEN)): ByteArray {
    val body = deflate(json, json.size, 6)
    val sealed = gcm(Cipher.ENCRYPT_MODE, derive(master, "snapshot-encryption"), nonce, SNAP_MAGIC,
                     body, 0, body.size)
    return SNAP_MAGIC + nonce + sealed
  }

  fun openSnapshot(master: ByteArray, raw: ByteArray): ByteArray {
    if (raw.size < 4 + NONCE_LEN + TAG_LEN || !raw.copyOf(4).contentEquals(SNAP_MAGIC)) {
      throw IntegrityException("not a Tuckvault snapshot")
    }
    val body = try {
      gcm(Cipher.DECRYPT_MODE, derive(master, "snapshot-encryption"), raw.copyOfRange(4, 4 + NONCE_LEN),
          SNAP_MAGIC, raw, 4 + NONCE_LEN, raw.size - 4 - NONCE_LEN)
    } catch (e: AEADBadTagException) {
      throw IntegrityException("snapshot failed authentication (damaged, or wrong key)")
    }
    return inflate(body, "snapshot")
  }

  // -- recovery key (what the user writes down) ----------------------------------
  private const val B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"

  fun encodeRecoveryKey(master: ByteArray): String {
    require(master.size == KEY_LEN) { "master key must be 32 bytes" }
    val raw = master + sha256(master).copyOf(3)
    val text = StringBuilder()
    var buffer = 0
    var bits = 0
    for (b in raw) {
      buffer = (buffer shl 8) or (b.toInt() and 0xFF)
      bits += 8
      while (bits >= 5) {
        text.append(B32[(buffer shr (bits - 5)) and 31])
        bits -= 5
      }
    }
    if (bits > 0) text.append(B32[(buffer shl (5 - bits)) and 31])
    return text.chunked(4).joinToString("-")
  }

  /** Throws IllegalArgumentException with a message fit to show the user. */
  fun decodeRecoveryKey(text: String): ByteArray {
    val clean = text.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' }
    val out = ByteArrayOutputStream()
    var buffer = 0
    var bits = 0
    for (c in clean) {
      val v = B32.indexOf(c)
      if (v < 0) throw IllegalArgumentException("That is not a valid recovery key.")
      buffer = ((buffer shl 5) or v) and 0xFFFFF
      bits += 5
      if (bits >= 8) {
        out.write((buffer shr (bits - 8)) and 0xFF)
        bits -= 8
      }
    }
    val raw = out.toByteArray()
    if (clean.length != 56 || raw.size != KEY_LEN + 3) {
      throw IllegalArgumentException("That is not a valid recovery key.")
    }
    val master = raw.copyOf(KEY_LEN)
    if (!sha256(master).copyOf(3).contentEquals(raw.copyOfRange(KEY_LEN, KEY_LEN + 3))) {
      throw IllegalArgumentException("That recovery key has a typo in it (checksum mismatch).")
    }
    return master
  }
}
