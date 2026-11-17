/**
 * Backup, restore, checking and clean-up: the same steps and the same stored
 * format as tuckvault/engine.py on the PC. Nothing in here knows about React
 * or Expo; the device is reached through `Platform` (see platform.ts).
 */

import { BackupError, Client } from './client.ts';
import type { Platform } from './platform.ts';

export const CHUNK = 4 * 1024 * 1024;
const VERIFY_BATCH = 200;
const SAVE_EVERY_MS = 20_000;
/** Pause before an upload is tried again grows from this (tests shorten it). */
export const tuning = { retryMs: 1000 };

/** One file to back up. */
export type Item = {
  /** Stable identity of the file on this phone (photo id, or the file's address). */
  key: string;
  /** What it belongs to: "Photos" or "Files/<folder>". Backups are kept per source. */
  source: string;
  /** Where it sits inside the backup, with "/". */
  path: string;
  /** Address the native module can read, or a way to get it when it is needed. */
  uri: string | (() => Promise<string>);
  /** Bytes, or null when only reading it will tell. */
  size: number | null;
  /** Last change, milliseconds since 1970. */
  mtime: number;
};

/** What is known about a file that was backed up before: size, time, pieces, source. */
export type CacheEntry = { s: number; m: number; c: string[]; o: string };
export type Cache = { repo: string; files: Record<string, CacheEntry> };

export type FileEntry = { p: string; s: number; m: number; c: string[] };
export type Manifest = {
  v: number;
  name: string;
  created: string;
  host: string;
  sources: string[];
  files: FileEntry[];
  stats: { files: number; bytes: number };
  warnings: string[];
};

export type Phase = 'connect' | 'backup' | 'verify' | 'scrub' | 'prune' | 'restore';
/** done/total are bytes while backing up or restoring (total 0 = not known), pieces while checking. */
export type Progress = { phase: Phase; done: number; total: number; text: string; file?: number; files?: number };

export type Result = {
  kind: 'backup' | 'verify' | 'restore';
  /** partial: stopped because time was up; what was uploaded is kept and the next run continues. */
  status: 'success' | 'warning' | 'failed' | 'partial' | 'cancelled';
  started: string;
  finished: string;
  files: number;
  bytes: number;
  uploadedBytes: number;
  newFiles: number;
  warnings: string[];
  error?: string;
  code?: string;
  snapshot?: string;
  /** Files still to go when a run stopped early. */
  remaining?: number;
};

export type Session = {
  platform: Platform;
  client: Client;
  /** 64 hex characters. */
  master: string;
  /** Folder (ending in "/") where decrypted backup indexes are kept on this phone. */
  manifestDir: string;
};

export type BackupOptions = Session & {
  items: Item[];
  /** Every source that was looked at for this run, including ones that turned out empty. */
  sources: string[];
  cache: Cache;
  saveCache: (cache: Cache) => void;
  /** Shown as "host" in the backup: the phone's name. */
  host: string;
  /** Backups to keep per source; 0 keeps all. */
  keepLast: number;
  /** Also re-check every stored piece after the backup. */
  scrub?: boolean;
  progress?: (p: Progress) => void;
  /** Stop cleanly once the clock passes this (background runs get little time). */
  deadline?: number;
  cancelled?: () => boolean;
};

export function iso(ms: number): string {
  return new Date(ms).toISOString().slice(0, 19) + 'Z';
}

function snapshotName(ms: number, suffix: string): string {
  const d = new Date(ms).toISOString(); // 2026-11-17T10:15:00.123Z
  return d.slice(0, 19).replace(/[-:]/g, '') + '.' + d.slice(20, 23) + '000Z-' + suffix + '.snap';
}

function report(cb: ((p: Progress) => void) | undefined, phase: Phase, done: number, total: number, text: string,
                file?: number, files?: number) {
  if (cb) {
    try {
      cb({ phase, done, total, text, file, files });
    } catch {
      // a broken progress display must never break a backup
    }
  }
}

function message(e: unknown): string {
  if (e && typeof e === 'object' && 'message' in e) return String((e as { message: unknown }).message).slice(0, 200);
  return String(e).slice(0, 200);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Make sure the vault exists and belongs to this key. Returns its id and the free space. */
export async function openVault(s: Session, create: boolean): Promise<{ id: string; free: number | null }> {
  const { core } = s.platform;
  const check = core.keyCheck(s.master);
  let info = await s.client.open(false);
  if (info.meta === null) {
    if (!create) throw new BackupError('no_backups', 'There are no backups from this phone on your computer yet.');
    await s.client.open(true);
    await s.client.init({ format: 1, id: core.randomHex(8), created: iso(s.platform.now()), key_check: check });
    info = await s.client.open(false);
    if (info.meta === null) throw new BackupError('server', 'Your computer could not create the backup folder.');
  }
  if (info.meta.key_check !== check) {
    throw new BackupError('wrong_key', 'The key on this phone does not match the backups on your computer.');
  }
  return { id: info.meta.id, free: info.free };
}

/** A decrypted backup index, fetched once and then kept on this phone. */
export async function manifest(s: Session, name: string): Promise<Manifest> {
  const local = s.manifestDir + name + '.json';
  const cached = s.platform.readText(local);
  if (cached) {
    try {
      const parsed = JSON.parse(cached) as Manifest;
      if (parsed && parsed.name === name && Array.isArray(parsed.files)) return parsed;
    } catch {
      // unreadable copy: fetch it again
    }
  }
  const tmp = s.platform.tmp('index-' + s.platform.core.randomHex(4) + '.snap');
  try {
    await s.client.getSnapshot(name, tmp);
    let text: string;
    try {
      text = await s.platform.core.openSnapshot(s.master, tmp);
    } catch (e) {
      throw new BackupError('integrity', 'A backup index is damaged or was written with another key (' + message(e) + ').');
    }
    const parsed = JSON.parse(text) as Manifest;
    // An index must sit under the name it was made with, so an old backup cannot pose as a new one.
    if (!parsed || parsed.name !== name || !Array.isArray(parsed.files)) {
      throw new BackupError('integrity', 'A backup is stored under a different name than it was created with.');
    }
    s.platform.writeText(local, text);
    return parsed;
  } finally {
    s.platform.remove(tmp);
  }
}

async function putBlob(s: Session, name: string, path: string): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    try {
      await s.client.putBlob(name, path);
      return;
    } catch (e) {
      const code = e instanceof BackupError ? e.code : '';
      if (attempt >= 3 || !['unreachable', 'damaged', 'cut_short', 'storage_unreachable', 'busy'].includes(code)) throw e;
      await sleep(tuning.retryMs * (attempt + 1) * (attempt + 1));
    }
  }
}

async function check(s: Session, names: string[], full: boolean, progress: BackupOptions['progress'],
                     phase: Phase, text: string) {
  const out = { missing: [] as string[], corrupt: [] as string[], unreadable: [] as string[] };
  for (let i = 0; i < names.length; i += VERIFY_BATCH) {
    report(progress, phase, i, names.length, text);
    const got = await s.client.verify(names.slice(i, i + VERIFY_BATCH), full);
    out.missing.push(...got.missing);
    out.corrupt.push(...got.corrupt);
    out.unreadable.push(...got.unreadable);
  }
  return out;
}

function uniquePath(path: string, taken: Set<string>): string {
  const lower = path.toLowerCase();
  if (!taken.has(lower)) {
    taken.add(lower);
    return path;
  }
  const slash = path.lastIndexOf('/');
  const dot = path.lastIndexOf('.');
  const stem = dot > slash + 1 ? path.slice(0, dot) : path;
  const ext = dot > slash + 1 ? path.slice(dot) : '';
  for (let n = 2; ; n++) {
    const candidate = stem + ' (' + n + ')' + ext;
    if (!taken.has(candidate.toLowerCase())) {
      taken.add(candidate.toLowerCase());
      return candidate;
    }
  }
}

/** Run one backup. Never throws: the outcome is in the result. */
export async function runBackup(o: BackupOptions): Promise<Result> {
  const { platform } = o;
  const result: Result = { kind: 'backup', status: 'failed', started: iso(platform.now()), finished: '',
                           files: 0, bytes: 0, uploadedBytes: 0, newFiles: 0, warnings: [] };
  let cache = o.cache;
  try {
    await backup(o, result, (c) => { cache = c; });
  } catch (e) {
    if (e instanceof BackupError) {
      result.error = e.message;
      result.code = e.code;
    } else {
      result.error = 'Unexpected error: ' + message(e);
    }
  }
  try {
    o.saveCache(cache);
  } catch {
    // the next run simply reads those files again
  }
  result.finished = iso(platform.now());
  result.warnings = result.warnings.slice(0, 50);
  return result;
}

async function backup(o: BackupOptions, result: Result, keep: (c: Cache) => void) {
  const { platform, client, master } = o;
  const { core } = platform;
  const warnings = result.warnings;
  if (!o.items.length) {
    throw new BackupError('nothing', o.sources.length
      ? 'Nothing was found to back up.'
      : 'Nothing is selected for backup yet.');
  }

  report(o.progress, 'connect', 0, 0, 'Connecting to your computer…');
  const vault = await openVault(o, true);
  const blobs = await client.listBlobs();
  let free = vault.free;

  const cache: Cache = o.cache.repo === vault.id ? o.cache : { repo: vault.id, files: {} };
  keep(cache);
  const total = o.items.reduce((sum, item) => sum + (item.size || 0), 0);
  const entries: FileEntry[] = [];
  const seen = new Set<string>();
  const taken = new Set<string>();
  const tmp = platform.tmp('piece-' + core.randomHex(4) + '.blob');
  let done = 0;
  let saved = platform.now();
  let stopped: 'partial' | 'cancelled' | null = null;

  try {
    for (let index = 0; index < o.items.length; index++) {
      const item = o.items[index];
      if (o.cancelled && o.cancelled()) stopped = 'cancelled';
      else if (o.deadline && platform.now() > o.deadline) stopped = 'partial';
      if (stopped) {
        result.remaining = o.items.length - index;
        break;
      }
      report(o.progress, 'backup', done, total, item.path, index, o.items.length);
      const hit = cache.files[item.key];
      let chunks: string[];
      let size: number;
      if (hit && hit.m === item.mtime && (item.size === null || hit.s === item.size) &&
          hit.c.every((b) => blobs.has(b))) {
        chunks = hit.c;
        size = hit.s;
      } else {
        try {
          const uri = typeof item.uri === 'string' ? item.uri : await item.uri();
          chunks = [];
          size = 0;
          for (;;) {
            const sealed = await core.sealChunk(master, uri, size, CHUNK, tmp);
            if (!sealed.read || !sealed.name) break;
            if (!blobs.has(sealed.name)) {
              if (free !== null && sealed.size > free) {
                throw new BackupError('full', 'Your computer is out of disk space for backups.');
              }
              await putBlob(o, sealed.name, tmp);
              blobs.set(sealed.name, sealed.size);
              if (free !== null) free -= sealed.size;
              result.uploadedBytes += sealed.size;
            }
            chunks.push(sealed.name);
            size += sealed.read;
            report(o.progress, 'backup', done + size, Math.max(total, done + size), item.path, index, o.items.length);
            if (sealed.read < CHUNK) break;
            if (o.cancelled && o.cancelled()) throw new BackupError('cancelled', 'Stopped.');
          }
          if (item.size !== null && size !== item.size) warnings.push('Changed while being backed up: ' + item.path);
          result.newFiles += 1;
        } catch (e) {
          if (e instanceof BackupError) {
            if (e.code === 'cancelled') {
              stopped = 'cancelled';
              result.remaining = o.items.length - index;
              break;
            }
            throw e; // the computer is the problem, not this file
          }
          warnings.push('Could not read ' + item.path + ': ' + message(e));
          done += item.size || 0;
          continue;
        }
      }
      cache.files[item.key] = { s: size, m: item.mtime, c: chunks, o: item.source };
      seen.add(item.key);
      entries.push({ p: uniquePath(item.path, taken), s: size, m: Math.round(item.mtime) * 1_000_000, c: chunks });
      done += item.size === null ? size : item.size;
      result.files += 1;
      result.bytes += size;
      if (platform.now() - saved > SAVE_EVERY_MS) { // an interrupted run keeps what it has done
        saved = platform.now();
        try {
          o.saveCache(cache);
        } catch {
          // not worth failing over
        }
      }
    }
  } finally {
    platform.remove(tmp);
  }

  if (stopped) {
    result.status = stopped;
    if (stopped === 'partial') {
      result.error = 'Not finished yet: ' + result.remaining + ' files are still to go. ' +
        'Open Tuckvault to let it finish.';
    }
    return;
  }
  if (!entries.length) {
    throw new BackupError('unreadable', 'No file could be read. ' + (warnings[0] || ''));
  }

  // Forget files that are gone from the sources this run covered.
  const covered = new Set(o.sources);
  for (const key of Object.keys(cache.files)) {
    if (!seen.has(key) && covered.has(cache.files[key].o)) delete cache.files[key];
  }

  entries.sort((a, b) => (a.p.toLowerCase() < b.p.toLowerCase() ? -1 : a.p.toLowerCase() > b.p.toLowerCase() ? 1 : 0));
  const now = platform.now();
  const name = snapshotName(now, core.randomHex(3));
  const made: Manifest = { v: 1, name, created: iso(now), host: o.host, sources: o.sources, files: entries,
                           stats: { files: result.files, bytes: result.bytes }, warnings: warnings.slice(0, 50) };
  const text = JSON.stringify(made);
  const snapTmp = platform.tmp('new-' + core.randomHex(4) + '.snap');
  try {
    const sealed = await core.sealSnapshot(master, text, snapTmp);
    await client.putSnapshot(name, snapTmp, sealed.sha256);
  } finally {
    platform.remove(snapTmp);
  }
  platform.writeText(o.manifestDir + name + '.json', text);
  result.snapshot = name;

  // Everything this backup needs must really be on the other side.
  const wanted = [...new Set(entries.flatMap((e) => e.c))].sort();
  const quick = await check(o, wanted, false, o.progress, 'verify', 'Checking the new backup…');
  const bad = quick.missing.length + quick.corrupt.length + quick.unreadable.length;
  if (bad) {
    throw new BackupError('incomplete', bad + ' pieces of the new backup are missing on your computer. ' +
      'The backup is not complete.');
  }

  let problems: string | null = null;
  if (o.scrub) problems = await scrub(o, o.progress);
  await prune(o, o.keepLast, name, o.progress);
  if (problems) throw new BackupError('damaged', problems);
  result.status = warnings.length ? 'warning' : 'success';
}

/** Re-check every stored piece of every backup. Returns a description of what is wrong, or null. */
export async function scrub(s: Session, progress?: (p: Progress) => void): Promise<string | null> {
  const names = await s.client.listSnapshots();
  const refs = new Map<string, Set<string>>();
  let unreadable = 0;
  for (const name of names) {
    try {
      for (const f of (await manifest(s, name)).files) {
        for (const blob of f.c) {
          if (!refs.has(blob)) refs.set(blob, new Set());
          refs.get(blob)!.add(name);
        }
      }
    } catch (e) {
      if (e instanceof BackupError && e.code === 'integrity') unreadable += 1;
      else throw e;
    }
  }
  const got = await check(s, [...refs.keys()].sort(), true, progress, 'scrub', 'Checking everything stored…');
  const bad = [...got.missing, ...got.corrupt];
  if (got.corrupt.length) await s.client.quarantine(got.corrupt);
  const hit = new Set<string>();
  for (const blob of bad) for (const snap of refs.get(blob) || []) hit.add(snap);
  const parts: string[] = [];
  if (bad.length) {
    parts.push(bad.length + ' stored pieces were damaged or missing on your computer, affecting ' + hit.size +
      ' of ' + (names.length - unreadable) + ' backups. Back up now to send the files that are still on this phone again.');
  }
  if (got.unreadable.length) {
    parts.push(got.unreadable.length + ' stored pieces could not be read just now. That can be a temporary ' +
      'disk or connection problem on the computer; nothing was changed. Run the check again.');
  }
  if (unreadable) parts.push(unreadable + ' backups could not be read at all (damaged index).');
  return parts.join(' ') || null;
}

/** Drop old backups (keeping the newest `keepLast` of every source), then data nothing refers to. */
export async function prune(s: Session, keepLast: number, protect: string | null,
                            progress?: (p: Progress) => void): Promise<void> {
  let names = await s.client.listSnapshots();
  if (keepLast > 0) {
    const keep = new Set<string>(protect ? [protect] : []);
    const count = new Map<string, number>();
    const newestFirst = [...names].reverse();
    for (let pos = 0; pos < newestFirst.length; pos++) {
      const name = newestFirst[pos];
      let sources: string[];
      try {
        sources = (await manifest(s, name)).sources;
        if (!Array.isArray(sources) || !sources.length) sources = [''];
      } catch (e) {
        if (!(e instanceof BackupError && e.code === 'integrity')) throw e;
        if (pos < keepLast) keep.add(name); // unreadable ones only age out
        continue;
      }
      for (const source of sources) {
        const n = count.get(source) || 0;
        if (n < keepLast) {
          count.set(source, n + 1);
          keep.add(name);
        }
      }
    }
    for (const old of names.filter((n) => !keep.has(n))) {
      await s.client.deleteSnapshot(old);
      s.platform.remove(s.manifestDir + old + '.json');
    }
    names = names.filter((n) => keep.has(n));
  }
  const used = new Set<string>();
  for (const name of names) {
    let m: Manifest;
    try {
      m = await manifest(s, name);
    } catch (e) {
      if (e instanceof BackupError && e.code === 'integrity') return; // cannot tell what is needed: remove nothing
      throw e;
    }
    for (const f of m.files) for (const blob of f.c) used.add(blob);
  }
  const unused = [...(await s.client.listBlobs()).keys()].filter((b) => !used.has(b));
  if (unused.length) {
    report(progress, 'prune', 0, 0, 'Removing data no backup needs any more…');
    await s.client.deleteBlobs(unused);
  }
}

export type BackupInfo = {
  name: string;
  created?: string;
  files?: number;
  bytes?: number;
  sources?: string[];
  host?: string;
  warnings?: number;
  error?: string;
};

/** The backups on the computer, newest first. */
export async function listBackups(s: Session): Promise<BackupInfo[]> {
  await openVault(s, false);
  const out: BackupInfo[] = [];
  for (const name of (await s.client.listSnapshots()).reverse()) {
    try {
      const m = await manifest(s, name);
      out.push({ name, created: m.created, files: m.stats.files, bytes: m.stats.bytes, sources: m.sources,
                 host: m.host, warnings: (m.warnings || []).length });
    } catch (e) {
      if (!(e instanceof BackupError && e.code === 'integrity')) throw e;
      out.push({ name, error: e.message });
    }
  }
  return out;
}

/** Fetch, check and decrypt one file of a backup into `outPath`. Every byte is verified. */
export async function restoreFile(s: Session, entry: FileEntry, outPath: string,
                                  onBytes?: (bytes: number) => void): Promise<void> {
  const { platform } = s;
  const tmp = platform.tmp('fetch-' + platform.core.randomHex(4) + '.blob');
  platform.remove(outPath);
  try {
    if (!entry.c.length) platform.writeText(outPath, '');
    for (let i = 0; i < entry.c.length; i++) {
      await s.client.getBlob(entry.c[i], tmp);
      let n: number;
      try {
        n = await platform.core.openChunk(s.master, entry.c[i], tmp, outPath, i > 0);
      } catch (e) {
        throw new BackupError('integrity', 'A stored piece of this file is damaged (' + message(e) + ').');
      }
      if (onBytes) onBytes(n);
    }
  } catch (e) {
    platform.remove(outPath);
    throw e;
  } finally {
    platform.remove(tmp);
  }
}

/** Full check of everything stored, as a result like a backup's. Never throws. */
export async function runVerify(s: Session, progress?: (p: Progress) => void): Promise<Result> {
  const result: Result = { kind: 'verify', status: 'failed', started: iso(s.platform.now()), finished: '',
                           files: 0, bytes: 0, uploadedBytes: 0, newFiles: 0, warnings: [] };
  try {
    report(progress, 'connect', 0, 0, 'Connecting to your computer…');
    await openVault(s, false);
    const problem = await scrub(s, progress);
    if (problem) result.error = problem;
    else result.status = 'success';
  } catch (e) {
    result.error = e instanceof BackupError ? e.message : 'Unexpected error: ' + message(e);
    if (e instanceof BackupError) result.code = e.code;
  }
  result.finished = iso(s.platform.now());
  return result;
}
