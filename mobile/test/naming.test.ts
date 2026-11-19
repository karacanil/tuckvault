import assert from 'node:assert/strict';
import test from 'node:test';

import { ago, baseName, describeRun, humanSize, plural } from '../src/lib/format.ts';
import { clean, photoPath, toMs } from '../src/lib/naming.ts';

test('times from the photo library become milliseconds, whichever unit they came in', () => {
  assert.equal(toMs(1763374500), 1763374500000);
  assert.equal(toMs(1763374500123), 1763374500123);
  assert.equal(toMs(1763374500.5), 1763374500500);
  assert.equal(toMs(null), 0);
  assert.equal(toMs(0), 0);
});

test('photos are filed by the month they were taken', () => {
  assert.equal(photoPath('IMG_0001.jpg', 'x', Date.UTC(2026, 10, 17, 10)), 'Photos/2026/11/IMG_0001.jpg');
  assert.equal(photoPath('IMG_0001.jpg', 'x', Date.UTC(2026, 0, 1)), 'Photos/2026/01/IMG_0001.jpg');
  assert.equal(photoPath('a.jpg', 'x', 0), 'Photos/Undated/a.jpg');
  assert.equal(photoPath(null, 'content://media/external/images/media/1234', 0), 'Photos/Undated/item-gesmedia1234');
});

test('a name can never climb out of its folder', () => {
  assert.equal(clean('../../etc/passwd'), '.._.._etc_passwd');
  assert.equal(clean('..'), '_');
  assert.equal(clean('a' + String.fromCharCode(92) + 'b'), 'a_b');
  assert.equal(clean(' \u0000 '), '_');
  assert.equal(photoPath('../x.jpg', 'i', 0), 'Photos/Undated/.._x.jpg');
});

test('numbers and times read naturally', () => {
  assert.equal(humanSize(0), '0 bytes');
  assert.equal(humanSize(999), '999 bytes');
  assert.equal(humanSize(21_300_000), '21.3 MB');
  assert.equal(humanSize(5_600_000_000), '5.6 GB');
  assert.equal(plural(1, 'file'), '1 file');
  assert.equal(plural(1234, 'file'), '1,234 files');
  const now = Date.UTC(2026, 10, 17, 12);
  assert.equal(ago(null, now), 'never');
  assert.equal(ago(new Date(now - 30_000).toISOString(), now), 'just now');
  assert.equal(ago(new Date(now - 5 * 60_000).toISOString(), now), '5 minutes ago');
  assert.equal(ago(new Date(now - 3 * 3600_000).toISOString(), now), '3 hours ago');
  assert.equal(ago(new Date(now - 30 * 3600_000).toISOString(), now), 'yesterday');
  assert.equal(ago(new Date(now - 4 * 86400_000).toISOString(), now), '4 days ago');
  assert.equal(baseName('Photos/2026/11/a.jpg'), 'a.jpg');
  assert.match(describeRun({ kind: 'backup', status: 'failed', finished: new Date().toISOString() }), /^Backup did not work, just now$/);
});

test('the chosen albums are summed up in a few words', async () => {
  const { albumsSummary } = await import('../src/lib/naming.ts');
  assert.equal(albumsSummary([]), 'All photos');
  assert.equal(albumsSummary([{ title: 'Holiday' }]), 'Holiday');
  assert.equal(albumsSummary([{ title: 'Holiday' }, { title: 'Family' }]), 'Holiday, Family');
  assert.equal(albumsSummary([{ title: 'A' }, { title: 'B' }, { title: 'C' }, { title: 'D' }]), 'A, B and 2 more');
});
