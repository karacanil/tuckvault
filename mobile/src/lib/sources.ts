/**
 * Finds what there is to back up on this phone: photos and videos from the
 * library, and files from folders.
 */

import { Directory, File, Paths } from 'expo-file-system';
import { Album, Asset, AssetField, MediaType, Query, getPermissionsAsync, requestPermissionsAsync } from 'expo-media-library';
import { Platform as RN } from 'react-native';

import { tmpDir } from './device';
import Native, { inExpoGo } from './native';
import type { Item } from './engine';
import { clean, photoPath, toMs } from './naming';
import type { ChosenAlbum, Folder, Settings } from './store';

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

/** False where the photo library cannot be had at all: Expo Go on Android is not allowed to see it. */
export const photosPossible = !(inExpoGo && RN.OS === 'android');

export async function photoAccess(): Promise<PhotoAccess> {
  try {
    const got = await getPermissionsAsync(false, ['photo', 'video']);
    if (got.granted) return got.accessPrivileges === 'limited' ? 'limited' : 'all';
    return got.canAskAgain ? 'ask' : 'none';
  } catch {
    return 'none'; // the photo library is not there at all (Expo Go on Android)
  }
}

export async function askPhotoAccess(): Promise<PhotoAccess> {
  try {
    const got = await requestPermissionsAsync(false, ['photo', 'video']);
    if (got.granted) return got.accessPrivileges === 'limited' ? 'limited' : 'all';
    return got.canAskAgain ? 'ask' : 'none';
  } catch {
    return 'none';
  }
}

/**
 * Where a photo in the iOS library can be read from. The native module reads the library's own
 * file. Without it (inside Expo Go) that file may be out of reach; then the phone is asked for a
 * copy, one at a time, which the next photo replaces.
 */
async function iosPhoto(id: string): Promise<string> {
  const uri = await new Asset(id).getUri();
  if (!Native.plainJs) return uri;
  try {
    new File(uri).open().close();
    return uri;
  } catch {
    const staged = new File(tmpDir(), 'photo-in-hand');
    if (staged.exists) staged.delete();
    const legacy: typeof import('expo-file-system/legacy') = require('expo-file-system/legacy');
    await legacy.copyAsync({ from: id.startsWith('ph://') ? id : 'ph://' + id, to: staged.uri });
    return staged.uri;
  }
}

export type AlbumChoice = { id: string; title: string; count: number | null; own: boolean };

/**
 * The albums on this phone, the user's own first, each with how many items it holds. On an iPhone
 * also the ones the phone keeps by itself, such as Favorites and Screenshots.
 */
export async function listAlbums(): Promise<AlbumChoice[]> {
  const found: { id: string; title: string; type: string | null }[] = await Album.getAlbumsMetadata();
  if (RN.OS === 'ios') {
    try {
      for (const album of await Album.getSmartAlbums()) {
        if (found.some((f) => f.id === album.id)) continue;
        found.push({ id: album.id, title: await album.getTitle().catch(() => ''), type: 'smartAlbum' });
      }
    } catch {
      // only the user's own albums are offered
    }
  }
  const out: AlbumChoice[] = [];
  for (const album of found) {
    let count: number | null = null;
    try {
      count = await new Album(album.id).getAssetCount();
    } catch {
      // shown without a number
    }
    if (count === 0) continue;
    out.push({ id: album.id, title: album.title || 'Untitled album', count, own: album.type !== 'smartAlbum' });
  }
  out.sort((a, b) => Number(b.own) - Number(a.own) || a.title.localeCompare(b.title));
  return out;
}

async function scanLibrary(settings: Settings, out: Scan): Promise<void> {
  const access = await photoAccess();
  if (access === 'none' || access === 'ask') {
    out.warnings.push('Tuckvault is not allowed to see your photos, so they were not backed up. ' +
      'Allow it in the phone\'s settings.');
    return; // not listed as a source: the photo backups already made are kept
  }
  // With access to selected photos only, what is seen is not the library. It is kept as a source
  // of its own, so these smaller backups never count against (and push out) backups of everything.
  // The same goes for chosen albums: they are backed up as a source of their own.
  const chosen = settings.albums || [];
  const source = chosen.length ? 'Photos (albums)' : access === 'limited' ? 'Photos (selected)' : 'Photos';
  const seen = new Set<string>(); // a photo can be in several albums
  let readable = 0;
  for (const album of chosen.length ? chosen : [null]) {
    try {
      await scanAssets(settings, source, album, seen, out);
      readable += 1;
    } catch (e) {
      if (!album) throw e;
      out.warnings.push('The album "' + album.title + '" could not be read, so it was not backed up. ' +
        'If it was deleted, choose the albums again in Settings.');
    }
  }
  if (readable) out.sources.push(source); // otherwise the backups already made are kept as they are
}

async function scanAssets(settings: Settings, source: string, album: ChosenAlbum | null, seen: Set<string>,
                          out: Scan): Promise<void> {
  const found: Item[] = [];
  let photos = 0;
  let videos = 0;
  for (let offset = 0; ; offset += PAGE) {
    let query = new Query().orderBy({ key: AssetField.CREATION_TIME, ascending: true });
    if (album) query = query.album(new Album(album.id));
    const page = await query.limit(PAGE).offset(offset).exeForMetadata();
    for (const asset of page) {
      const video = asset.mediaType === MediaType.VIDEO;
      if (!video && asset.mediaType !== MediaType.IMAGE) continue;
      if (video && !settings.videos) continue;
      const changed = toMs(asset.modificationTime);
      // Pictures that were copied onto the phone often carry no "taken" time; file them by their file time.
      const created = toMs(asset.creationTime) || changed;
      const id = asset.id;
      if (seen.has(id)) continue;
      seen.add(id);
      found.push({
        key: 'photo:' + id,
        source,
        path: photoPath(asset.filename, id, created),
        // Android names a photo by an address the native code can read directly; on iOS it has to be asked for.
        uri: RN.OS === 'android' ? id : () => iosPhoto(id),
        size: null,
        mtime: changed || created,
      });
      if (video) videos += 1;
      else photos += 1;
    }
    if (page.length < PAGE) break;
  }
  // Only now, so an album that fails halfway adds nothing.
  out.items.push(...found);
  out.photos += photos;
  out.videos += videos;
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
  if (settings.photos && photosPossible) await scanLibrary(settings, out);
  // (Inside Expo Go there is no Files-app folder: the app's storage there belongs to Expo Go.)
  const folders = RN.OS === 'ios' ? (settings.inbox && !inExpoGo ? [inboxFolder()] : []) : settings.folders;
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
