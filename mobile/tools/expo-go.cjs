#!/usr/bin/env node
/**
 * Runs Tuckvault inside Expo Go on a phone, served over Tailscale:
 *
 *     npm run go            (in mobile/)
 *
 * The phone needs Expo Go and Tailscale, both signed in. This computer's Tailscale address
 * is used for the app's code as well as for the backups, so it works wherever the phone is.
 * `npm run go -- --lan` serves on the local network instead (the backups still go over Tailscale).
 *
 * Expo Go on an iPhone only opens projects of the account it is signed in to, so this asks
 * you to sign in to Expo here first if you have not yet.
 */

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const PORT = process.env.TUCKVAULT_EXPO_PORT || '8093';
const root = path.join(__dirname, '..');
const expo = [process.execPath, [path.join(root, 'node_modules', 'expo', 'bin', 'cli')]];
const run = (args, options = {}) => spawnSync(expo[0], [...expo[1], ...args], { cwd: root, stdio: 'inherit', ...options });

function tailscaleAddress() {
  const programs = ['tailscale', 'C:\\Program Files\\Tailscale\\tailscale.exe',
                    '/Applications/Tailscale.app/Contents/MacOS/Tailscale'];
  for (const program of programs) {
    if (path.isAbsolute(program) && !fs.existsSync(program)) continue;
    const got = spawnSync(program, ['ip', '-4'], { encoding: 'utf8' });
    const address = got.status === 0 ? got.stdout.trim().split(/\s+/)[0] : '';
    if (/^\d+\.\d+\.\d+\.\d+$/.test(address)) return address;
  }
  return null;
}

function lanAddress() {
  for (const list of Object.values(os.networkInterfaces())) {
    for (const a of list || []) {
      if (a.family === 'IPv4' && !a.internal && /^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(a.address)) return a.address;
    }
  }
  return null;
}

const lan = process.argv.includes('--lan');
const address = lan ? lanAddress() : tailscaleAddress();
if (!address) {
  console.error(lan ? 'No local network address found.'
    : 'Tailscale is not running on this computer (no address from "tailscale ip -4"). Start it, or use --lan.');
  process.exit(1);
}

const who = run(['whoami'], { stdio: ['ignore', 'pipe', 'pipe'], encoding: 'utf8' });
if (who.status !== 0 || /not logged in/i.test(String(who.stdout) + String(who.stderr))) {
  console.log('Expo Go on an iPhone needs you signed in to the same Expo account here and on the phone.\n');
  if (run(['login']).status !== 0) process.exit(1);
}

console.log('\nServing Tuckvault for Expo Go at exp://' + address + ':' + PORT);
console.log(lan ? 'The phone must be on this network.' : 'Switch Tailscale on on the phone, then scan the code with the phone\'s camera.');
console.log('In the app, the computer\'s address to type is this computer\'s Tailscale address or name.\n');
const done = run(['start', '--go', '--lan', '--port', PORT],
                 { env: { ...process.env, REACT_NATIVE_PACKAGER_HOSTNAME: address, EXPO_NO_TELEMETRY: '1' } });
process.exit(done.status ?? 0);
