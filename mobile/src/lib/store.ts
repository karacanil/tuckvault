/**
 * What the app remembers: settings, the history of runs, and the two secrets
 * (the backup key and the pairing secret), which live in the phone's secure storage.
 */

import * as Device from 'expo-device';
import { File } from 'expo-file-system';
import * as SecureStore from 'expo-secure-store';
import { useSyncExternalStore } from 'react';

import type { Cache, Result } from './engine';
import { dataDir } from './device';

export type Folder = { uri: string; name: string };
/** A photo album on this phone, as the user chose it. */
export type ChosenAlbum = { id: string; title: string };

export type Settings = {
  /** The computer this phone is paired with. The secret is kept apart, in secure storage. */
  server: { url: string; device: string; name: string } | null;
  onboarded: boolean;
  recoveryKeySaved: boolean;
  photos: boolean;
  videos: boolean;
  /** Back up only these albums. Empty: the whole photo library. */
  albums: ChosenAlbum[];
  /** Android: folders the user picked. iOS: the Tuckvault folder in the Files app is always the one folder. */
  folders: Folder[];
  /** iOS: back up what is put into the Tuckvault folder in the Files app. */
  inbox: boolean;
  auto: boolean;
  wifiOnly: boolean;
  chargingOnly: boolean;
  /** Backups to keep per source; 0 keeps all of them. */
  keepLast: number;
  notifyFinished: boolean;
  notifyProblems: boolean;
  /** Remind after this many days without a backup; 0 = never. */
  overdueDays: number;
  deviceName: string;
};

export type RunState = {
  lastRun: Result | null;
  /** When the last complete backup finished (ISO time). */
  lastSuccess: string | null;
  /** What the last complete backup held. */
  protectedFiles: number;
  protectedBytes: number;
  history: Result[];
  lastScrub: string | null;
  /** Automatic runs that failed in a row. */
  failures: number;
  lastProblemNotice: number;
};

export const DEFAULT_SETTINGS: Settings = {
  server: null,
  onboarded: false,
  recoveryKeySaved: false,
  photos: true,
  videos: true,
  albums: [],
  folders: [],
  inbox: true,
  auto: true,
  wifiOnly: true,
  chargingOnly: false,
  keepLast: 30,
  notifyFinished: true,
  notifyProblems: true,
  overdueDays: 7,
  deviceName: Device.deviceName || Device.modelName || 'Phone',
};

const DEFAULT_STATE: RunState = { lastRun: null, lastSuccess: null, protectedFiles: 0, protectedBytes: 0,
                                  history: [], lastScrub: null, failures: 0, lastProblemNotice: 0 };

const KEY_MASTER = 'tuckvault.master';
const KEY_SECRET = 'tuckvault.secret';
// Readable after the first unlock since the phone started, so backups can run while it is locked.
const SECURE = { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY };

function readJson<T>(name: string, fallback: T): T {
  // The freshly written copy is only there if a save was interrupted before it took the real file's place.
  for (const candidate of [name, name + '.new']) {
    try {
      const file = new File(dataDir(), candidate);
      if (!file.exists) continue;
      const parsed = JSON.parse(file.textSync());
      if (parsed && typeof parsed === 'object') return parsed;
    } catch {
      // unreadable: try the other one
    }
  }
  return fallback;
}

function writeJson(name: string, value: unknown): void {
  // Written beside the real file and then moved over it, so a crash never leaves half a file.
  const dir = dataDir();
  const tmp = new File(dir, name + '.new');
  if (tmp.exists) tmp.delete();
  tmp.create();
  tmp.writeSync(JSON.stringify(value));
  tmp.moveSync(new File(dir, name), { overwrite: true });
}

// -- settings and state, observable from the screens -------------------------
let settings: Settings | null = null;
let state: RunState | null = null;
const listeners = new Set<() => void>();

function changed() {
  for (const fn of [...listeners]) fn();
}

function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getSettings(): Settings {
  if (!settings) settings = { ...DEFAULT_SETTINGS, ...readJson<Partial<Settings>>('settings.json', {}) };
  return settings;
}

export function updateSettings(patch: Partial<Settings>): Settings {
  settings = { ...getSettings(), ...patch };
  writeJson('settings.json', settings);
  changed();
  return settings;
}

export function getState(): RunState {
  if (!state) state = { ...DEFAULT_STATE, ...readJson<Partial<RunState>>('state.json', {}) };
  return state;
}

export function updateState(patch: Partial<RunState>): RunState {
  state = { ...getState(), ...patch };
  writeJson('state.json', state);
  changed();
  return state;
}

export function recordRun(result: Result): RunState {
  const now = getState();
  const patch: Partial<RunState> = { lastRun: result, history: [result, ...now.history].slice(0, 40) };
  if (result.kind === 'backup' && (result.status === 'success' || result.status === 'warning')) {
    patch.lastSuccess = result.finished;
    patch.protectedFiles = result.files;
    patch.protectedBytes = result.bytes;
  }
  return updateState(patch);
}

export function useSettings(): Settings {
  return useSyncExternalStore(subscribe, getSettings);
}

export function useRunState(): RunState {
  return useSyncExternalStore(subscribe, getState);
}

// -- the file cache (large; read only when a backup runs) ---------------------
export function loadCache(): Cache {
  const cache = readJson<Cache>('cache.json', { repo: '', files: {} });
  return cache && typeof cache.repo === 'string' && cache.files ? cache : { repo: '', files: {} };
}

export function saveCache(cache: Cache): void {
  writeJson('cache.json', cache);
}

// -- secrets ---------------------------------------------------------------------
export function getMasterKey(): string | null {
  const key = SecureStore.getItem(KEY_MASTER, SECURE);
  return key && /^[0-9a-f]{64}$/.test(key) ? key : null;
}

export function setMasterKey(key: string): void {
  if (!/^[0-9a-f]{64}$/.test(key)) throw new Error('bad key');
  SecureStore.setItem(KEY_MASTER, key, SECURE);
}

export function getPairingSecret(): string | null {
  return SecureStore.getItem(KEY_SECRET, SECURE);
}

export function setPairingSecret(secret: string): void {
  SecureStore.setItem(KEY_SECRET, secret, SECURE);
}

/** Forget everything on this phone. The backups on the computer are not touched. */
export async function resetEverything(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY_MASTER, SECURE);
  await SecureStore.deleteItemAsync(KEY_SECRET, SECURE);
  try {
    dataDir().delete();
  } catch {
    // already gone
  }
  settings = null;
  state = null;
  changed();
}
