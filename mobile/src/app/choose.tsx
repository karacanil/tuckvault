import { router } from 'expo-router';
import { useState } from 'react';

import { syncAutoBackup } from '../lib/background';
import { ask } from '../lib/notify';
import { updateSettings, useSettings } from '../lib/store';
import { Button, Card, P, Screen, SectionTitle, ToggleRow } from '../ui/kit';
import { SourcesEditor } from '../ui/SourcesEditor';

/** Setup step three: what to back up, and whether to do it automatically. */
export default function Choose() {
  const settings = useSettings();
  const [busy, setBusy] = useState(false);

  async function finish() {
    setBusy(true);
    await ask().catch(() => false); // so the phone can say when a backup finished or needs attention
    updateSettings({ onboarded: true });
    await syncAutoBackup();
    router.dismissAll();
    router.replace('/(tabs)');
  }

  return (
    <Screen footer={<Button testID="finish-setup" title="Finish" onPress={finish} busy={busy} />}>
      <P muted>Choose what Tuckvault keeps safe. You can change this at any time in Settings.</P>
      <SourcesEditor />
      <SectionTitle>Automatic</SectionTitle>
      <Card padded={false}>
        <ToggleRow testID="toggle-auto" first icon="sync-outline" title="Back up automatically" value={settings.auto}
                   onChange={(v) => updateSettings({ auto: v })}
                   detail="A few times a day, in the background, when your computer can be reached." />
        <ToggleRow icon="wifi-outline" title="Only on Wi-Fi" value={settings.wifiOnly} disabled={!settings.auto}
                   onChange={(v) => updateSettings({ wifiOnly: v })} detail="Saves your mobile data." />
      </Card>
    </Screen>
  );
}
