/** How things on the phone are named inside a backup. No device code in here, so it is tested on a PC. */

/** The library reports times in seconds on some systems and milliseconds on others. */
export function toMs(time: number | null | undefined): number {
  if (!time || time <= 0) return 0;
  return Math.round(time < 100_000_000_000 ? time * 1000 : time);
}

export function clean(name: string): string {
  // one path segment: nothing that could be read as a folder or a parent
  const out = name.replace(/[\\/\u0000-\u001f]/g, '_').trim();
  return out === '' || out === '.' || out === '..' ? '_' : out;
}

/** "Photos/2026/11/IMG_0001.jpg" from when the picture was taken. */
export function photoPath(filename: string | null, id: string, created: number): string {
  const name = clean(filename || 'item-' + id.replace(/[^0-9A-Za-z]/g, '').slice(-12));
  if (!created) return 'Photos/Undated/' + name;
  const d = new Date(created);
  return 'Photos/' + d.getUTCFullYear() + '/' + String(d.getUTCMonth() + 1).padStart(2, '0') + '/' + name;
}
