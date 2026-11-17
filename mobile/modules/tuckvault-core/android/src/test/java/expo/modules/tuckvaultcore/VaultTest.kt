package expo.modules.tuckvaultcore

import java.io.File
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Checks the Android code against bytes produced by the PC program (tools/make_mobile_vectors.py). */
class VaultTest {
  private val vectors = JSONObject(File(System.getProperty("tuckvault.vectors")!!).readText(Charsets.UTF_8))
  private val master = Vault.unhex(vectors.getString("master"))

  @Test
  fun keyCheckAndRecoveryKeyMatchThePc() {
    assertEquals(vectors.getString("key_check"), Vault.keyCheck(master))
    assertEquals(vectors.getString("recovery_key"), Vault.encodeRecoveryKey(master))
    assertArrayEquals(master, Vault.decodeRecoveryKey(vectors.getString("recovery_key")))
    assertArrayEquals(master, Vault.decodeRecoveryKey(vectors.getString("recovery_key").lowercase().replace("-", " ")))
    val bad = vectors.getJSONObject("bad_recovery_keys")
    for (key in bad.keys()) {
      try {
        Vault.decodeRecoveryKey(bad.getString(key))
        fail("accepted a bad recovery key: $key")
      } catch (e: IllegalArgumentException) {
        assertTrue(e.message!!.isNotEmpty())
      }
    }
  }

  @Test
  fun opensEveryBlobThePcWrote() {
    val chunks = vectors.getJSONArray("chunks")
    for (i in 0 until chunks.length()) {
      val c = chunks.getJSONObject(i)
      val data = Vault.openChunk(master, c.getString("name"), Vault.unhex(c.getString("blob")))
      assertEquals(c.getString("label"), c.getString("data"), Vault.hex(data))
    }
  }

  @Test
  fun writesTheSameBlobsAsThePc() {
    val chunks = vectors.getJSONArray("chunks")
    for (i in 0 until chunks.length()) {
      val c = chunks.getJSONObject(i)
      val data = Vault.unhex(c.getString("data"))
      val (name, blob) = Vault.sealChunk(master, data)
      assertEquals(c.getString("label"), c.getBoolean("compressed"), blob[4].toInt() == 1)
      if (!c.getBoolean("compressed")) {  // compressed bytes may differ between zlib builds
        assertEquals(c.getString("label"), c.getString("name"), name)
        assertEquals(c.getString("label"), c.getString("blob"), Vault.hex(blob))
      }
      assertArrayEquals(data, Vault.openChunk(master, name, blob))
      // identical content gives the identical blob (that is what makes later backups small)
      assertEquals(name, Vault.sealChunk(master, data).first)
    }
  }

  @Test
  fun sealsOnlyTheGivenLengthOfABuffer() {
    val buffer = ByteArray(4096) { (it % 251).toByte() }
    val (name, blob) = Vault.sealChunk(master, buffer, 1000)
    assertArrayEquals(buffer.copyOf(1000), Vault.openChunk(master, name, blob))
  }

  @Test
  fun snapshotsReadAndWriteLikeThePc() {
    val snap = vectors.getJSONObject("snapshot")
    val json = snap.getString("json")
    assertEquals(json, String(Vault.openSnapshot(master, Vault.unhex(snap.getString("raw"))), Charsets.UTF_8))
    val mine = Vault.sealSnapshot(master, json.toByteArray(Charsets.UTF_8))
    assertEquals(json, String(Vault.openSnapshot(master, mine), Charsets.UTF_8))
    assertFalse(mine.contentEquals(Vault.sealSnapshot(master, json.toByteArray(Charsets.UTF_8))))
  }

  @Test
  fun damageAndWrongKeysAreNoticed() {
    val c = vectors.getJSONArray("chunks").getJSONObject(1)
    val blob = Vault.unhex(c.getString("blob"))
    val other = ByteArray(32) { 7 }
    val flipped = blob.clone().also { it[it.size - 1] = (it[it.size - 1].toInt() xor 1).toByte() }
    for (attempt in listOf(
      { Vault.openChunk(master, c.getString("name"), flipped) },           // does not match its name
      { Vault.openChunk(master, Vault.hex(Vault.sha256(flipped)), flipped) },  // renamed to match: fails authentication
      { Vault.openChunk(other, c.getString("name"), blob) },               // another key
      { Vault.openSnapshot(other, Vault.unhex(vectors.getJSONObject("snapshot").getString("raw"))) },
      { Vault.openSnapshot(master, ByteArray(40)) }
    )) {
      try {
        attempt()
        fail("damage went unnoticed")
      } catch (e: IntegrityException) {
        assertTrue(e.message!!.isNotEmpty())
      }
    }
  }

  @Test
  fun requestSignatureMatchesTheServer() {
    val s = vectors.getJSONObject("signature")
    assertEquals(s.getString("body_sha256"), Vault.hex(Vault.sha256(s.getString("body").toByteArray())))
    val message = listOf(s.getString("method"), s.getString("target"), s.getLong("time").toString(),
                         s.getString("body_sha256")).joinToString("\n")
    assertEquals(s.getString("sign"), Vault.hex(Vault.hmac(Vault.unhex(s.getString("secret")), message.toByteArray())))
  }
}
