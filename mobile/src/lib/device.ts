/**
 * The real device behind the engine's `Platform`: the native module for
 * encryption and file reading, expo-file-system for uploads and downloads.
 */

import { Directory, File, Paths, UploadType } from 'expo-file-system';
import { Platform as RN } from 'react-native';

import Native, { inExpoGo } from './native';
import type { HttpReply, Platform } from './platform';

function ensure(dir: Directory): Directory {
  if (!dir.exists) dir.create({ intermediates: true });
  return dir;
}

/**
 * Where Tuckvault keeps its own files. On iOS the Documents folder is shown to the user
 * in the Files app (it is the "Tuckvault" folder they drop files into), so the app's own
 * records live beside it instead.
 */
export function dataDir(): Directory {
  // (Inside Expo Go the app may only use its own corner of Expo Go's storage.)
  if (RN.OS === 'ios' && !inExpoGo) {
    return ensure(new Directory(Paths.document.parentDirectory, 'Library', 'Application Support', 'tuckvault'));
  }
  return ensure(new Directory(Paths.document, 'tuckvault'));
}

export function tmpDir(): Directory {
  return ensure(new Directory(Paths.cache, 'tuckvault-tmp'));
}

export function manifestDir(): string {
  const uri = ensure(new Directory(dataDir(), 'manifests')).uri;
  return uri.endsWith('/') ? uri : uri + '/';
}

/** Leftovers of a run that was killed half way. */
export function clearTmp(): void {
  try {
    const dir = new Directory(Paths.cache, 'tuckvault-tmp');
    if (dir.exists) dir.delete();
  } catch {
    // they are only temporary files
  }
}

export const device: Platform = {
  core: Native,

  async request(method, url, headers, body): Promise<HttpReply> {
    const res = await fetch(url, { method, headers, body });
    return { status: res.status, text: await res.text() };
  },

  async upload(url, headers, path): Promise<HttpReply> {
    const res = await new File(path).upload(url, {
      httpMethod: 'PUT',
      uploadType: UploadType.BINARY_CONTENT,
      headers: { ...headers, 'Content-Type': 'application/octet-stream' },
    });
    return { status: res.status, text: res.body };
  },

  async download(url, headers, path): Promise<HttpReply> {
    const target = new File(path);
    try {
      await File.downloadFileAsync(url, target, { headers, idempotent: true });
      return { status: 200, text: '' };
    } catch {
      // The download call does not say why it failed. Ask again in a way that does:
      // an answer with an error status carries the computer's own explanation.
      const res = await fetch(url, { headers });
      if (res.status !== 200) return { status: res.status, text: await res.text() };
      const bytes = new Uint8Array(await res.arrayBuffer());
      if (target.exists) target.delete();
      target.create();
      target.writeSync(bytes);
      return { status: 200, text: '' };
    }
  },

  tmp(name) {
    return new File(tmpDir(), name).uri;
  },

  remove(path) {
    try {
      const file = new File(path);
      if (file.exists) file.delete();
    } catch {
      // nothing to remove
    }
  },

  readText(path) {
    try {
      const file = new File(path);
      return file.exists ? file.textSync() : null;
    } catch {
      return null;
    }
  },

  writeText(path, text) {
    const file = new File(path);
    if (!file.exists) file.create({ intermediates: true });
    file.writeSync(text);
  },

  now: () => Date.now(),
};
