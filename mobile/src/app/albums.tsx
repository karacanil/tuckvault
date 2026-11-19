import { useEffect, useState } from 'react';
import { View } from 'react-native';

import { listAlbums } from '../lib/sources';
import type { AlbumChoice } from '../lib/sources';
import { updateSettings, useSettings } from '../lib/store';
import { Card, Check, Loading, Notice, P, Screen } from '../ui/kit';
import { space } from '../ui/theme';

/** Choose between backing up the whole photo library and only some albums. */
export default function Albums() {
  const settings = useSettings();
  const [albums, setAlbums] = useState<AlbumChoice[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    listAlbums().then(setAlbums).catch(() => setFailed(true));
  }, []);

  const chosen = settings.albums;
  // An album that was chosen earlier and is gone from the phone stays listed, so it can be unticked.
  const gone = albums ? chosen.filter((c) => !albums.some((a) => a.id === c.id)) : [];

  function toggle(id: string, title: string, on: boolean) {
    updateSettings({ albums: on ? [...chosen.filter((c) => c.id !== id), { id, title }] : chosen.filter((c) => c.id !== id) });
  }

  return (
    <Screen>
      <P muted style={{ marginBottom: space.lg }}>
        Back up every photo on this phone, or only the albums you tick. Photos in other albums are then left out.
      </P>
      <Card>
        <Check testID="all-photos" checked={chosen.length === 0} label="All photos"
               onChange={(on) => { if (on) updateSettings({ albums: [] }); }} />
      </Card>
      <View style={{ height: space.lg }} />
      {failed ? (
        <Notice kind="bad">The albums on this phone could not be listed. Check that Tuckvault may see your photos.</Notice>
      ) : !albums ? <Loading text="Looking for albums…" /> : albums.length + gone.length === 0 ? (
        <Notice kind="info">There are no albums with photos on this phone.</Notice>
      ) : (
        <Card>
          {albums.map((album, i) => (
            <Check key={album.id} testID={'album-' + i} checked={chosen.some((c) => c.id === album.id)}
                   label={album.title + (album.count === null ? '' : '  (' + album.count + ')')}
                   onChange={(on) => toggle(album.id, album.title, on)} />
          ))}
          {gone.map((album) => (
            <Check key={album.id} checked label={album.title + '  (no longer on this phone)'}
                   onChange={() => toggle(album.id, album.title, false)} />
          ))}
        </Card>
      )}
      {chosen.length ? (
        <View style={{ marginTop: space.lg }}>
          <Notice kind="info" title="Backups of chosen albums are kept apart">
            They do not replace backups of all your photos that were made earlier.
          </Notice>
        </View>
      ) : null}
    </Screen>
  );
}
