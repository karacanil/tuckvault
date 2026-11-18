/**
 * The app's native module, done with what every Expo app has: the storage format from
 * vaultjs.ts, the phone's own SHA-256 and AES-GCM through expo-crypto, and files through
 * expo-file-system. It is used where modules/tuckvault-core cannot be loaded, which is
 * the case when the app runs inside Expo Go (see native.ts).
 */

import {
  AESEncryptionKey, AESSealedData, CryptoDigestAlgorithm, aesDecryptAsync, aesEncryptAsync, digest, getRandomBytes,
} from 'expo-crypto';
import { Directory, File } from 'expo-file-system';
import * as Network from 'expo-network';

import type { FileStat, SealedChunk } from './platform';
import * as vault from './vaultjs';

export type TreeEntry = { uri: string; path: string; size: number | null; mtime: number | null };

/** A view into a larger buffer is copied first: what is handed to the phone's own code is always exactly the data. */
function own(bytes: Uint8Array): Uint8Array<ArrayBuffer> {
  if (bytes.byteOffset === 0 && bytes.byteLength === bytes.buffer.byteLength && bytes.buffer instanceof ArrayBuffer) {
    return bytes as Uint8Array<ArrayBuffer>;
  }
  return Uint8Array.from(bytes);
}

const keys = new Map<string, Promise<AESEncryptionKey>>();

function aesKey(raw: Uint8Array): Promise<AESEncryptionKey> {
  const id = vault.hex(raw);
  let got = keys.get(id);
  if (!got) {
    got = AESEncryptionKey.import(own(raw));
    keys.set(id, got);
    got.catch(() => keys.delete(id));
  }
  return got;
}

const primitives: vault.Primitives = {
  sha256: async (data) => new Uint8Array(await digest(CryptoDigestAlgorithm.SHA256, own(data))),
  async encrypt(key, nonce, aad, data) {
    const sealed = await aesEncryptAsync(own(data), await aesKey(key), { nonce: { bytes: own(nonce) }, additionalData: own(aad) });
    return (await sealed.ciphertext({ includeTag: true, encoding: 'bytes' })) as Uint8Array;
  },
  async decrypt(key, nonce, aad, sealed) {
    const data = AESSealedData.fromParts(own(nonce), own(sealed), 16);
    return (await aesDecryptAsync(data, await aesKey(key), { additionalData: own(aad), output: 'bytes' })) as Uint8Array;
  },
  random: (bytes) => getRandomBytes(bytes),
};

// One piece of data as the PC program stores it (from the shared test vectors).
const CHECK = {
  master: '000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f',
  data: '78',
  name: '9321c16d3d165a53776635cef6043bd6c57594c06fc0b8edfa61da49d79f7a17',
  blob: '534b423100cb67e96618e2dc72d29960ced99c607b9b8dfbac988ede000bbfe2ed94',
};

let checked: Promise<void> | null = null;

/**
 * Before anything is stored or restored, make sure this phone's encryption produces exactly
 * what the PC program does. If it ever did not, backups would be written that nothing can
 * read, so then nothing is written at all.
 */
function ready(): Promise<void> {
  if (!checked) {
    checked = (async () => {
      const master = vault.key(CHECK.master);
      const sealed = await vault.sealChunk(primitives, master, vault.unhex(CHECK.data));
      const opened = await vault.openChunk(primitives, master, CHECK.name, vault.unhex(CHECK.blob));
      if (sealed.name !== CHECK.name || vault.hex(sealed.blob) !== CHECK.blob || vault.hex(opened) !== CHECK.data) {
        throw Object.assign(new Error('This phone\'s encryption does not match Tuckvault\'s format, so nothing was stored.'),
                            { code: 'ERR_SELFTEST' });
      }
    })();
    checked.catch(() => { checked = null; });
  }
  return checked;
}

function gone(name: string): Error {
  return Object.assign(new Error(name + ' is gone'), { code: 'ERR_NOT_FOUND' });
}

function put(path: string, bytes: Uint8Array): void {
  const file = new File(path);
  if (file.exists) file.delete();
  file.create({ intermediates: true });
  file.writeSync(own(bytes));
}

function walk(dir: Directory, prefix: string, out: TreeEntry[]): void {
  for (const entry of dir.list()) { // throws when a folder cannot be listed: it must not look empty
    if (entry instanceof Directory) {
      walk(entry, prefix + entry.name + '/', out);
    } else {
      const info = entry.info();
      out.push({ uri: entry.uri, path: prefix + entry.name, size: info.size ?? entry.size ?? 0,
                 mtime: info.modificationTime != null ? Math.round(info.modificationTime) : null });
    }
  }
}

const JsCore = {
  /** Lets the screens say that this is the Expo Go version. */
  plainJs: true,

  newMasterKey: (): string => vault.hex(getRandomBytes(32)),
  randomHex: (bytes: number): string => vault.hex(getRandomBytes(bytes)),
  keyCheck: vault.keyCheck,
  hmacHex: vault.hmacHex,
  sha256Hex: vault.sha256Hex,
  encodeRecoveryKey: vault.encodeRecoveryKey,
  decodeRecoveryKey(text: string): string {
    try {
      return vault.decodeRecoveryKey(text);
    } catch (e) {
      throw Object.assign(new Error((e as Error).message), { code: 'ERR_RECOVERY_KEY' });
    }
  },

  /** True while connected to Wi-Fi, also underneath a VPN such as Tailscale, as far as the phone tells. */
  async onWifi(): Promise<boolean> {
    try {
      const state = await Network.getNetworkStateAsync();
      if (state.type === Network.NetworkStateType.WIFI) return true;
      if (state.type !== Network.NetworkStateType.VPN) return false;
      // Behind a VPN the phone does not say what carries it. A Wi-Fi address means Wi-Fi is up.
      const ip = await Network.getIpAddressAsync();
      return /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(ip);
    } catch {
      return false;
    }
  },

  async statFile(uri: string): Promise<FileStat> {
    const file = new File(uri);
    if (!file.exists) throw gone(file.name);
    const info = file.info();
    return { size: info.size ?? file.size ?? 0,
             mtime: info.modificationTime != null ? Math.round(info.modificationTime) : null, name: file.name };
  },

  async listTree(uri: string): Promise<TreeEntry[]> {
    const root = new Directory(uri);
    if (!root.exists) throw gone(root.name);
    const out: TreeEntry[] = [];
    walk(root, '', out);
    return out;
  },

  async sealChunk(master: string, uri: string, offset: number, length: number, outPath: string): Promise<SealedChunk> {
    await ready();
    const handle = new File(uri).open();
    let data: Uint8Array;
    try {
      handle.offset = Math.max(offset, 0);
      data = await handle.readBytes(length);
    } finally {
      handle.close();
    }
    if (!data.length) return { read: 0, name: null, size: 0 };
    const sealed = await vault.sealChunk(primitives, vault.key(master), data);
    put(outPath, sealed.blob);
    return { read: data.length, name: sealed.name, size: sealed.blob.length };
  },

  async openChunk(master: string, name: string, blobPath: string, outPath: string, append: boolean): Promise<number> {
    await ready();
    const data = await vault.openChunk(primitives, vault.key(master), name, await new File(blobPath).bytes());
    const out = new File(outPath);
    if (append && out.exists) {
      const handle = out.open();
      try {
        handle.offset = handle.size ?? 0;
        handle.writeBytesSync(own(data));
      } finally {
        handle.close();
      }
    } else {
      put(outPath, data);
    }
    return data.length;
  },

  async sealSnapshot(master: string, json: string, outPath: string): Promise<{ size: number; sha256: string }> {
    await ready();
    const raw = await vault.sealSnapshot(primitives, vault.key(master), json);
    put(outPath, raw);
    return { size: raw.length, sha256: await vault.sha256HexOf(primitives, raw) };
  },

  async openSnapshot(master: string, path: string): Promise<string> {
    await ready();
    return vault.openSnapshot(primitives, vault.key(master), await new File(path).bytes());
  },

  async sha256File(path: string): Promise<string> {
    return vault.sha256HexOf(primitives, await new File(path).bytes());
  },
};

export default JsCore;
