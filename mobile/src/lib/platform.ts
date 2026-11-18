/**
 * Everything the backup engine needs from the device, as plain interfaces.
 * The app supplies the real thing (native module, expo-file-system); the tests
 * supply a Node version, so the engine itself is tested against the real
 * `tuckvault serve` on a computer without any phone.
 */

export type FileStat = { size: number; mtime: number | null; name: string | null };
export type SealedChunk = { read: number; name: string | null; size: number };

/** The native module (modules/tuckvault-core). Keys are 64 hex characters. */
export interface Core {
  newMasterKey(): string;
  randomHex(bytes: number): string;
  keyCheck(master: string): string;
  hmacHex(keyHex: string, message: string): string;
  sha256Hex(text: string): string;
  encodeRecoveryKey(master: string): string;
  decodeRecoveryKey(text: string): string;
  statFile(uri: string): Promise<FileStat>;
  sealChunk(master: string, uri: string, offset: number, length: number, outPath: string): Promise<SealedChunk>;
  openChunk(master: string, name: string, blobPath: string, outPath: string, append: boolean): Promise<number>;
  sealSnapshot(master: string, json: string, outPath: string): Promise<{ size: number; sha256: string }>;
  openSnapshot(master: string, path: string): Promise<string>;
}

export type HttpReply = { status: number; text: string };

export interface Platform {
  core: Core;
  /** A small request with an optional text body. Rejects only when the computer cannot be reached. */
  request(method: string, url: string, headers: Record<string, string>, body?: string): Promise<HttpReply>;
  /** PUT a file as the request body. */
  upload(url: string, headers: Record<string, string>, path: string): Promise<HttpReply>;
  /** GET into a file. `text` carries the body only when the status is not 200. */
  download(url: string, headers: Record<string, string>, path: string): Promise<HttpReply>;
  /** A path for a temporary file with this name (the folder exists). */
  tmp(name: string): string;
  remove(path: string): void;
  readText(path: string): string | null;
  writeText(path: string, text: string): void;
  /** Milliseconds since 1970. */
  now(): number;
}
