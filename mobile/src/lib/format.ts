/** Small helpers for showing numbers, sizes and times. No device code in here. */

/** Below this much free room on the computer, the app points it out (5 GB). */
export const LOW_SPACE = 5e9;

/** "412 GB free of 500 GB on your computer", and a nudge when it is nearly full. */
export function spaceText(free: number, total: number | null): string {
  const text = humanSize(free) + ' free' + (total ? ' of ' + humanSize(total) : '') + ' on your computer';
  return free < LOW_SPACE ? text + '. Nearly full: make room there soon.' : text;
}

export function humanSize(bytes: number | null | undefined): string {
  let n = Math.max(0, bytes || 0);
  const units = ['bytes', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1000 && i < units.length - 1) {
    n /= 1000;
    i += 1;
  }
  return (i === 0 ? String(Math.round(n)) : n >= 100 ? n.toFixed(0) : n.toFixed(1)) + ' ' + units[i];
}

export function plural(n: number, word: string): string {
  return n.toLocaleString('en') + ' ' + word + (n === 1 ? '' : 's');
}

/** "just now", "5 minutes ago", "yesterday", "12 Nov". `now` and `iso` in the same clock. */
export function ago(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return 'never';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return 'never';
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' minutes ago';
  if (s < 2 * 3600) return 'an hour ago';
  if (s < 24 * 3600) return Math.round(s / 3600) + ' hours ago';
  if (s < 48 * 3600) return 'yesterday';
  if (s < 7 * 24 * 3600) return Math.round(s / 86400) + ' days ago';
  return localDate(iso);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function localDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const year = d.getFullYear() === new Date().getFullYear() ? '' : ' ' + d.getFullYear();
  return d.getDate() + ' ' + MONTHS[d.getMonth()] + year;
}

export function localDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return localDate(iso) + ', ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

/** Last part of a path. */
export function baseName(path: string): string {
  const i = path.lastIndexOf('/');
  return i < 0 ? path : path.slice(i + 1);
}

/** "Backup finished, 2 hours ago" for a run's result. */
export function describeRun(r: { kind: string; status: string; finished: string; left?: string[] }): string {
  const what = r.kind === 'verify' ? 'Check' : r.kind === 'restore' ? 'Restore' : 'Backup';
  const how: Record<string, string> = { success: 'finished', warning: 'finished with skipped files', failed: 'did not work',
                                        partial: 'not finished', cancelled: 'stopped' };
  if (r.status === 'warning' && r.left?.length) return what + ' finished, but not everything was backed up, ' + ago(r.finished);
  return what + ' ' + (how[r.status] || r.status) + ', ' + ago(r.finished);
}
