/**
 * The app's backup engine against the real `tuckvault serve`, with Node playing
 * the phone. What the engine stores is then restored by the PC program
 * (`tuckvault phone restore`) and compared byte for byte.
 *
 *     npm test          (needs Python with the Tuckvault requirements; set TUCKVAULT_PYTHON to choose one)
 */

import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import test, { after, before } from 'node:test';

import { BackupError, Client, hello, normalizeAddress, pair, scannedPairing } from '../src/lib/client.ts';
import type { Server } from '../src/lib/client.ts';
import { CHUNK, listBackups, manifest, restoreFile, runBackup, runVerify, tuning } from '../src/lib/engine.ts';
import type { BackupOptions, Cache, Item, Session } from '../src/lib/engine.ts';
import { nodePlatform } from './node-platform.ts';
import type { NodePlatform } from './node-platform.ts';

const REPO = path.resolve(import.meta.dirname, '..', '..');
const PYTHON = process.env.TUCKVAULT_PYTHON ||
  [path.join(REPO, '.venv', 'Scripts', 'python.exe'), path.join(REPO, '.venv', 'bin', 'python')]
    .find((p) => fs.existsSync(p)) || 'python3';

let tmp: string;
let home: string;
let url: string;
let service: ChildProcess;
let env: Record<string, string | undefined>;

function python(args: string[], extra: Record<string, string> = {}) {
  const run = spawnSync(PYTHON, ['-m', 'tuckvault', ...args], { cwd: REPO, env: { ...env, ...extra } as NodeJS.ProcessEnv, encoding: 'utf8' });
  return { code: run.status, out: (run.stdout || '') + (run.stderr || '') };
}

function freePort(): Promise<number> {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.listen(0, '127.0.0.1', () => {
      const port = (s.address() as net.AddressInfo).port;
      s.close(() => resolve(port));
    });
  });
}

before(async () => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'tuckvault-mobile-'));
  home = path.join(tmp, 'home');
  fs.mkdirSync(home);
  const port = await freePort();
  fs.writeFileSync(path.join(home, 'config.json'), JSON.stringify({ destination: { type: 'local', folder: path.join(tmp, 'repo') } }));
  fs.writeFileSync(path.join(home, 'phones.json'), JSON.stringify({ port }));
  env = { ...process.env, TUCKVAULT_HOME: home, TUCKVAULT_SERVE_IDLE: '1', PYTHONUNBUFFERED: '1' };
  service = spawn(PYTHON, ['-m', 'tuckvault', 'serve', '--listen', '127.0.0.1', '--port', String(port)],
    { cwd: REPO, env: env as NodeJS.ProcessEnv, stdio: 'ignore' });
  url = 'http://127.0.0.1:' + port;
  const probe = nodePlatform(path.join(tmp, 'probe'));
  for (let i = 0; ; i++) {
    try {
      await hello(probe, url);
      break;
    } catch (e) {
      if (i > 100) throw e;
      await new Promise((r) => setTimeout(r, 100));
    }
  }
});

after(() => {
  service?.kill();
  fs.rmSync(tmp, { recursive: true, force: true });
});

/** A freshly paired phone with its own key and files. */
async function phone(name: string, sharedKey?: string) {
  const dir = path.join(tmp, name);
  const platform: NodePlatform = nodePlatform(path.join(dir, 'tmp'));
  const shown = python(['phone', 'pair']);
  const code = /Code:\s+(\S+)/.exec(shown.out)?.[1];
  assert.ok(code, shown.out);
  const server: Server = await pair(platform, url, code, name, 'test');
  const master = sharedKey || platform.core.newMasterKey();
  const session: Session = { platform, master, manifestDir: path.join(dir, 'manifests') + path.sep,
                             client: new Client(platform, server, platform.core.keyCheck(master).slice(0, 24)) };
  const files = path.join(dir, 'files');
  fs.mkdirSync(files, { recursive: true });
  let cache: Cache = { repo: '', files: {} };
  const write = (rel: string, data: Buffer | string) => {
    const full = path.join(files, rel);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, data);
  };
  const items = (): Item[] => {
    const out: Item[] = [];
    const walk = (folder: string, prefix: string) => {
      for (const entry of fs.readdirSync(folder, { withFileTypes: true })) {
        const full = path.join(folder, entry.name);
        if (entry.isDirectory()) walk(full, prefix + entry.name + '/');
        else {
          const st = fs.statSync(full);
          out.push({ key: full, source: prefix.startsWith('Photos/') ? 'Photos' : 'Files/Documents',
                     path: prefix + entry.name, uri: full, size: st.size, mtime: Math.round(st.mtimeMs) });
        }
      }
    };
    walk(files, '');
    return out;
  };
  const backup = (extra: Partial<BackupOptions> = {}) => runBackup({
    ...session, items: items(), sources: ['Photos', 'Files/Documents'], cache, saveCache: (c) => { cache = c; },
    host: name, keepLast: 30, ...extra });
  return { dir, files, platform, session, master, server, write, items, backup };
}

function blobFiles(vault: string): string[] {
  const base = path.join(tmp, 'repo-phones', vault, 'blobs');
  return fs.readdirSync(base).flatMap((sub) => fs.readdirSync(path.join(base, sub)).map((f) => path.join(base, sub, f)));
}

test('addresses typed by hand are understood', () => {
  assert.equal(normalizeAddress(' 100.101.102.103 '), 'http://100.101.102.103:8743');
  assert.equal(normalizeAddress('PC.tail1234.ts.net:9000/'), 'http://pc.tail1234.ts.net:9000');
  assert.equal(normalizeAddress('https://pc.tail1234.ts.net/x'), 'https://pc.tail1234.ts.net');
  assert.throws(() => normalizeAddress(''), BackupError);
  assert.throws(() => normalizeAddress('http://user@host'), BackupError);
});

test('a scanned pairing code is understood, and anything else is left alone', () => {
  const token = 'ABCDEFGHJKMNPQRSTUVWXYZ234567892';
  assert.deepEqual(scannedPairing('http://pc.tail1234.ts.net:8743', token),
                   { url: 'http://pc.tail1234.ts.net:8743', host: 'pc.tail1234.ts.net', token });
  assert.deepEqual(scannedPairing('100.101.102.103:9000', token),
                   { url: 'http://100.101.102.103:9000', host: '100.101.102.103:9000', token });
  assert.equal(scannedPairing(undefined, token), null);
  assert.equal(scannedPairing('http://pc.tail1234.ts.net:8743', undefined), null);
  assert.equal(scannedPairing(['a', 'b'], token), null);                       // a link with the address twice
  assert.equal(scannedPairing('http://pc.tail1234.ts.net:8743', 'ABCD-EFGH'), null); // the short code is typed, never scanned
  assert.equal(scannedPairing('http://pc.tail1234.ts.net:8743', token + '&x=<script>'), null);
  assert.equal(scannedPairing('http://someone@pc.tail1234.ts.net', token), null);
  assert.equal(scannedPairing('', token), null);
  assert.equal(scannedPairing('h'.repeat(301), token), null);
});

test('a phone pairs with the picture the computer makes, once', async () => {
  const picture = path.join(tmp, 'pair.png');
  const shown = python(['phone', 'pair', '--qr', picture]);
  assert.match(shown.out, /saved as a picture/, shown.out + shown.err);
  assert.equal(fs.readFileSync(picture).subarray(1, 4).toString(), 'PNG');
  const token = JSON.parse(fs.readFileSync(path.join(tmp, 'home', 'phones.json'), 'utf8')).pairing;
  assert.ok(token.link && !('token' in token)); // only a hash of the token is kept on the computer
});

test('a wrong pairing code and a wrong address are explained', async () => {
  const platform = nodePlatform(path.join(tmp, 'nobody'));
  await assert.rejects(pair(platform, url, 'AAAA-AAAA', 'x', 'test'),
                       (e: BackupError) => ['no_pairing', 'bad_code', 'pairing_used', 'pairing_locked'].includes(e.code));
  await assert.rejects(hello(platform, 'http://127.0.0.1:9'), (e: BackupError) => e.code === 'unreachable');
});

test('a backup made by the phone is restored by the PC with the recovery key', async () => {
  const p = await phone('pixel');
  const big = crypto.randomBytes(CHUNK + 54321);
  p.write('Photos/2026/11/IMG_0001.jpg', crypto.randomBytes(70_000));
  p.write('Photos/2026/11/VID_0002.mp4', big);
  p.write('Belgeler/şifre notları.txt', 'gizli ünïcode ✓\n'.repeat(500));
  p.write('Belgeler/empty.bin', '');
  const result = await p.backup();
  assert.equal(result.status, 'success', JSON.stringify(result));
  assert.equal(result.files, 4);
  assert.equal(result.newFiles, 4);

  // nothing readable is stored
  const vault = p.platform.core.keyCheck(p.master).slice(0, 24);
  const stored = Buffer.concat(blobFiles(vault).map((f) => fs.readFileSync(f)));
  assert.equal(stored.includes('gizli'), false);

  const out = path.join(p.dir, 'restored-by-pc');
  const restored = python(['phone', 'restore', out], { TUCKVAULT_PHONE_KEY: p.platform.core.encodeRecoveryKey(p.master) });
  assert.equal(restored.code, 0, restored.out);
  for (const item of p.items()) {
    assert.deepEqual(fs.readFileSync(path.join(out, item.path)), fs.readFileSync(item.key), item.path);
    // the time of the last change survives to the millisecond
    assert.ok(Math.abs(fs.statSync(path.join(out, item.path)).mtimeMs - item.mtime) < 2, item.path);
  }
});

test('later backups send only what changed, and old ones are tidied away', async () => {
  const p = await phone('iphone');
  p.write('Photos/a.jpg', crypto.randomBytes(30_000));
  p.write('Photos/b.jpg', crypto.randomBytes(30_000));
  assert.equal((await p.backup()).status, 'success');

  // nothing changed: nothing is sent and no new backup is written, so automatic runs
  // several times a day do not push real history out of the newest N
  const first = (await listBackups(p.session))[0].name;
  p.platform.uploads = 0;
  const second = await p.backup();
  assert.equal(second.status, 'success');
  assert.equal(second.unchanged, true);
  assert.equal(second.snapshot, first);
  assert.equal(p.platform.uploads, 0);
  assert.equal((await listBackups(p.session)).length, 1);

  p.write('Photos/c.jpg', crypto.randomBytes(30_000));
  fs.rmSync(path.join(p.files, 'Photos', 'a.jpg'));
  const third = await p.backup({ keepLast: 2 });
  assert.equal(third.status, 'success', JSON.stringify(third));
  assert.equal(third.newFiles, 1);

  const backups = await listBackups(p.session);
  assert.equal(backups.length, 2);
  assert.equal(backups[0].name, third.snapshot);
  assert.deepEqual((await manifest(p.session, third.snapshot!)).files.map((f) => f.p), ['Photos/b.jpg', 'Photos/c.jpg']);
  // a.jpg is still in the older backup that was kept, so its data stays
  assert.equal(blobFiles(p.platform.core.keyCheck(p.master).slice(0, 24)).length, 3);
  const fourth = await p.backup({ keepLast: 1 });
  assert.equal(fourth.status, 'success');
  assert.equal(blobFiles(p.platform.core.keyCheck(p.master).slice(0, 24)).length, 2);
});

test('two phones that share a recovery key do not push out each other\'s backups', async () => {
  const old = await phone('old-pixel');
  old.write('Photos/holiday.jpg', crypto.randomBytes(20_000));
  const kept = await old.backup({ keepLast: 2 });
  assert.equal(kept.status, 'success');

  // the replacement phone continues with the same key and backs up many times
  const fresh = await phone('new-pixel', old.master);
  for (let i = 0; i < 4; i++) {
    fresh.write('Photos/new-' + i + '.jpg', crypto.randomBytes(5000));
    assert.equal((await fresh.backup({ keepLast: 2 })).status, 'success');
  }
  const all = await listBackups(fresh.session);
  assert.equal(all.filter((b) => b.host === 'new-pixel').length, 2);
  assert.equal(all.filter((b) => b.host === 'old-pixel').length, 1, 'the old phone\'s backup must survive');
  const out = path.join(fresh.dir, 'holiday.jpg');
  const entry = (await manifest(fresh.session, kept.snapshot!)).files[0];
  await restoreFile(fresh.session, entry, out);
  assert.deepEqual(fs.readFileSync(out), fs.readFileSync(path.join(old.files, 'Photos', 'holiday.jpg')));

  // while the other phone was active recently, data that no backup lists yet is left alone:
  // it may belong to a backup that phone is in the middle of
  const vault = fresh.platform.core.keyCheck(fresh.master).slice(0, 24);
  const before = blobFiles(vault).length;
  fs.rmSync(path.join(fresh.files, 'Photos', 'new-3.jpg'));
  fresh.write('Photos/new-9.jpg', crypto.randomBytes(5000));
  assert.equal((await fresh.backup({ keepLast: 1 })).status, 'success');
  assert.ok(blobFiles(vault).length >= before, 'nothing may be cleaned up while another phone shares the vault');
});

test('damage found in an old backup is reported, but the new backup still counts', async () => {
  const p = await phone('scrubbed');
  p.write('Photos/gone-later.jpg', crypto.randomBytes(9000));
  p.write('Photos/stays.jpg', crypto.randomBytes(9000));
  const first = await p.backup();
  const gone = (await manifest(p.session, first.snapshot!)).files.find((f) => f.p === 'Photos/gone-later.jpg')!;
  // the photo is deleted from the phone, and its stored piece rots on the computer
  fs.rmSync(path.join(p.files, 'Photos', 'gone-later.jpg'));
  const vault = p.platform.core.keyCheck(p.master).slice(0, 24);
  fs.writeFileSync(blobFiles(vault).find((f) => f.endsWith(gone.c[0]))!, 'rotten');
  const second = await p.backup({ scrub: true });
  assert.equal(second.status, 'warning', JSON.stringify(second));
  assert.equal(second.scrubbed, true);
  assert.match(second.warnings.join(' '), /1 stored pieces were damaged or missing/);
  assert.ok(second.snapshot && second.snapshot !== first.snapshot);
  // and it does not keep failing: the next ordinary run is fine
  p.write('Photos/another.jpg', crypto.randomBytes(100));
  assert.equal((await p.backup()).status, 'success');
});

test('a run that is out of time in the middle of a large file stops there', async () => {
  const p = await phone('big-file');
  p.write('Photos/long-video.mp4', crypto.randomBytes(CHUNK * 3 + 5));
  let pieces = 0;
  const first = await p.backup({ deadline: Date.now() + 60_000,
    progress: (x) => { if (x.phase === 'backup' && x.done > 0 && ++pieces === 2) p.platform.skewMs = 120_000; } });
  p.platform.skewMs = 0;
  assert.equal(first.status, 'partial', JSON.stringify(first));
  assert.equal(first.remaining, 1);
  p.platform.uploads = 0;
  const second = await p.backup();
  assert.equal(second.status, 'success');
  assert.ok(p.platform.uploads <= 3, 'pieces sent before the stop are not sent again'); // 2 left + the index
});

test('a source that was not looked at keeps its backups', async () => {
  const p = await phone('tablet');
  p.write('Photos/a.jpg', crypto.randomBytes(10_000));
  p.write('Docs/note.txt', 'note');
  assert.equal((await p.backup()).status, 'success');
  // photo access was taken away: only the documents are looked at from now on
  const onlyDocs = () => p.backup({ items: p.items().filter((i) => i.source !== 'Photos'), sources: ['Files/Documents'], keepLast: 1 });
  assert.equal((await onlyDocs()).status, 'success');
  assert.equal((await onlyDocs()).status, 'success');
  const backups = await listBackups(p.session);
  assert.equal(backups.length, 2); // the newest documents-only one, and the last one that has the photos
  assert.ok(backups.some((b) => b.sources!.includes('Photos')));
});

test('a run that is out of time stops cleanly and the next one continues', async () => {
  const p = await phone('slow');
  for (let i = 0; i < 6; i++) p.write('Photos/' + i + '.jpg', crypto.randomBytes(20_000));
  let calls = 0;
  const realNow = p.platform.now;
  const first = await p.backup({ deadline: Date.now() + 1000,
    progress: () => { if (++calls === 8) p.platform.skewMs = 5000; } });
  p.platform.skewMs = 0;
  p.platform.now = realNow;
  assert.equal(first.status, 'partial', JSON.stringify(first));
  assert.ok(first.remaining! > 0 && first.remaining! < 6);
  assert.deepEqual(await listBackups(p.session), []); // nothing is called a backup until it is complete

  p.platform.uploads = 0;
  const second = await p.backup();
  assert.equal(second.status, 'success');
  assert.equal(second.newFiles, first.remaining);
  assert.equal(p.platform.uploads, first.remaining! + 1);
});

test('a dropped connection is retried; a dead one is reported plainly', async () => {
  const p = await phone('flaky');
  p.write('Photos/a.jpg', crypto.randomBytes(10_000));
  tuning.retryMs = 1;
  let failures = 2;
  p.platform.beforeUpload = () => { if (failures-- > 0) throw new TypeError('network down'); };
  const ok = await p.backup();
  assert.equal(ok.status, 'success', JSON.stringify(ok));

  p.write('Photos/b.jpg', crypto.randomBytes(10_000));
  p.platform.beforeUpload = () => { throw new TypeError('network down'); };
  tuning.retryMs = 1; // do not sit through the pauses
  const dead = await p.backup();
  tuning.retryMs = 1000;
  assert.equal(dead.status, 'failed');
  assert.equal(dead.code, 'unreachable');
  assert.match(dead.error!, /Tailscale/);
});

test('a file that cannot be read is skipped with a warning', async () => {
  const p = await phone('partial');
  p.write('Photos/good.jpg', crypto.randomBytes(10_000));
  const items = [...p.items(), { key: 'gone', source: 'Photos', path: 'Photos/gone.jpg',
                                 uri: path.join(p.files, 'nope.jpg'), size: 5, mtime: 1 }];
  const result = await p.backup({ items });
  assert.equal(result.status, 'warning');
  assert.equal(result.files, 1);
  assert.match(result.warnings[0], /Photos\/gone\.jpg/);

  // The file stays unreadable. That is reported every time, but it is not a change:
  // no new backup is written, so history is not used up by repeating the same one.
  const again = await p.backup({ items });
  assert.equal(again.status, 'warning');
  assert.equal(again.unchanged, true);
  assert.equal(again.snapshot, result.snapshot);
  assert.equal((await listBackups(p.session)).length, 1);
});

test('two photos with the same name both get a place', async () => {
  const p = await phone('twins');
  p.write('x/1.jpg', 'one');
  p.write('y/1.jpg', 'two');
  const items = p.items().map((i) => ({ ...i, path: 'Photos/2026/IMG.jpg' }));
  const result = await p.backup({ items });
  assert.equal(result.status, 'success');
  assert.deepEqual((await manifest(p.session, result.snapshot!)).files.map((f) => f.p),
    ['Photos/2026/IMG (2).jpg', 'Photos/2026/IMG.jpg']);
});

test('the phone restores its own files and checks every byte', async () => {
  const p = await phone('restore');
  const big = crypto.randomBytes(CHUNK + 999);
  p.write('Photos/big.mp4', big);
  p.write('Photos/empty.jpg', '');
  const done = await p.backup();
  const m = await manifest(p.session, done.snapshot!);
  const out = path.join(p.dir, 'back.mp4');
  let seen = 0;
  await restoreFile(p.session, m.files.find((f) => f.p === 'Photos/big.mp4')!, out, (n) => { seen += n; });
  assert.deepEqual(fs.readFileSync(out), big);
  assert.equal(seen, big.length);
  await restoreFile(p.session, m.files.find((f) => f.p === 'Photos/empty.jpg')!, out);
  assert.equal(fs.statSync(out).size, 0);

  // damage one stored piece on the computer: the restore refuses it, the check finds it,
  // and the next backup sends it again
  const vault = p.platform.core.keyCheck(p.master).slice(0, 24);
  const victim = blobFiles(vault)[0];
  fs.writeFileSync(victim, 'rotten');
  await assert.rejects(restoreFile(p.session, m.files.find((f) => f.p === 'Photos/big.mp4')!, out),
    (e: BackupError) => e.code === 'integrity');
  assert.equal(fs.existsSync(out), false);
  const verdict = await runVerify(p.session);
  assert.equal(verdict.status, 'failed');
  assert.match(verdict.error!, /1 stored pieces were damaged or missing/);
  const again = await p.backup();
  assert.equal(again.status, 'success', JSON.stringify(again));
  assert.equal(again.newFiles, 1);
  assert.equal((await runVerify(p.session)).status, 'success');
});

test('a new phone with the recovery key reaches the old phone\'s backups; a wrong key reaches nothing', async () => {
  const old = await phone('old-phone');
  old.write('Photos/a.jpg', crypto.randomBytes(5000));
  await old.backup();
  const fresh = await phone('new-phone');
  const key = fresh.platform.core.decodeRecoveryKey(old.platform.core.encodeRecoveryKey(old.master));
  const session: Session = { ...fresh.session, master: key,
    client: new Client(fresh.platform, fresh.server, fresh.platform.core.keyCheck(key).slice(0, 24)) };
  const backups = await listBackups(session);
  assert.equal(backups.length, 1);
  assert.equal(backups[0].host, 'old-phone');
  await assert.rejects(listBackups(fresh.session), (e: BackupError) => e.code === 'no_backups');
});

test('a phone that was removed on the computer, or whose clock is wrong, is told so', async () => {
  const p = await phone('removed');
  p.write('Photos/a.jpg', 'a');
  p.platform.skewMs = 3600_000;
  const late = await p.backup();
  assert.equal(late.code, 'clock');
  p.platform.skewMs = 0;
  assert.equal(python(['phone', 'remove', p.server.device]).code, 0);
  const gone = await p.backup();
  assert.equal(gone.status, 'failed');
  assert.equal(gone.code, 'not_paired');
});
