/**
 * Notifications: a backup finished, a backup has a problem, and a reminder
 * when no backup has happened for a while (set ahead of time, so it also
 * arrives when the app never got to run).
 */

import * as Notifications from 'expo-notifications';
import { Platform as RN } from 'react-native';

import type { Result } from './engine';
import { humanSize, plural } from './format';
import { getSettings, getState, updateState } from './store';

const CHANNEL = 'backups';
const OVERDUE_ID = 'tuckvault-overdue';
const DAY = 24 * 3600;

let prepared = false;

export async function prepare(): Promise<void> {
  if (prepared) return;
  prepared = true;
  Notifications.setNotificationHandler({
    // While the app is open the screen already shows what happened.
    handleNotification: async () => ({ shouldShowBanner: false, shouldShowList: true, shouldPlaySound: false,
                                       shouldSetBadge: false }),
  });
  if (RN.OS === 'android') {
    await Notifications.setNotificationChannelAsync(CHANNEL, {
      name: 'Backups',
      description: 'When a backup finishes or needs your attention',
      importance: Notifications.AndroidImportance.DEFAULT,
    });
  }
}

export async function allowed(): Promise<boolean> {
  return (await Notifications.getPermissionsAsync()).granted;
}

/** Ask once for permission to notify. Returns whether notifications will be shown. */
export async function ask(): Promise<boolean> {
  await prepare();
  const now = await Notifications.getPermissionsAsync();
  if (now.granted) return true;
  if (!now.canAskAgain) return false;
  return (await Notifications.requestPermissionsAsync()).granted;
}

async function show(title: string, body: string): Promise<void> {
  try {
    await prepare();
    if (!(await allowed())) return;
    await Notifications.scheduleNotificationAsync({
      content: { title, body },
      trigger: RN.OS === 'android' ? { channelId: CHANNEL } : null,
    });
  } catch {
    // a notification that cannot be shown must not fail a backup
  }
}

/** What to tell the user about a finished run: [title, text], or null for nothing. */
export function describe(result: Result, background: boolean, failures: number): [string, string] | null {
  const settings = getSettings();
  if (result.kind !== 'backup') return null;
  if (result.status === 'success' || result.status === 'warning') {
    if (!settings.notifyFinished || !result.newFiles) return null; // nothing new: nothing to say
    // Nothing sent means the content was already stored (a copy of something backed up before).
    const text = plural(result.newFiles, 'new file') + ' safely stored' +
      (result.uploadedBytes ? ' (' + humanSize(result.uploadedBytes) + ' sent).' : '.') +
      (result.warnings.length ? ' ' + plural(result.warnings.length, 'file') + ' could not be read.' : '');
    return ['Backup finished', text];
  }
  if (result.status === 'cancelled' || !settings.notifyProblems) return null;
  if (result.status === 'partial') {
    // Normal for the first big backup: say it once it keeps happening, not every time.
    return background && failures === 3 ? ['Backup needs a moment of your time', result.error || ''] : null;
  }
  if (background && (result.code === 'unreachable' || result.code === 'storage_unreachable') && failures < 3) {
    return null; // the computer is off or out of reach now and then; that alone is not news
  }
  return ['Backup did not work', result.error || 'Open Tuckvault to see what is wrong.'];
}

export async function notifyResult(result: Result, background: boolean): Promise<void> {
  const state = getState();
  const message = describe(result, background, state.failures);
  if (!message) return;
  const problem = result.status !== 'success' && result.status !== 'warning';
  if (problem && background) {
    if (Date.now() - state.lastProblemNotice < DAY * 1000) return; // at most one a day
    updateState({ lastProblemNotice: Date.now() });
  }
  await show(message[0], message[1]);
}

/** (Re)set the reminder that fires when no backup has finished for the chosen number of days. */
export async function scheduleOverdueReminder(): Promise<void> {
  try {
    await prepare();
    await Notifications.cancelScheduledNotificationAsync(OVERDUE_ID).catch(() => {});
    const days = getSettings().overdueDays;
    if (!days || !getSettings().server || !(await allowed())) return;
    await Notifications.scheduleNotificationAsync({
      identifier: OVERDUE_ID,
      content: { title: 'No backup for ' + plural(days, 'day'),
                 body: 'Your phone has not been backed up lately. Open Tuckvault to back up now.' },
      trigger: { type: Notifications.SchedulableTriggerInputTypes.TIME_INTERVAL, seconds: days * DAY,
                 repeats: false, channelId: CHANNEL },
    });
  } catch {
    // reminders are a convenience
  }
}
