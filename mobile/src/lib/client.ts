import type { HttpReply, Platform } from './platform.ts';

/** A problem worth showing to the user as it is. `code` says what kind. */
export class BackupError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
    this.name = 'BackupError';
  }
}

export const UNREACHABLE =
  'Cannot reach your computer. Check that Tailscale is on, on this phone and on the computer, ' +
  'and that the computer is switched on.';

export type Server = { url: string; device: string; secret: string; name?: string };
export type RepoMeta = { format: number; id: string; created: string; key_check: string };
export type OpenInfo = { meta: RepoMeta | null; free: number | null; total: number | null };
export type Verdict = { missing: string[]; corrupt: string[]; unreadable: string[] };

const BLOB = /^[0-9a-f]{64}$/;
const SNAP = /^[0-9A-Za-z][0-9A-Za-z._-]{0,95}\.snap$/;
const INVALID = 'Your computer sent an invalid reply.';

/** "100.64.1.2", "pc.tail1234.ts.net:8743" or a full address -> "http://host:port" without a path. */
export function normalizeAddress(input: string): string {
  let text = input.trim();
  if (!text) throw new BackupError('address', 'Enter the address shown on your computer.');
  if (!/^https?:\/\//i.test(text)) text = 'http://' + text;
  const m = /^(https?):\/\/([^/?#\s]+)/i.exec(text);
  if (!m || m[2].includes('@')) throw new BackupError('address', 'That does not look like an address.');
  const scheme = m[1].toLowerCase();
  let host = m[2].toLowerCase();
  if (scheme === 'http' && !/:\d+$/.test(host)) host += ':8743';
  return scheme + '://' + host;
}

export type ScannedPairing = { url: string; host: string; token: string };

/**
 * What a scanned pairing code carries: the link "…connect?u=<address>&c=<token>" the computer
 * shows as a square code. Returns null for anything that is not a complete, well-formed one,
 * so a half-made or tampered link just leaves the ordinary boxes to fill in.
 */
export function scannedPairing(u: unknown, c: unknown): ScannedPairing | null {
  if (typeof u !== 'string' || typeof c !== 'string') return null;
  if (u.length > 300 || !/^[A-Za-z0-9]{20,64}$/.test(c)) return null;
  try {
    const url = normalizeAddress(u);
    return { url, host: url.replace(/^https?:\/\//, '').replace(/:8743$/, ''), token: c };
  } catch {
    return null;
  }
}

/**
 * The same, read straight from a link the phone was opened with. The router does not always
 * hand a link's details to the screen (inside Expo Go on an iPhone it drops them), so the app
 * also looks at the link itself.
 */
export function pairingParams(link: unknown): { u: string; c: string } | null {
  if (typeof link !== 'string' || link.length > 1000) return null;
  const m = /\/connect\/?\?([^#]*)/.exec(link);
  if (!m) return null;
  const found: Record<string, string> = {};
  for (const part of m[1].split('&')) {
    const at = part.indexOf('=');
    if (at < 1) continue;
    try {
      found[part.slice(0, at)] = decodeURIComponent(part.slice(at + 1));
    } catch {
      return null;
    }
  }
  return scannedPairing(found.u, found.c) ? { u: found.u, c: found.c } : null;
}

function parse(reply: HttpReply): any {
  try {
    return JSON.parse(reply.text);
  } catch {
    return null;
  }
}

function failure(reply: HttpReply): BackupError {
  const body = parse(reply);
  if (body && typeof body.error === 'string' && typeof body.message === 'string') {
    return new BackupError(body.error, body.message.slice(0, 300));
  }
  return new BackupError('server', 'Your computer answered in a way Tuckvault does not understand (' +
    reply.status + '). Is the address right, and is Tuckvault on the computer up to date?');
}

async function reach<T>(work: () => Promise<T>): Promise<T> {
  try {
    return await work();
  } catch (e) {
    if (e instanceof BackupError) throw e;
    throw new BackupError('unreachable', UNREACHABLE);
  }
}

/** Who answers at this address? Rejects unless it is a Tuckvault service. */
export async function hello(platform: Platform, url: string): Promise<{ name: string; version: string }> {
  const reply = await reach(() => platform.request('GET', url + '/v1/hello', {}));
  const body = parse(reply);
  if (reply.status !== 200 || !body || body.app !== 'tuckvault') {
    throw new BackupError('not_tuckvault', 'Something answers at that address, but it is not Tuckvault. ' +
      'Check the address shown on your computer.');
  }
  return { name: String(body.name || 'your computer'), version: String(body.version || '') };
}

/** Trade the code shown on the computer for this phone's own secret. */
export async function pair(platform: Platform, url: string, code: string, deviceName: string,
                           os: string): Promise<Server> {
  const reply = await reach(() => platform.request('POST', url + '/v1/pair',
    { 'Content-Type': 'application/json' }, JSON.stringify({ code, name: deviceName, platform: os })));
  if (reply.status !== 200) throw failure(reply);
  const body = parse(reply);
  if (!body || !/^[0-9a-f]{12}$/.test(body.device) || !/^[0-9a-f]{64}$/.test(body.secret)) {
    throw new BackupError('server', 'Your computer sent an unexpected answer while pairing.');
  }
  return { url, device: body.device, secret: body.secret, name: String(body.name || '') };
}

/** One vault on the computer, addressed with signed requests. */
export class Client {
  platform: Platform;
  server: Server;
  base: string;

  constructor(platform: Platform, server: Server, vault: string) {
    if (!/^[0-9a-f]{24}$/.test(vault)) throw new Error('bad vault');
    this.platform = platform;
    this.server = server;
    this.base = '/v1/vaults/' + vault;
  }

  /** Headers that prove the request comes from this paired phone. bodyHash: SHA-256 of the body, hex. */
  headers(method: string, target: string, bodyHash: string): Record<string, string> {
    const when = String(Math.floor(this.platform.now() / 1000));
    const sign = this.platform.core.hmacHex(this.server.secret, [method, target, when, bodyHash].join('\n'));
    return { 'X-Tuckvault-Device': this.server.device, 'X-Tuckvault-Time': when, 'X-Tuckvault-Sign': sign };
  }

  private async json(method: string, target: string, body?: object): Promise<any> {
    const text = body === undefined ? '' : JSON.stringify(body);
    const headers = this.headers(method, target, this.platform.core.sha256Hex(text));
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const reply = await reach(() => this.platform.request(method, this.server.url + target, headers,
      body === undefined ? undefined : text));
    if (reply.status !== 200) throw failure(reply);
    const parsed = parse(reply);
    if (parsed === null || typeof parsed !== 'object') throw new BackupError('server', INVALID);
    return parsed;
  }

  async whoami(): Promise<{ name: string; version: string }> {
    const got = await this.json('GET', '/v1/whoami');
    return { name: String(got.name || ''), version: String(got.version || '') };
  }

  async open(create: boolean): Promise<OpenInfo> {
    const got = await this.json('POST', this.base + '/open', { create });
    const meta = got.meta;
    if (meta !== null && !(meta && typeof meta.id === 'string' && /^[0-9a-f]{16}$/.test(meta.id) &&
        typeof meta.key_check === 'string')) {
      throw new BackupError('server', INVALID);
    }
    return { meta, free: typeof got.free === 'number' ? got.free : null,
             total: typeof got.total === 'number' ? got.total : null };
  }

  async init(meta: RepoMeta): Promise<void> {
    await this.json('POST', this.base + '/init', { meta });
  }

  /** name -> stored size of every blob in the vault. */
  async listBlobs(): Promise<Map<string, number>> {
    const target = this.base + '/blobs';
    const reply = await reach(() => this.platform.request('GET', this.server.url + target,
      this.headers('GET', target, this.platform.core.sha256Hex(''))));
    if (reply.status !== 200) throw failure(reply);
    const out = new Map<string, number>();
    for (const line of reply.text.split('\n')) {
      if (!line) continue;
      const [name, size] = line.split(' ');
      if (!BLOB.test(name) || !/^\d+$/.test(size || '')) throw new BackupError('server', INVALID);
      out.set(name, Number(size));
    }
    return out;
  }

  /** Upload a sealed blob from a file. The blob's name is the SHA-256 of that file. */
  async putBlob(name: string, path: string): Promise<void> {
    const target = this.base + '/blobs/' + name;
    const reply = await reach(() => this.platform.upload(this.server.url + target,
      this.headers('PUT', target, name), path));
    if (reply.status !== 200) throw failure(reply);
  }

  async getBlob(name: string, path: string): Promise<void> {
    if (!BLOB.test(name)) throw new BackupError('server', 'Bad piece name in the backup.');
    const target = this.base + '/blobs/' + name;
    const reply = await reach(() => this.platform.download(this.server.url + target,
      this.headers('GET', target, this.platform.core.sha256Hex('')), path));
    if (reply.status !== 200) throw failure(reply);
  }

  async verify(names: string[], full: boolean): Promise<Verdict> {
    const got = await this.json('POST', this.base + '/verify', { names, full });
    const asked = new Set(names);
    const out: Verdict = { missing: [], corrupt: [], unreadable: [] };
    for (const key of ['missing', 'corrupt', 'unreadable'] as const) {
      const list = got[key];
      if (!Array.isArray(list) || !list.every((n) => typeof n === 'string' && asked.has(n))) {
        throw new BackupError('server', INVALID);
      }
      out[key] = list;
    }
    return out;
  }

  /** Have damaged pieces moved aside on the computer, so the next backup sends them again. */
  async quarantine(names: string[]): Promise<void> {
    for (let i = 0; i < names.length; i += 5000) {
      await this.json('POST', this.base + '/quarantine', { names: names.slice(i, i + 5000) });
    }
  }

  async deleteBlobs(names: string[]): Promise<void> {
    for (let i = 0; i < names.length; i += 5000) {
      await this.json('POST', this.base + '/delete-blobs', { names: names.slice(i, i + 5000) });
    }
  }

  /** Names of the stored backups, oldest first. */
  async listSnapshots(): Promise<string[]> {
    const got = await this.json('GET', this.base + '/snapshots');
    const list = got.snapshots;
    if (!Array.isArray(list) || !list.every((s) => s && typeof s.name === 'string' && SNAP.test(s.name))) {
      throw new BackupError('server', INVALID);
    }
    return list.map((s: { name: string }) => s.name).sort();
  }

  /** sha256: of the sealed snapshot file, as the native module reported it. */
  async putSnapshot(name: string, path: string, sha256: string): Promise<void> {
    const target = this.base + '/snapshots/' + name;
    const reply = await reach(() => this.platform.upload(this.server.url + target,
      this.headers('PUT', target, sha256), path));
    if (reply.status !== 200) throw failure(reply);
  }

  async getSnapshot(name: string, path: string): Promise<void> {
    if (!SNAP.test(name)) throw new BackupError('server', 'Bad backup name.');
    const target = this.base + '/snapshots/' + name;
    const reply = await reach(() => this.platform.download(this.server.url + target,
      this.headers('GET', target, this.platform.core.sha256Hex('')), path));
    if (reply.status !== 200) throw failure(reply);
  }

  async deleteSnapshot(name: string): Promise<void> {
    await this.json('DELETE', this.base + '/snapshots/' + name);
  }
}
