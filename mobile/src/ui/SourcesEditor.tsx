import { useEffect, useState } from 'react';
import { Alert, Linking, Platform, View } from 'react-native';

import { inExpoGo } from '../lib/native';
import { askPhotoAccess, photoAccess, photosPossible, pickFolder } from '../lib/sources';
import type { PhotoAccess } from '../lib/sources';
import { updateSettings, useSettings } from '../lib/store';
import { Button, Card, Notice, P, Row, SectionTitle, ToggleRow } from './kit';
import { space } from './theme';

/** Choose what is backed up: photos, videos, and folders. Used during setup and in Settings. */
export function SourcesEditor() {
  const settings = useSettings();
  const [access, setAccess] = useState<PhotoAccess | null>(null);

  useEffect(() => {
    photoAccess().then(setAccess).catch(() => setAccess('ask'));
  }, []);

  async function togglePhotos(on: boolean) {
    if (on) {
      const got = await askPhotoAccess();
      setAccess(got);
      if (got === 'none') {
        Alert.alert('Photos are blocked', 'Tuckvault is not allowed to see your photos. You can allow it in the phone\'s settings.', [
          { text: 'Not now', style: 'cancel' },
          { text: 'Open settings', onPress: () => Linking.openSettings() },
        ]);
      }
      if (got === 'none' || got === 'ask') return;
    }
    updateSettings({ photos: on });
  }

  async function addFolder() {
    const folder = await pickFolder();
    if (!folder) return;
    if (settings.folders.some((f) => f.uri === folder.uri)) return;
    updateSettings({ folders: [...settings.folders, folder] });
  }

  function removeFolder(uri: string, name: string) {
    Alert.alert('Stop backing up "' + name + '"?', 'Backups already made of it are kept until they age out.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Stop', style: 'destructive', onPress: () => updateSettings({ folders: settings.folders.filter((f) => f.uri !== uri) }) },
    ]);
  }

  const photosOn = settings.photos && (access === 'all' || access === 'limited');
  return (
    <View>
      <SectionTitle>Photos and videos</SectionTitle>
      {!photosPossible ? (
        <View style={{ marginBottom: space.md }}>
          <Notice kind="info" title="Not in the Expo Go version on Android">
            Android does not let Expo Go see the photo library. Folders you add below are backed up.
          </Notice>
        </View>
      ) : null}
      <Card padded={false}>
        <ToggleRow testID="toggle-photos" first icon="images-outline" title="Photos" value={photosOn} onChange={togglePhotos}
                   detail="Every photo on this phone, as the camera saved it." />
        <ToggleRow testID="toggle-videos" icon="videocam-outline" title="Videos" value={photosOn && settings.videos}
                   disabled={!photosOn} onChange={(v) => updateSettings({ videos: v })}
                   detail="Videos are large; the first backup can take a while." />
      </Card>
      {settings.photos && access === 'limited' ? (
        <View style={{ marginTop: space.md }}>
          <Notice kind="warn" title="Only some photos are allowed" action="Open settings" onAction={() => Linking.openSettings()}>
            You gave Tuckvault access to selected photos only, so only those are backed up. Allow all photos to back up everything.
          </Notice>
        </View>
      ) : null}

      <SectionTitle>Files</SectionTitle>
      {Platform.OS === 'ios' && inExpoGo ? (
        <Notice kind="info" title="Not in the Expo Go version">
          Inside Expo Go, Tuckvault has no folder in the Files app, so only photos and videos are backed up.
        </Notice>
      ) : Platform.OS === 'ios' ? (
        <Card padded={false}>
          <ToggleRow testID="toggle-inbox" first icon="folder-outline" title="The Tuckvault folder" value={settings.inbox}
                     onChange={(v) => updateSettings({ inbox: v })}
                     detail="Open the Files app, choose On My iPhone, then Tuckvault. Everything you put in that folder is backed up." />
        </Card>
      ) : (
        <Card padded={false}>
          {settings.folders.length === 0 ? (
            <View style={{ padding: space.lg }}>
              <P muted>No folders yet. Add Documents, Downloads or any other folder you want kept safe.</P>
            </View>
          ) : settings.folders.map((folder, i) => (
            <Row key={folder.uri} first={i === 0} icon="folder-outline" title={folder.name}
                 right={<Button title="Remove" kind="ghost" onPress={() => removeFolder(folder.uri, folder.name)} style={{ minHeight: 36 }} />} />
          ))}
          <View style={{ padding: space.md }}>
            <Button testID="add-folder" title="Add a folder" icon="add" kind="secondary" onPress={addFolder} />
          </View>
        </Card>
      )}
    </View>
  );
}
