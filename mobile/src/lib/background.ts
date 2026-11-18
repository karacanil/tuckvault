/**
 * Automatic backups. The system wakes the app now and then (Android: WorkManager,
 * iOS: background tasks) and gives it a little time; a run that does not finish in
 * that time keeps what it uploaded and the next one continues.
 */

import { inExpoGo } from './native';
import { startBackup } from './runner';
import { getSettings } from './store';

export const TASK = 'tuckvault-auto-backup';
/** The system decides the real moment; this is the shortest gap we ask for, in minutes. */
const EVERY_MINUTES = 120;

/** False inside Expo Go: it has no background work, so there backups run only while the app is open. */
export const autoBackupPossible = !inExpoGo;

// Loaded only where they exist; inside Expo Go even loading them fails.
const BackgroundTask: typeof import('expo-background-task') | null =
  autoBackupPossible ? require('expo-background-task') : null;
const TaskManager: typeof import('expo-task-manager') | null =
  autoBackupPossible ? require('expo-task-manager') : null;

// Must be defined when the app's code loads, also when the system starts the app in the background.
if (BackgroundTask && TaskManager) {
  const results = BackgroundTask.BackgroundTaskResult;
  TaskManager.defineTask(TASK, async () => {
    try {
      await startBackup(true);
      return results.Success;
    } catch {
      return results.Failed;
    }
  });
}

/** Make the system's schedule match the setting. Safe to call often. */
export async function syncAutoBackup(): Promise<void> {
  if (!BackgroundTask || !TaskManager) return;
  try {
    const settings = getSettings();
    const wanted = settings.auto && settings.onboarded && !!settings.server;
    const registered = await TaskManager.isTaskRegisteredAsync(TASK);
    if (wanted && !registered) await BackgroundTask.registerTaskAsync(TASK, { minimumInterval: EVERY_MINUTES });
    if (!wanted && registered) await BackgroundTask.unregisterTaskAsync(TASK);
  } catch {
    // the phone does not allow background work; manual backups still work
  }
}

/** False when the phone's settings forbid background work for this app. */
export async function backgroundAllowed(): Promise<boolean> {
  if (!BackgroundTask) return false;
  try {
    return (await BackgroundTask.getStatusAsync()) === BackgroundTask.BackgroundTaskStatus.Available;
  } catch {
    return false;
  }
}
