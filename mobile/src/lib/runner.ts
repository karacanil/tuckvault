/**
 * Starts backups, checks and restores, one at a time, and lets the screens
 * watch what is going on.
 */

import * as Battery from 'expo-battery';
import { Directory, File, Paths } from 'expo-file-system';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { useSyncExternalStore } from 'react';
import { AppState, Platform as RN } from 'react-native';

import Native from './native';
import { BackupError, Client } from './client';
import { clearTmp, device, manifestDir } from './device';
import { iso, listBackups, manifest, restoreFile, runBackup, runVerify } from './engine';
import type { BackupInfo, FileEntry, Manifest, Progress, Result, Session } from './engine';
import { baseName } from './format';
import { notifyResult, scheduleOverdueReminder } from './notify';
import { scan } from './sources';
import { getMasterKey, getPairingSecret, getSettings, getState, loadCache, recordRun, saveCache, updateState } from './store';

const SCRUB_EVERY_DAYS = 14;
/** How long an automatic run may take before it stops itself (the system gives little time). */
const BACKGROUND_BUDGET_MS = RN.OS === 'ios' ? 20_000 : 8 * 60_000;

export type Activity = {
  kind: 'backup' | 'verify' | 'restore' | null;
  /** Looking for files, before the engine starts. */
  scanning: boolean;
  progress: Progress | null;
  cancelling: boolean;
};

let activity: Activity = { kind: null, scanning: false, progress: null, cancelling: false };
let cancelRequested = false;
const listeners = new Set<() => void>();

function set(patch: Partial<Activity>) {
  activity = { ...activity, ...patch };
  for (const fn of [...listeners]) fn();
}

export function getActivity(): Activity {
  return activity;
}

export function useActivity(): Activity {
  return useSyncExternalStore((fn) => {
    listeners.add(fn);
    return () => {
      listeners.delete(fn);
    };
  }, getActivity);
}

/** The paired computer and this phone's key, ready for the engine. Throws BackupError if not set up. */
export function session(masterOverride?: string): Session {
  const settings = getSettings();
  const master = masterOverride || getMasterKey();
  const secret = getPairingSecret();
  if (!settings.server || !secret) {
    throw new BackupError('setup', 'This phone is not connected to a computer yet.');
  }
  if (!master) {
    throw new BackupError('setup', 'This phone has no backup key. Enter your recovery key in Settings.');
  }
  const server = { url: settings.server.url, device: settings.server.device, secret, name: settings.server.name };
  return { platform: device, master, manifestDir: manifestDir(),
           client: new Client(device, server, Native.keyCheck(master).slice(0, 24)) };
}

function failed(kind: Result['kind'], e: unknown): Result {
  const now = iso(Date.now());
  return { kind, status: 'failed', started: now, finished: now, files: 0, bytes: 0, uploadedBytes: 0, newFiles: 0,
           warnings: [], error: e instanceof BackupError ? e.message : 'Unexpected error: ' + String(e).slice(0, 200),
           code: e instanceof BackupError ? e.code : undefined };
}

async function charging(): Promise<boolean> {
  try {
    const state = await Battery.getBatteryStateAsync();
    return state === Battery.BatteryState.CHARGING || state === Battery.BatteryState.FULL;
  } catch {
    return true; // cannot tell: do not hold the backup back for it
  }
}

/** Why an automatic backup should wait, or null if it may run now. */
export async function autoBlocker(): Promise<string | null> {
  const settings = getSettings();
  if (!settings.onboarded || !settings.server) return 'not set up';
  if (!settings.auto) return 'automatic backups are off';
  if (settings.wifiOnly && !(await Native.onWifi())) return 'waiting for Wi-Fi';
  if (settings.chargingOnly && !(await charging())) return 'waiting for the charger';
  return null;
}

/**
 * Back up now. `background`: started by the system, not by the user; then the
 * conditions for automatic backups apply and the run limits its own time.
 * Returns null when nothing was started.
 */
export async function startBackup(background = false): Promise<Result | null> {
  if (activity.kind) return null;
  if (background && (await autoBlocker())) return null;
  cancelRequested = false;
  set({ kind: 'backup', scanning: true, progress: null, cancelling: false });
  const started = Date.now();
  let result: Result;
  try {
    if (!background) await activateKeepAwakeAsync('tuckvault-backup').catch(() => {});
    clearTmp();
    const s = session();
    const settings = getSettings();
    const found = await scan(settings);
    set({ scanning: false });
    const state = getState();
    const scrubDue = !state.lastScrub || Date.now() - Date.parse(state.lastScrub) > SCRUB_EVERY_DAYS * 86400_000;
    let last = 0;
    result = await runBackup({
      ...s,
      items: found.items,
      sources: found.sources,
      cache: loadCache(),
      saveCache,
      host: settings.deviceName,
      keepLast: settings.keepLast,
      scrub: scrubDue && !(background && RN.OS === 'ios'),
      deadline: background ? started + BACKGROUND_BUDGET_MS : undefined,
      cancelled: () => cancelRequested,
      progress: (p) => {
        const now = Date.now();
        if (now - last > 150 || p.phase !== activity.progress?.phase) { // screens need not hear of every piece
          last = now;
          set({ progress: p });
        }
      },
    });
    if (found.warnings.length) {
      result.warnings = [...found.warnings, ...result.warnings].slice(0, 50);
      if (result.status === 'success') result.status = 'warning';
    }
    if (result.scrubbed) updateState({ lastScrub: result.finished });
  } catch (e) {
    result = failed('backup', e);
  } finally {
    if (!background) deactivateKeepAwake('tuckvault-backup').catch(() => {});
  }
  finish(result, background);
  return result;
}

function finish(result: Result, background: boolean) {
  const ok = result.status === 'success' || result.status === 'warning';
  if (result.status !== 'cancelled') {
    updateState({ failures: ok ? 0 : getState().failures + 1 });
  }
  recordRun(result);
  set({ kind: null, scanning: false, progress: null, cancelling: false });
  if (result.kind === 'backup') {
    // With the app open on screen the result is already in front of the user.
    if (background || AppState.currentState !== 'active') notifyResult(result, background).catch(() => {});
    if (ok) scheduleOverdueReminder().catch(() => {});
  }
}

/** Re-check every stored piece of every backup. */
export async function startVerify(): Promise<Result | null> {
  if (activity.kind) return null;
  set({ kind: 'verify', scanning: false, progress: null, cancelling: false });
  let result: Result;
  try {
    await activateKeepAwakeAsync('tuckvault-verify').catch(() => {});
    result = await runVerify(session(), (p) => set({ progress: p }));
    if (result.status === 'success') updateState({ lastScrub: result.finished });
  } catch (e) {
    result = failed('verify', e);
  } finally {
    deactivateKeepAwake('tuckvault-verify').catch(() => {});
  }
  finish(result, false);
  return result;
}

export function cancel(): void {
  if (activity.kind === 'backup') {
    cancelRequested = true;
    set({ cancelling: true });
  }
}

// -- looking at and restoring backups --------------------------------------------
export function backups(masterOverride?: string): Promise<BackupInfo[]> {
  return listBackups(session(masterOverride));
}

export function backupContents(name: string): Promise<Manifest> {
  return manifest(session(), name);
}

function restoreDir(): Directory {
  const dir = new Directory(Paths.cache, 'tuckvault-restored');
  if (!dir.exists) dir.create({ intermediates: true });
  return dir;
}

/** Throw away files that were fetched to look at or share. */
export function clearRestored(): void {
  try {
    const dir = new Directory(Paths.cache, 'tuckvault-restored');
    if (dir.exists) dir.delete();
  } catch {
    // only copies
  }
}

/**
 * Fetch one file of a backup onto this phone (into the app's own temporary storage).
 * Returns its address. Every byte is checked on the way.
 */
export async function fetchFile(entry: FileEntry, onProgress?: (done: number, total: number) => void): Promise<string> {
  const target = new File(restoreDir(), baseName(entry.p).replace(/[\\/:*?"<>|]/g, '_') || 'file');
  let done = 0;
  await restoreFile(session(), entry, target.uri, (n) => {
    done += n;
    if (onProgress) onProgress(done, entry.s);
  });
  return target.uri;
}

export function isMedia(path: string): boolean {
  return /\.(jpe?g|png|heic|heif|gif|webp|bmp|dng|tiff?|mp4|mov|m4v|3gp|mkv|webm|avi)$/i.test(path);
}
