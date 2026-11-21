import Constants from 'expo-constants';
import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Alert, Linking, Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { autoBackupPossible, syncAutoBackup } from '../../lib/background';
import { ago } from '../../lib/format';
import { allowed, ask, scheduleOverdueReminder } from '../../lib/notify';
import { startVerify, useActivity } from '../../lib/runner';
import { resetEverything, updateSettings, useRunState, useSettings } from '../../lib/store';
import { Card, H1, Notice, P, Row, Screen, SectionTitle, ToggleRow } from '../../ui/kit';
import { SourcesEditor } from '../../ui/SourcesEditor';
import { radius, space, useTheme } from '../../ui/theme';

const KEEP: [number, string][] = [[10, '10'], [30, '30'], [90, '90'], [0, 'All']];
const REMIND: [number, string][] = [[3, '3 days'], [7, '7 days'], [14, '14 days'], [0, 'Never']];

function Choice<T extends number>({ options, value, onChange, label }: {
  options: [T, string][]; value: T; onChange: (v: T) => void; label: string;
}) {
  const t = useTheme();
  return (
    <View accessibilityRole="radiogroup" accessibilityLabel={label}
          style={{ flexDirection: 'row', backgroundColor: t.cardAlt, borderRadius: radius.sm, padding: 3, marginTop: space.sm }}>
      {options.map(([v, text]) => (
        <Pressable key={v} accessibilityRole="radio" accessibilityState={{ selected: v === value }} accessibilityLabel={text}
                   onPress={() => onChange(v)}
                   style={{ flex: 1, paddingVertical: 9, borderRadius: radius.sm - 2, alignItems: 'center',
                            backgroundColor: v === value ? t.card : 'transparent' }}>
          <Text style={{ color: v === value ? t.primary : t.muted, fontSize: 14, fontWeight: '600' }}>{text}</Text>
        </Pressable>
      ))}
    </View>
  );
}

export default function Settings() {
  const insets = useSafeAreaInsets();
  const settings = useSettings();
  const run = useRunState();
  const activity = useActivity();
  const [notifications, setNotifications] = useState(true);

  useEffect(() => {
    allowed().then(setNotifications).catch(() => {});
  }, []);

  function setAuto(patch: Parameters<typeof updateSettings>[0]) {
    updateSettings(patch);
    syncAutoBackup();
  }

  async function check() {
    const result = await startVerify();
    if (!result) return;
    Alert.alert(result.status === 'success' ? 'All backups are complete' : 'The check found a problem',
      result.status === 'success' ? 'Every stored piece of every backup was read again on your computer and is undamaged.'
        : result.error || 'Unknown problem.');
  }

  function disconnect() {
    Alert.alert('Disconnect this phone?',
      'Tuckvault forgets your computer and the backup key on this phone. The backups on your computer are kept, ' +
      'and you can reach them again with your recovery key.\n\nWithout the recovery key they cannot be opened any more.', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Disconnect', style: 'destructive', onPress: async () => {
        await resetEverything();
        await syncAutoBackup();
        router.dismissAll();
        router.replace('/welcome');
      } },
    ]);
  }

  return (
    <View style={{ flex: 1 }}>
      <Screen padTop>
        <H1>Settings</H1>
        <SourcesEditor />

        <SectionTitle>Automatic backup</SectionTitle>
        {!autoBackupPossible ? (
          <Notice kind="info" title="Not in the Expo Go version">
            Expo Go cannot run apps in the background. Open Tuckvault and tap Back up now; keep it open until it is done.
          </Notice>
        ) : (
        <Card padded={false}>
          <ToggleRow testID="settings-auto" first icon="sync-outline" title="Back up automatically" value={settings.auto}
                     onChange={(v) => setAuto({ auto: v })}
                     detail="A few times a day in the background. The phone decides the exact moments." />
          <ToggleRow icon="wifi-outline" title="Only on Wi-Fi" value={settings.wifiOnly} disabled={!settings.auto}
                     onChange={(v) => setAuto({ wifiOnly: v })} detail="Backups you start yourself always run." />
          <ToggleRow icon="battery-charging-outline" title="Only while charging" value={settings.chargingOnly} disabled={!settings.auto}
                     onChange={(v) => setAuto({ chargingOnly: v })} />
        </Card>
        )}

        <SectionTitle>Notifications</SectionTitle>
        {!notifications ? (
          <View style={{ marginBottom: space.md }}>
            <Notice kind="warn" title="Notifications are off for Tuckvault" action="Allow notifications"
                    onAction={async () => { if (!(await ask())) Linking.openSettings(); setNotifications(await allowed()); }}>
              Tuckvault cannot tell you when backups stop working.
            </Notice>
          </View>
        ) : null}
        <Card padded={false}>
          <ToggleRow first icon="checkmark-done-outline" title="When a backup stored new files" value={settings.notifyFinished}
                     onChange={(v) => updateSettings({ notifyFinished: v })} />
          <ToggleRow icon="alert-circle-outline" title="When a backup has a problem" value={settings.notifyProblems}
                     onChange={(v) => updateSettings({ notifyProblems: v })} />
          <View style={{ padding: space.lg }}>
            <P>Remind me when there was no backup for</P>
            <Choice label="Remind me after" options={REMIND} value={settings.overdueDays}
                    onChange={(v) => { updateSettings({ overdueDays: v }); scheduleOverdueReminder(); }} />
          </View>
        </Card>

        <SectionTitle>Storage</SectionTitle>
        <Card>
          <P>Backups to keep</P>
          <Choice label="Backups to keep" options={KEEP} value={settings.keepLast} onChange={(v) => updateSettings({ keepLast: v })} />
          <P muted small style={{ marginTop: space.sm }}>
            {settings.keepLast ? 'A photo you delete from this phone stays restorable until ' + settings.keepLast + ' newer backups exist.'
              : 'Nothing is ever removed from your backups. They grow with everything you add.'}
          </P>
        </Card>

        <SectionTitle>Safety</SectionTitle>
        <Card padded={false}>
          <Row testID="show-recovery-key" first icon="key-outline" title="Recovery key" detail="The only way to restore on a new phone"
               onPress={() => router.push('/recovery-key')} />
          <Row testID="check-backups" icon="pulse-outline" title={activity.kind === 'verify' ? 'Checking…' : 'Check all backups now'}
               detail={'Reads every stored piece again on your computer. Last check: ' + ago(run.lastScrub)}
               onPress={activity.kind ? undefined : check} />
        </Card>

        <SectionTitle>Computer</SectionTitle>
        <Card padded={false}>
          <Row first icon="desktop-outline" title={settings.server?.name || 'Not connected'} detail={settings.server?.url} />
          <Row icon="phone-portrait-outline" title="This phone" detail={settings.deviceName} />
          <Row testID="reconnect" icon="link-outline" title="Connect again"
               detail="Pair with a new code from the computer, for example after this phone was removed there. The recovery key stays the same."
               onPress={() => router.push('/connect')} />
          <Row testID="disconnect" icon="log-out-outline" danger title="Disconnect this phone" onPress={disconnect} />
        </Card>

        <P muted small style={{ textAlign: 'center', marginTop: space.xl, marginBottom: insets.bottom }}>
          Tuckvault {Constants.expoConfig?.version || ''}  ·  open source, MIT licence
        </P>
      </Screen>
    </View>
  );
}
