package expo.modules.tuckvaultcore

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.provider.DocumentsContract
import android.provider.MediaStore
import android.provider.OpenableColumns
import expo.modules.kotlin.exception.CodedException
import expo.modules.kotlin.exception.Exceptions
import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import java.io.File
import java.io.FileInputStream
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.io.InputStream
import java.io.RandomAccessFile

class TuckvaultException(code: String, message: String) : CodedException(code, message, null)

/**
 * Encryption and file reading for the Tuckvault app, off the JavaScript thread.
 * File content never passes through JavaScript: a piece of a file is read,
 * encrypted and written to a temporary file here, and uploaded from there.
 */
class TuckvaultCoreModule : Module() {
  private val context: Context
    get() = appContext.reactContext ?: throw Exceptions.ReactContextLost()

  override fun definition() = ModuleDefinition {
    Name("TuckvaultCore")

    // -- small, synchronous ----------------------------------------------------
    Function("newMasterKey") { Vault.hex(Vault.randomBytes(Vault.KEY_LEN)) }
    Function("randomHex") { bytes: Int -> Vault.hex(Vault.randomBytes(bytes.coerceIn(1, 64))) }
    Function("keyCheck") { master: String -> Vault.keyCheck(Vault.unhex(master)) }
    Function("hmacHex") { keyHex: String, message: String ->
      Vault.hex(Vault.hmac(Vault.unhex(keyHex), message.toByteArray(Charsets.UTF_8)))
    }
    Function("sha256Hex") { text: String -> Vault.hex(Vault.sha256(text.toByteArray(Charsets.UTF_8))) }
    Function("encodeRecoveryKey") { master: String -> Vault.encodeRecoveryKey(Vault.unhex(master)) }
    Function("decodeRecoveryKey") { text: String ->
      try {
        Vault.hex(Vault.decodeRecoveryKey(text))
      } catch (e: IllegalArgumentException) {
        throw TuckvaultException("ERR_RECOVERY_KEY", e.message ?: "That is not a valid recovery key.")
      }
    }

    // True while the phone has a Wi-Fi (or cable) connection, also when a VPN such as Tailscale
    // is the active network on top of it.
    Function("onWifi") {
      val manager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
      manager.allNetworks.any { network ->
        val caps = manager.getNetworkCapabilities(network)
        caps != null && (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
          caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET))
      }
    }

    // -- files -----------------------------------------------------------------
    AsyncFunction("statFile") { uri: String -> guarded { stat(Uri.parse(uri)) } }

    AsyncFunction("listTree") { uri: String -> guarded { listTree(Uri.parse(uri)) } }

    // Read up to `length` bytes at `offset`, encrypt them and write the blob to `outPath`.
    AsyncFunction("sealChunk") { master: String, uri: String, offset: Double, length: Int, outPath: String ->
      guarded {
        val data = ByteArray(length)
        val read = readRange(Uri.parse(uri), offset.toLong(), data)
        if (read == 0) {
          mapOf("read" to 0, "name" to null, "size" to 0)
        } else {
          val (name, blob) = Vault.sealChunk(Vault.unhex(master), data, read)
          FileOutputStream(plainPath(outPath)).use { it.write(blob) }
          mapOf("read" to read, "name" to name, "size" to blob.size)
        }
      }
    }

    // Check and decrypt the blob in `blobPath`; write (or append) the content to `outPath`.
    AsyncFunction("openChunk") { master: String, name: String, blobPath: String, outPath: String, append: Boolean ->
      guarded {
        val data = Vault.openChunk(Vault.unhex(master), name, File(plainPath(blobPath)).readBytes())
        FileOutputStream(plainPath(outPath), append).use { it.write(data) }
        data.size
      }
    }

    AsyncFunction("sealSnapshot") { master: String, json: String, outPath: String ->
      guarded {
        val raw = Vault.sealSnapshot(Vault.unhex(master), json.toByteArray(Charsets.UTF_8))
        FileOutputStream(plainPath(outPath)).use { it.write(raw) }
        mapOf("size" to raw.size, "sha256" to Vault.hex(Vault.sha256(raw)))
      }
    }

    AsyncFunction("openSnapshot") { master: String, path: String ->
      guarded {
        String(Vault.openSnapshot(Vault.unhex(master), File(plainPath(path)).readBytes()), Charsets.UTF_8)
      }
    }

    AsyncFunction("sha256File") { path: String ->
      guarded {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        FileInputStream(plainPath(path)).use { input ->
          val buf = ByteArray(1 shl 16)
          while (true) {
            val n = input.read(buf)
            if (n < 0) break
            md.update(buf, 0, n)
          }
        }
        Vault.hex(md.digest())
      }
    }
  }

  private inline fun <T> guarded(block: () -> T): T {
    try {
      return block()
    } catch (e: IntegrityException) {
      throw TuckvaultException("ERR_INTEGRITY", e.message ?: "Stored data is damaged.")
    } catch (e: FileNotFoundException) {
      throw TuckvaultException("ERR_NOT_FOUND", e.message ?: "The file is gone.")
    } catch (e: SecurityException) {
      throw TuckvaultException("ERR_NO_ACCESS", e.message ?: "No permission to read the file.")
    } catch (e: java.io.IOException) {
      throw TuckvaultException("ERR_IO", e.message ?: "The file could not be read.")
    } catch (e: IllegalArgumentException) {
      throw TuckvaultException("ERR_ARGUMENT", e.message ?: "Bad argument.")
    }
  }

  private fun plainPath(pathOrUri: String): String =
    if (pathOrUri.startsWith("file:")) Uri.parse(pathOrUri).path ?: pathOrUri else pathOrUri

  private fun isFile(uri: Uri) = uri.scheme == null || uri.scheme == "file"

  /**
   * Photos are read as the camera wrote them. Without this Android hands out a copy with the
   * location removed, and a restored photo would no longer know where it was taken.
   */
  private fun original(uri: Uri): Uri {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && uri.authority == MediaStore.AUTHORITY) {
      return MediaStore.setRequireOriginal(uri)
    }
    return uri
  }

  private fun <T> withStream(uri: Uri, block: (FileInputStream) -> T): T {
    val resolver = context.contentResolver
    val pfd = try {
      resolver.openFileDescriptor(original(uri), "r")
    } catch (e: SecurityException) {
      resolver.openFileDescriptor(uri, "r")  // no location permission: the redacted copy is what we can get
    } catch (e: UnsupportedOperationException) {
      resolver.openFileDescriptor(uri, "r")
    } ?: throw FileNotFoundException("Cannot open $uri")
    return pfd.use { FileInputStream(it.fileDescriptor).use(block) }
  }

  private fun readFully(input: InputStream, into: ByteArray): Int {
    var total = 0
    while (total < into.size) {
      val n = input.read(into, total, into.size - total)
      if (n < 0) break
      total += n
    }
    return total
  }

  private fun readRange(uri: Uri, offset: Long, into: ByteArray): Int {
    if (isFile(uri)) {
      RandomAccessFile(uri.path ?: throw FileNotFoundException("bad path"), "r").use { f ->
        if (offset >= f.length()) return 0
        f.seek(offset)
        var total = 0
        while (total < into.size) {
          val n = f.read(into, total, into.size - total)
          if (n < 0) break
          total += n
        }
        return total
      }
    }
    return withStream(uri) { input ->
      try {
        input.channel.position(offset)
      } catch (e: java.io.IOException) {  // a provider that hands out a pipe: skip instead of seeking
        var left = offset
        while (left > 0) {
          val skipped = input.skip(left)
          if (skipped <= 0) break
          left -= skipped
        }
        if (left > 0) return@withStream 0
      }
      readFully(input, into)
    }
  }

  private fun stat(uri: Uri): Map<String, Any?> {
    if (isFile(uri)) {
      val f = File(uri.path ?: throw FileNotFoundException("bad path"))
      if (!f.isFile) throw FileNotFoundException("${f.name} is gone")
      return mapOf("size" to f.length().toDouble(), "mtime" to f.lastModified().toDouble(), "name" to f.name)
    }
    var size: Double? = null
    var name: String? = null
    var mtime: Double? = null
    context.contentResolver.query(uri, null, null, null, null)?.use { c ->
      if (c.moveToFirst()) {
        val sizeAt = c.getColumnIndex(OpenableColumns.SIZE)
        if (sizeAt >= 0 && !c.isNull(sizeAt)) size = c.getLong(sizeAt).toDouble()
        val nameAt = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
        if (nameAt >= 0 && !c.isNull(nameAt)) name = c.getString(nameAt)
        val docAt = c.getColumnIndex(DocumentsContract.Document.COLUMN_LAST_MODIFIED)
        val mediaAt = c.getColumnIndex(MediaStore.MediaColumns.DATE_MODIFIED)
        if (docAt >= 0 && !c.isNull(docAt)) mtime = c.getLong(docAt).toDouble()
        else if (mediaAt >= 0 && !c.isNull(mediaAt)) mtime = c.getLong(mediaAt) * 1000.0
      }
    } ?: throw FileNotFoundException("$uri is gone")
    if (size == null) {  // the provider does not say: ask the file itself
      size = withStream(uri) { it.channel.size().toDouble() }
    }
    return mapOf("size" to size, "mtime" to mtime, "name" to name)
  }

  /**
   * Every file below a folder: [{uri, path, size, mtime}], path relative to the folder with "/".
   * A folder that cannot be listed fails the whole call: reporting it as empty would make its
   * files look deleted.
   */
  private fun listTree(root: Uri): List<Map<String, Any?>> {
    val out = ArrayList<Map<String, Any?>>()
    if (isFile(root)) {
      val base = File(root.path ?: throw FileNotFoundException("bad path"))
      if (!base.isDirectory) throw FileNotFoundException("${base.name} is gone")
      val stack = ArrayDeque<Pair<File, String>>()
      stack.addLast(Pair(base, ""))
      while (stack.isNotEmpty()) {
        val (dir, prefix) = stack.removeLast()
        val children = dir.listFiles() ?: throw java.io.IOException("Cannot list the folder " + dir.name)
        for (f in children) {
          if (java.nio.file.Files.isSymbolicLink(f.toPath())) continue
          if (f.isDirectory) stack.addLast(Pair(f, prefix + f.name + "/"))
          else if (f.isFile) out.add(mapOf("uri" to Uri.fromFile(f).toString(), "path" to prefix + f.name,
                                           "size" to f.length().toDouble(), "mtime" to f.lastModified().toDouble()))
        }
      }
      return out
    }
    val resolver = context.contentResolver
    val rootId = if (DocumentsContract.isDocumentUri(context, root)) DocumentsContract.getDocumentId(root)
                 else DocumentsContract.getTreeDocumentId(root)
    val columns = arrayOf(DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                          DocumentsContract.Document.COLUMN_DISPLAY_NAME,
                          DocumentsContract.Document.COLUMN_MIME_TYPE,
                          DocumentsContract.Document.COLUMN_SIZE,
                          DocumentsContract.Document.COLUMN_LAST_MODIFIED)
    val stack = ArrayDeque<Pair<String, String>>()
    stack.addLast(Pair(rootId, ""))
    var folders = 0
    while (stack.isNotEmpty()) {
      val (docId, prefix) = stack.removeLast()
      if (++folders > 50000) throw java.io.IOException("The folder has too many subfolders to list")
      val children = DocumentsContract.buildChildDocumentsUriUsingTree(root, docId)
      val cursor = resolver.query(children, columns, null, null, null)
        ?: throw java.io.IOException("The folder could not be listed")
      cursor.use { c ->
        while (c.moveToNext()) {
          val id = c.getString(0) ?: continue
          val name = (c.getString(1) ?: continue).replace('/', '_')
          if (c.getString(2) == DocumentsContract.Document.MIME_TYPE_DIR) {
            stack.addLast(Pair(id, "$prefix$name/"))
          } else {
            out.add(mapOf("uri" to DocumentsContract.buildDocumentUriUsingTree(root, id).toString(),
                          "path" to prefix + name,
                          "size" to (if (c.isNull(3)) null else c.getLong(3).toDouble()),
                          "mtime" to (if (c.isNull(4)) null else c.getLong(4).toDouble())))
          }
        }
      }
    }
    return out
  }
}
