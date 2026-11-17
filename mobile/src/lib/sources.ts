/**
 * Finds what there is to back up on this phone: photos and videos from the
 * library, and files from folders.
 */

import { Directory, Paths } from 'expo-file-system';
import { Asset, AssetField, MediaType, Query, getPermissionsAsync, requestPermissionsAsync } from 'expo-media-library';
import { Platform as RN } from 'react-native';

import Native from '../../modules/tuckvault-core';
import type { Item } from './engine';
import { clean, photoPath, toMs } from './naming';
import type { Folder, Settings } from './store';

const PAGE = 500;

export type Scan = {
  items: Item[];
  /** Every source that was looked at, also the ones that turned out to hold nothing. */
  sources: string[];
  /** Things the user should know, such as a folder that is no longer reachable. */
  warnings: string[];
  photos: number;
  videos: number;
  files: number;
};

export type PhotoAccess = 'all' | 'limited' | 'none' | 'ask';

export async function photoAccess(): Promise<PhotoAccess> {
  const got = await getPermissionsAsync(false, ['photo', 'video']);
  if (got.granted) return got.accessPrivileges === 'limited' ? 'limited' : 'all';
  return got.canAskAgain ? 'ask' : 'none';
}

export async function askPhotoAccess(): Promise<PhotoAccess> {
  const got = await requestPermissionsAsync(false, ['photo', 'video']);
  if (got.granted) return got.accessPrivileges === 'limited' ? 'limited' : 'all';
  return got.canAskAgain ? 'ask' : 'none';
}

async function scanLibrary(settings: Settings, out: Scan): Promise<void> {
  const access = await photoAccess();
  if (access === 'none' || access === 'ask') {
    out.warnings.push('Tuckvault is not allowed to see your photos, so they were not backed up. ' +
      'Allow it in the phone\'s settings.');
    return; // not listed as a source: the photo backups already made are kept
  }
  out.sources.push('Photos');
  for (let offset = 0; ; offset += PAGE) {
    const page = await new Query().orderBy({ key: AssetField.CREATION_TIME, ascending: true })
      .limit(PAGE).offset(offset).exeForMetadata();
    for (const asset of page) {
      const video = asset.mediaType === MediaType.VIDEO;
      if (!video && asset.mediaType !== MediaType.IMAGE) continue;
      if (video && !settings.videos) continue;
      const changed = toMs(asset.modificationTime);
      // Pictures that were copied onto the phone often carry no "taken" time; file them by their file time.
      const created = toMs(asset.creationTime) || changed;
      const id = asset.id;
      out.items.push({
        key: 'photo:' + id,
        source: 'Photos',
        path: photoPath(asset.filename, id, created),
        // Android names a photo by an address the native code can read directly; on iOS it has to be asked for.
        uri: RN.OS === 'android' ? id : () => new Asset(id).getUri(),
        size: null,
        mtime: changed || created,
      });
      if (video) out.videos += 1;
      else out.photos += 1;
    }
    if (page.length < PAGE) break;
  }
}

/** The name a folder's files are filed under: "Files/<name>". */
export function folderSource(folder: Folder): string {
  return 'Files/' + clean(folder.name || 'Folder');
}

async function scanFolder(folder: Folder, source: string, out: Scan): Promise<void> {
  let entries;
  try {
    entries = await Native.listTree(folder.uri);
  } catch {
    out.warnings.push('The folder "' + folder.name + '" could not be opened, so it was not backed up. ' +
      'Remove it and add it again in Settings.');
    return;
  }
  out.sources.push(source);
  for (const entry of entries) {
    if (entry.path.split('/').some((part) => part.startsWith('.'))) continue; // hidden files and folders
    out.items.push({ key: 'file:' + entry.uri, source, path: source + '/' + entry.path.split('/').map(clean).join('/'),
                     uri: entry.uri, size: entry.size, mtime: Math.round(entry.mtime || 0) });
    out.files += 1;
  }
}

/** The folder iOS shows in the Files app as "Tuckvault". */
export function inboxFolder(): Folder {
  return { uri: Paths.document.uri, name: 'Tuckvault' };
}

export async function scan(settings: Settings): Promise<Scan> {
  const out: Scan = { items: [], sources: [], warnings: [], photos: 0, videos: 0, files: 0 };
  if (settings.photos) await scanLibrary(settings, out);
  const folders = RN.OS === 'ios' ? (settings.inbox ? [inboxFolder()] : []) : settings.folders;
  const taken = new Set<string>();
  for (const folder of folders) {
    let source = folderSource(folder);
    for (let n = 2; taken.has(source); n++) source = folderSource(folder) + ' (' + n + ')';
    taken.add(source);
    await scanFolder(folder, source, out);
  }
  return out;
}

/** Let the user choose a folder to back up (Android). Returns null if they backed out. */
export async function pickFolder(): Promise<Folder | null> {
  try {
    const dir = await Directory.pickDirectoryAsync();
    return { uri: dir.uri, name: dir.name || 'Folder' };
  } catch {
    return null;
  }
}
