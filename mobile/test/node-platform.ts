/**
 * The device, played by Node: the same storage format written with node:crypto
 * and files on disk. It lets the real backup engine run on a computer against
 * the real `tuckvault serve`, and is itself checked against the shared test
 * vectors (see vectors.test.ts).
 */

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import type { Core, HttpReply, Platform } from '../src/lib/platform.ts';

const BLOB_MAGIC = Buffer.from('SKB1');
const SNAP_MAGIC = Buffer.from('SKS1');
const B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function derive(master: Buffer, label: string): Buffer {
  return crypto.createHmac('sha256', master).update('safekeep/v1/' + label).digest();
}

const sha256 = (data: Buffer | string) => crypto.createHash('sha256').update(data).digest();

function local(uri: string): string {
  return uri.startsWith('file://') ? decodeURIComponent(new URL(uri).pathname).replace(/^\/([A-Za-z]:)/, '$1') : uri;
}

export function sealChunkBytes(master: Buffer, data: Buffer): { name: string; blob: Buffer } {
  let flag = 0;
  let body = data;
  if (data.length >= 512) {
    const sample = data.subarray(0, 65536);
    if (zlib.deflateSync(sample, { level: 1 }).length < 0.9 * sample.length) {
      const comp = zlib.deflateSync(data, { level: 3 });
      if (comp.length < data.length) {
        flag = 1;
        body = comp;
      }
    }
  }
  const header = Buffer.concat([BLOB_MAGIC, Buffer.from([flag])]);
  const nonce = crypto.createHmac('sha256', derive(master, 'chunk-nonce')).update(header).update(body).digest().subarray(0, 12);
  const cipher = crypto.createCipheriv('aes-256-gcm', derive(master, 'chunk-encryption'), nonce);
  cipher.setAAD(header);
  const blob = Buffer.concat([header, nonce, cipher.update(body), cipher.final(), cipher.getAuthTag()]);
  return { name: sha256(blob).toString('hex'), blob };
}

export function openChunkBytes(master: Buffer, name: string, blob: Buffer): Buffer {
  if (sha256(blob).toString('hex') !== name) throw new Error('blob does not match its hash');
  if (blob.length < 33 || !blob.subarray(0, 4).equals(BLOB_MAGIC)) throw new Error('not a Tuckvault blob');
  const header = blob.subarray(0, 5);
  const decipher = crypto.createDecipheriv('aes-256-gcm', derive(master, 'chunk-encryption'), blob.subarray(5, 17));
  decipher.setAAD(header);
  decipher.setAuthTag(blob.subarray(blob.length - 16));
  const body = Buffer.concat([decipher.update(blob.subarray(17, blob.length - 16)), decipher.final()]);
  return header[4] === 1 ? zlib.inflateSync(body) : body;
}

export function sealSnapshotBytes(master: Buffer, json: string, nonce = crypto.randomBytes(12)): Buffer {
  const cipher = crypto.createCipheriv('aes-256-gcm', derive(master, 'snapshot-encryption'), nonce);
  cipher.setAAD(SNAP_MAGIC);
  const body = zlib.deflateSync(Buffer.from(json, 'utf8'), { level: 6 });
  return Buffer.concat([SNAP_MAGIC, nonce, cipher.update(body), cipher.final(), cipher.getAuthTag()]);
}

export function openSnapshotBytes(master: Buffer, raw: Buffer): string {
  if (raw.length < 32 || !raw.subarray(0, 4).equals(SNAP_MAGIC)) throw new Error('not a Tuckvault snapshot');
  const decipher = crypto.createDecipheriv('aes-256-gcm', derive(master, 'snapshot-encryption'), raw.subarray(4, 16));
  decipher.setAAD(SNAP_MAGIC);
  decipher.setAuthTag(raw.subarray(raw.length - 16));
  return zlib.inflateSync(Buffer.concat([decipher.update(raw.subarray(16, raw.length - 16)), decipher.final()])).toString('utf8');
}

export function encodeRecoveryKey(masterHex: string): string {
  const master = Buffer.from(masterHex, 'hex');
  const raw = Buffer.concat([master, sha256(master).subarray(0, 3)]);
  let bits = '';
  for (const byte of raw) bits += byte.toString(2).padStart(8, '0');
  let text = '';
  for (let i = 0; i < bits.length; i += 5) text += B32[parseInt(bits.slice(i, i + 5).padEnd(5, '0'), 2)];
  return text.match(/.{1,4}/g)!.join('-');
}

export function decodeRecoveryKey(text: string): string {
  const clean = text.toUpperCase().replace(/[^A-Z0-9]/g, '');
  let bits = '';
  for (const c of clean) {
    const v = B32.indexOf(c);
    if (v < 0) throw new Error('That is not a valid recovery key.');
    bits += v.toString(2).padStart(5, '0');
  }
  if (clean.length !== 56) throw new Error('That is not a valid recovery key.');
  const raw = Buffer.alloc(35);
  for (let i = 0; i < 35; i++) raw[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  if (!sha256(raw.subarray(0, 32)).subarray(0, 3).equals(raw.subarray(32))) {
    throw new Error('That recovery key has a typo in it (checksum mismatch).');
  }
  return raw.subarray(0, 32).toString('hex');
}

export const nodeCore: Core = {
  newMasterKey: () => crypto.randomBytes(32).toString('hex'),
  randomHex: (bytes) => crypto.randomBytes(bytes).toString('hex'),
  keyCheck: (master) => derive(Buffer.from(master, 'hex'), 'key-check').toString('hex'),
  hmacHex: (keyHex, message) => crypto.createHmac('sha256', Buffer.from(keyHex, 'hex')).update(message, 'utf8').digest('hex'),
  sha256Hex: (text) => sha256(Buffer.from(text, 'utf8')).toString('hex'),
  encodeRecoveryKey,
  decodeRecoveryKey,
  async statFile(uri) {
    const st = fs.statSync(local(uri));
    return { size: st.size, mtime: Math.round(st.mtimeMs), name: path.basename(local(uri)) };
  },
  async sealChunk(master, uri, offset, length, outPath) {
    const fd = fs.openSync(local(uri), 'r');
    try {
      const buf = Buffer.alloc(length);
      const read = fs.readSync(fd, buf, 0, length, offset);
      if (!read) return { read: 0, name: null, size: 0 };
      const { name, blob } = sealChunkBytes(Buffer.from(master, 'hex'), buf.subarray(0, read));
      fs.writeFileSync(local(outPath), blob);
      return { read, name, size: blob.length };
    } finally {
      fs.closeSync(fd);
    }
  },
  async openChunk(master, name, blobPath, outPath, append) {
    const data = openChunkBytes(Buffer.from(master, 'hex'), name, fs.readFileSync(local(blobPath)));
    if (append) fs.appendFileSync(local(outPath), data);
    else fs.writeFileSync(local(outPath), data);
    return data.length;
  },
  async sealSnapshot(master, json, outPath) {
    const raw = sealSnapshotBytes(Buffer.from(master, 'hex'), json);
    fs.writeFileSync(local(outPath), raw);
    return { size: raw.length, sha256: sha256(raw).toString('hex') };
  },
  async openSnapshot(master, p) {
    return openSnapshotBytes(Buffer.from(master, 'hex'), fs.readFileSync(local(p)));
  },
};

export type NodePlatform = Platform & {
  /** Seconds added to the clock (to play a phone whose clock is wrong). */
  skewMs: number;
  /** Called before every upload; throw to play a dropped connection. */
  beforeUpload: ((url: string) => void) | null;
  uploads: number;
};

export function nodePlatform(tmpDir: string): NodePlatform {
  fs.mkdirSync(tmpDir, { recursive: true });
  const reply = async (res: Response): Promise<HttpReply> => ({ status: res.status, text: await res.text() });
  const platform: NodePlatform = {
    core: nodeCore,
    skewMs: 0,
    beforeUpload: null,
    uploads: 0,
    request: async (method, url, headers, body) => reply(await fetch(url, { method, headers, body })),
    async upload(url, headers, file) {
      if (platform.beforeUpload) platform.beforeUpload(url);
      platform.uploads += 1;
      return reply(await fetch(url, { method: 'PUT', headers, body: fs.readFileSync(file) }));
    },
    async download(url, headers, file) {
      const res = await fetch(url, { headers });
      if (res.status !== 200) return reply(res);
      fs.writeFileSync(file, Buffer.from(await res.arrayBuffer()));
      return { status: 200, text: '' };
    },
    tmp: (name) => path.join(tmpDir, name),
    remove: (file) => fs.rmSync(file, { force: true }),
    readText: (file) => (fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null),
    writeText(file, text) {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(file, text);
    },
    now: () => Date.now() + platform.skewMs,
  };
  return platform;
}
