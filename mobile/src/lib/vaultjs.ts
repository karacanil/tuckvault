/**
 * The Tuckvault storage format in plain TypeScript.
 *
 * The app normally uses its native module (modules/tuckvault-core) for this. Where that
 * module cannot be loaded, which is the case inside Expo Go, this file does the same work.
 * It holds no phone code: the two heavy operations, SHA-256 over large data and AES-GCM,
 * are handed in as `Primitives`, so the phone can use its built-in ones and the tests can
 * run this file on a computer against the PC program's own test vectors.
 */

import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';
import { unzlibSync, zlibSync } from 'fflate';

export type Primitives = {
  /** SHA-256 of a (possibly large) piece of data. */
  sha256(data: Uint8Array): Promise<Uint8Array>;
  /** AES-256-GCM; returns the ciphertext followed by the 16-byte tag. */
  encrypt(key: Uint8Array, nonce: Uint8Array, aad: Uint8Array, data: Uint8Array): Promise<Uint8Array>;
  /** Takes the ciphertext followed by the tag; rejects when the data fails authentication. */
  decrypt(key: Uint8Array, nonce: Uint8Array, aad: Uint8Array, sealed: Uint8Array): Promise<Uint8Array>;
  random(bytes: number): Uint8Array;
};

const BLOB_MAGIC = ascii('SKB1');
const SNAP_MAGIC = ascii('SKS1');
const B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
const HEX = '0123456789abcdef';

function ascii(text: string): Uint8Array {
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i++) out[i] = text.charCodeAt(i);
  return out;
}

/** UTF-8 without relying on TextEncoder being there. */
export function utf8(text: string): Uint8Array {
  const out: number[] = [];
  for (let i = 0; i < text.length; i++) {
    let c = text.charCodeAt(i);
    if (c >= 0xd800 && c < 0xdc00 && i + 1 < text.length) {
      const low = text.charCodeAt(i + 1);
      if (low >= 0xdc00 && low < 0xe000) {
        c = 0x10000 + ((c - 0xd800) << 10) + (low - 0xdc00);
        i += 1;
      }
    }
    if (c >= 0xd800 && c < 0xe000) c = 0xfffd; // half of a pair on its own
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  return Uint8Array.from(out);
}

export function fromUtf8(bytes: Uint8Array): string {
  const parts: string[] = [];
  let piece: number[] = [];
  for (let i = 0; i < bytes.length; ) {
    const b = bytes[i];
    let c = b;
    let more = 0;
    if (b >= 0xf0) { c = b & 7; more = 3; }
    else if (b >= 0xe0) { c = b & 15; more = 2; }
    else if (b >= 0xc0) { c = b & 31; more = 1; }
    i += 1;
    for (; more > 0 && i < bytes.length; more--, i++) c = (c << 6) | (bytes[i] & 63);
    if (c >= 0x10000) {
      c -= 0x10000;
      piece.push(0xd800 + (c >> 10), 0xdc00 + (c & 0x3ff));
    } else {
      piece.push(c);
    }
    if (piece.length >= 8192) {
      parts.push(String.fromCharCode.apply(null, piece));
      piece = [];
    }
  }
  parts.push(String.fromCharCode.apply(null, piece));
  return parts.join('');
}

export function hex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i++) out += HEX[bytes[i] >> 4] + HEX[bytes[i] & 15];
  return out;
}

export function unhex(text: string): Uint8Array {
  if (text.length % 2 || /[^0-9a-fA-F]/.test(text)) throw new Error('Bad key.');
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(text.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function key(masterHex: string): Uint8Array {
  const master = unhex(masterHex);
  if (master.length !== 32) throw new Error('Bad key.');
  return master;
}

function concat(...parts: Uint8Array[]): Uint8Array {
  let length = 0;
  for (const p of parts) length += p.length;
  const out = new Uint8Array(length);
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

function same(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

/** Stored data failed its checks; carries the same code as the native module's error. */
function damage(text: string): Error {
  return Object.assign(new Error(text), { code: 'ERR_INTEGRITY' });
}

function derive(master: Uint8Array, label: string): Uint8Array {
  return hmac(sha256, master, ascii('safekeep/v1/' + label));
}

/** HMAC-SHA256 over large data, built from the fast SHA-256 that was handed in. */
async function bigHmac(p: Primitives, keyBytes: Uint8Array, ...message: Uint8Array[]): Promise<Uint8Array> {
  const inner = new Uint8Array(64).fill(0x36);
  const outer = new Uint8Array(64).fill(0x5c);
  for (let i = 0; i < keyBytes.length; i++) { // the keys here are 32 bytes, never longer than a block
    inner[i] ^= keyBytes[i];
    outer[i] ^= keyBytes[i];
  }
  return p.sha256(concat(outer, await p.sha256(concat(inner, ...message))));
}

export const keyCheck = (masterHex: string): string => hex(derive(key(masterHex), 'key-check'));
export const hmacHex = (keyHex: string, message: string): string => hex(hmac(sha256, unhex(keyHex), utf8(message)));
export const sha256Hex = (text: string): string => hex(sha256(utf8(text)));

export function encodeRecoveryKey(masterHex: string): string {
  const master = key(masterHex);
  const raw = concat(master, sha256(master).subarray(0, 3));
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
  const raw = new Uint8Array(35);
  for (let i = 0; i < 35; i++) raw[i] = parseInt(bits.slice(i * 8, i * 8 + 8), 2);
  if (!same(sha256(raw.subarray(0, 32)).subarray(0, 3), raw.subarray(32))) {
    throw new Error('That recovery key has a typo in it (checksum mismatch).');
  }
  return hex(raw.subarray(0, 32));
}

export async function sealChunk(p: Primitives, master: Uint8Array, data: Uint8Array): Promise<{ name: string; blob: Uint8Array }> {
  let flag = 0;
  let body = data;
  if (data.length >= 512) {
    const sample = data.subarray(0, 65536);
    if (zlibSync(sample, { level: 1 }).length < 0.9 * sample.length) {
      const packed = zlibSync(data, { level: 3 });
      if (packed.length < data.length) {
        flag = 1;
        body = packed;
      }
    }
  }
  const header = concat(BLOB_MAGIC, Uint8Array.of(flag));
  const nonce = (await bigHmac(p, derive(master, 'chunk-nonce'), header, body)).subarray(0, 12);
  const sealed = await p.encrypt(derive(master, 'chunk-encryption'), nonce, header, body);
  const blob = concat(header, nonce, sealed);
  return { name: hex(await p.sha256(blob)), blob };
}

export async function openChunk(p: Primitives, master: Uint8Array, name: string, blob: Uint8Array): Promise<Uint8Array> {
  if (hex(await p.sha256(blob)) !== name) throw damage('blob does not match its hash');
  if (blob.length < 33 || !same(blob.subarray(0, 4), BLOB_MAGIC)) throw damage('not a Tuckvault blob');
  const header = blob.subarray(0, 5);
  let body: Uint8Array;
  try {
    body = await p.decrypt(derive(master, 'chunk-encryption'), blob.subarray(5, 17), header, blob.subarray(17));
  } catch {
    throw damage('blob failed authentication');
  }
  if (header[4] !== 1) return body;
  try {
    return unzlibSync(body);
  } catch {
    throw damage('blob could not be unpacked');
  }
}

export async function sealSnapshot(p: Primitives, master: Uint8Array, json: string): Promise<Uint8Array> {
  const nonce = p.random(12);
  const sealed = await p.encrypt(derive(master, 'snapshot-encryption'), nonce, SNAP_MAGIC, zlibSync(utf8(json), { level: 6 }));
  return concat(SNAP_MAGIC, nonce, sealed);
}

export async function openSnapshot(p: Primitives, master: Uint8Array, raw: Uint8Array): Promise<string> {
  if (raw.length < 32 || !same(raw.subarray(0, 4), SNAP_MAGIC)) throw damage('not a Tuckvault snapshot');
  let body: Uint8Array;
  try {
    body = await p.decrypt(derive(master, 'snapshot-encryption'), raw.subarray(4, 16), SNAP_MAGIC, raw.subarray(16));
  } catch {
    throw damage('snapshot failed authentication');
  }
  try {
    return fromUtf8(unzlibSync(body));
  } catch {
    throw damage('snapshot could not be unpacked');
  }
}

export const sha256HexOf = async (p: Primitives, data: Uint8Array): Promise<string> => hex(await p.sha256(data));
