/**
 * The Node stand-in for the native module must speak the same format as the PC,
 * or the engine tests would prove nothing. Same vectors as the Kotlin and Swift tests.
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { nodeCore, openChunkBytes, openSnapshotBytes, sealChunkBytes, sealSnapshotBytes } from './node-platform.ts';

const vectors = JSON.parse(fs.readFileSync(
  path.join(import.meta.dirname, '..', 'modules', 'tuckvault-core', 'test-vectors.json'), 'utf8'));
const master = Buffer.from(vectors.master, 'hex');

test('key check and recovery key match the PC', () => {
  assert.equal(nodeCore.keyCheck(vectors.master), vectors.key_check);
  assert.equal(nodeCore.encodeRecoveryKey(vectors.master), vectors.recovery_key);
  assert.equal(nodeCore.decodeRecoveryKey(vectors.recovery_key.toLowerCase().replaceAll('-', ' ')), vectors.master);
  for (const bad of Object.values(vectors.bad_recovery_keys) as string[]) {
    assert.throws(() => nodeCore.decodeRecoveryKey(bad));
  }
});

test('blobs read and write like the PC', () => {
  for (const c of vectors.chunks) {
    const data = Buffer.from(c.data, 'hex');
    assert.equal(openChunkBytes(master, c.name, Buffer.from(c.blob, 'hex')).toString('hex'), c.data, c.label);
    const mine = sealChunkBytes(master, data);
    assert.equal(mine.blob[4] === 1, c.compressed, c.label);
    if (!c.compressed) assert.equal(mine.blob.toString('hex'), c.blob, c.label);
    assert.deepEqual(openChunkBytes(master, mine.name, mine.blob), data);
  }
});

test('snapshots read and write like the PC', () => {
  assert.equal(openSnapshotBytes(master, Buffer.from(vectors.snapshot.raw, 'hex')), vectors.snapshot.json);
  assert.equal(openSnapshotBytes(master, sealSnapshotBytes(master, vectors.snapshot.json)), vectors.snapshot.json);
});

test('request signature matches the server', () => {
  const s = vectors.signature;
  assert.equal(nodeCore.sha256Hex(s.body), s.body_sha256);
  assert.equal(nodeCore.hmacHex(s.secret, [s.method, s.target, String(s.time), s.body_sha256].join('\n')), s.sign);
});
