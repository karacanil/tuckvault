/**
 * The plain TypeScript storage code (used where the native module cannot be loaded, as in
 * Expo Go) against the PC's vectors, and against the Node stand-in in both directions.
 */

import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import * as vault from '../src/lib/vaultjs.ts';
import { nodeCore, openChunkBytes, openSnapshotBytes, sealChunkBytes, sealSnapshotBytes } from './node-platform.ts';

const vectors = JSON.parse(fs.readFileSync(
  path.join(import.meta.dirname, '..', 'modules', 'tuckvault-core', 'test-vectors.json'), 'utf8'));
const master = vault.key(vectors.master);
const hex = (bytes: Uint8Array) => Buffer.from(bytes).toString('hex');

/** What the phone supplies from its own libraries, here from Node. */
const primitives: vault.Primitives = {
  sha256: async (data) => crypto.createHash('sha256').update(data).digest(),
  async encrypt(key, nonce, aad, data) {
    const cipher = crypto.createCipheriv('aes-256-gcm', key, nonce);
    cipher.setAAD(aad);
    return Buffer.concat([cipher.update(data), cipher.final(), cipher.getAuthTag()]);
  },
  async decrypt(key, nonce, aad, sealed) {
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, nonce);
    decipher.setAAD(aad);
    decipher.setAuthTag(sealed.subarray(sealed.length - 16));
    return Buffer.concat([decipher.update(sealed.subarray(0, sealed.length - 16)), decipher.final()]);
  },
  random: (bytes) => crypto.randomBytes(bytes),
};

test('plain TypeScript: key check, recovery key and signature match the PC', () => {
  assert.equal(vault.keyCheck(vectors.master), vectors.key_check);
  assert.equal(vault.encodeRecoveryKey(vectors.master), vectors.recovery_key);
  assert.equal(vault.decodeRecoveryKey(vectors.recovery_key.toLowerCase().replaceAll('-', ' ')), vectors.master);
  for (const bad of Object.values(vectors.bad_recovery_keys) as string[]) {
    assert.throws(() => vault.decodeRecoveryKey(bad));
  }
  const s = vectors.signature;
  assert.equal(vault.sha256Hex(s.body), s.body_sha256);
  assert.equal(vault.hmacHex(s.secret, [s.method, s.target, String(s.time), s.body_sha256].join('\n')), s.sign);
});

test('plain TypeScript: blobs read and write like the PC', async () => {
  for (const c of vectors.chunks) {
    const data = Buffer.from(c.data, 'hex');
    assert.equal(hex(await vault.openChunk(primitives, master, c.name, Buffer.from(c.blob, 'hex'))), c.data, c.label);
    const mine = await vault.sealChunk(primitives, master, data);
    assert.equal(mine.blob[4] === 1, c.compressed, c.label);
    if (!c.compressed) assert.equal(hex(mine.blob), c.blob, c.label);
    // what it writes, the other implementation reads, and the other way round
    assert.equal(openChunkBytes(Buffer.from(master), mine.name, Buffer.from(mine.blob)).toString('hex'), c.data, c.label);
    const theirs = sealChunkBytes(Buffer.from(master), data);
    assert.equal(hex(await vault.openChunk(primitives, master, theirs.name, theirs.blob)), c.data, c.label);
  }
});

test('plain TypeScript: a large piece that packs well, and one that does not', async () => {
  const text = Buffer.from('Tuckvault keeps what matters. '.repeat(150_000)); // 4.5 MB of text
  const noise = crypto.randomBytes(4 * 1024 * 1024);
  for (const data of [text, noise]) {
    const mine = await vault.sealChunk(primitives, master, data);
    assert.equal(mine.blob[4] === 1, data === text);
    assert.ok(openChunkBytes(Buffer.from(master), mine.name, Buffer.from(mine.blob)).equals(data));
    assert.ok(Buffer.from(await vault.openChunk(primitives, master, mine.name, mine.blob)).equals(data));
  }
});

test('plain TypeScript: damage is noticed', async () => {
  const mine = await vault.sealChunk(primitives, master, Buffer.from('some bytes worth keeping'));
  const broken = Buffer.from(mine.blob);
  broken[20] ^= 1;
  await assert.rejects(vault.openChunk(primitives, master, mine.name, broken), { code: 'ERR_INTEGRITY' });
  const renamed = crypto.createHash('sha256').update(broken).digest('hex');
  await assert.rejects(vault.openChunk(primitives, master, renamed, broken), { code: 'ERR_INTEGRITY' });
  const other = vault.key(nodeCore.newMasterKey());
  await assert.rejects(vault.openChunk(primitives, other, mine.name, mine.blob), { code: 'ERR_INTEGRITY' });
});

test('plain TypeScript: snapshots read and write like the PC', async () => {
  assert.equal(await vault.openSnapshot(primitives, master, Buffer.from(vectors.snapshot.raw, 'hex')), vectors.snapshot.json);
  const json = JSON.stringify({ name: 'Fotoğraflar 📷 – été', files: [{ path: 'a/ü.jpg' }] });
  assert.equal(openSnapshotBytes(Buffer.from(master), Buffer.from(await vault.sealSnapshot(primitives, master, json))), json);
  assert.equal(await vault.openSnapshot(primitives, master, sealSnapshotBytes(Buffer.from(master), json)), json);
});

test('plain TypeScript: text encoding matches the standard one', () => {
  for (const text of ['', 'plain', 'Fotoğraflar', '日本語', '📷 and 🗝️', 'x'.repeat(20000) + 'é']) {
    assert.equal(hex(vault.utf8(text)), Buffer.from(text, 'utf8').toString('hex'));
    assert.equal(vault.fromUtf8(Buffer.from(text, 'utf8')), text);
  }
});
