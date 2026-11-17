import { NativeModule, requireNativeModule } from 'expo';

export type FileStat = { size: number; mtime: number | null; name: string | null };
export type TreeEntry = { uri: string; path: string; size: number | null; mtime: number | null };
export type SealedChunk = { read: number; name: string | null; size: number };

/**
 * The native half of Tuckvault: the storage format and file reading.
 * Keys are passed as 64 hex characters. Paths may be plain paths or file:// addresses;
 * `uri` arguments may also be content:// addresses on Android.
 */
export declare class TuckvaultCoreNative extends NativeModule {
  newMasterKey(): string;
  randomHex(bytes: number): string;
  keyCheck(master: string): string;
  hmacHex(keyHex: string, message: string): string;
  sha256Hex(text: string): string;
  encodeRecoveryKey(master: string): string;
  /** Throws with a message fit to show the user. */
  decodeRecoveryKey(text: string): string;
  /** True while connected to Wi-Fi, also underneath a VPN such as Tailscale. */
  onWifi(): boolean;

  statFile(uri: string): Promise<FileStat>;
  /** Every file below a folder, paths relative to it with "/". */
  listTree(uri: string): Promise<TreeEntry[]>;
  /** Reads up to `length` bytes at `offset`, encrypts them, writes the blob to `outPath`. read 0 = end of file. */
  sealChunk(master: string, uri: string, offset: number, length: number, outPath: string): Promise<SealedChunk>;
  /** Checks and decrypts a downloaded blob into (or onto the end of) `outPath`. Returns the plain length. */
  openChunk(master: string, name: string, blobPath: string, outPath: string, append: boolean): Promise<number>;
  sealSnapshot(master: string, json: string, outPath: string): Promise<{ size: number; sha256: string }>;
  openSnapshot(master: string, path: string): Promise<string>;
  sha256File(path: string): Promise<string>;
}

export default requireNativeModule<TuckvaultCoreNative>('TuckvaultCore');
