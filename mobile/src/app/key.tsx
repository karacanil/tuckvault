import * as Clipboard from 'expo-clipboard';
import { router } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, View } from 'react-native';

import Native from '../lib/native';
import { BackupError } from '../lib/client';
import { backups } from '../lib/runner';
import { setMasterKey, updateSettings } from '../lib/store';
import { Button, Card, Check, Field, H2, Notice, P, Screen } from '../ui/kit';
import { KeyBox } from '../ui/KeyBox';
import { space } from '../ui/theme';

/** Setup step two: make a new backup key, or enter the recovery key of backups that already exist. */
export default function Key() {
  const [mode, setMode] = useState<'new' | 'existing'>('new');
  const fresh = useMemo(() => Native.newMasterKey(), []);
  const [saved, setSaved] = useState(false);
  const [copied, setCopied] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function copy() {
    try {
      await Clipboard.setStringAsync(Native.encodeRecoveryKey(fresh));
      setCopied(true);
    } catch {
      Alert.alert('Could not copy', 'Write the key down instead.');
    }
  }

  function useNew() {
    setMasterKey(fresh);
    updateSettings({ recoveryKeySaved: true });
    router.replace('/choose');
  }

  async function useExisting() {
    setError(null);
    let master: string;
    try {
      master = Native.decodeRecoveryKey(typed);
    } catch (e) {
      setError(String((e as Error).message || e).replace(/^.*?: /, '').slice(0, 200) || 'That is not a valid recovery key.');
      return;
    }
    setBusy(true);
    try {
      const found = await backups(master);
      setMasterKey(master);
      updateSettings({ recoveryKeySaved: true });
      Alert.alert('Backups found', 'This key opens ' + found.length + (found.length === 1 ? ' backup' : ' backups') +
        ' on your computer. New backups from this phone are added to them.',
        [{ text: 'Continue', onPress: () => router.replace('/choose') }]);
    } catch (e) {
      if (e instanceof BackupError && e.code === 'no_backups') {
        Alert.alert('No backups for this key', 'Your computer holds no backups made with this recovery key. ' +
          'You can still use it: this phone will start a new set of backups with it.', [
          { text: 'Go back', style: 'cancel' },
          { text: 'Use it anyway', onPress: () => {
            setMasterKey(master);
            updateSettings({ recoveryKeySaved: true });
            router.replace('/choose');
          } },
        ]);
      } else {
        setError(e instanceof BackupError ? e.message : 'Something went wrong: ' + String(e).slice(0, 160));
      }
    } finally {
      setBusy(false);
    }
  }

  if (mode === 'existing') {
    return (
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <Screen footer={<Button testID="use-key" title="Use this key" onPress={useExisting} busy={busy} disabled={!typed.trim()} />}>
          <H2>Enter your recovery key</H2>
          <P muted style={{ marginTop: space.sm, marginBottom: space.lg }}>
            The key you saved when you first set up Tuckvault on a phone. With it, this phone can restore those
            backups and continue them.
          </P>
          <Field testID="recovery-input" label="Recovery key" value={typed} onChangeText={setTyped} multiline
                 autoCapitalize="characters" placeholder="ABCD-EFGH-…" error={error}
                 hint="56 letters and digits. Dashes and spaces do not matter."
                 style={{ minHeight: 96, textAlignVertical: 'top', fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace' }} />
          <Button title="Make a new key instead" kind="ghost" onPress={() => { setError(null); setMode('new'); }} />
        </Screen>
      </KeyboardAvoidingView>
    );
  }

  return (
    <Screen footer={<Button testID="key-continue" title="Continue" onPress={useNew} disabled={!saved} />}>
      <H2>Save your recovery key</H2>
      <P muted style={{ marginTop: space.sm, marginBottom: space.lg }}>
        Your backups are locked with this key, and only this phone has it. If the phone is lost or broken, the
        recovery key is the only way to get your photos and files back.
      </P>
      <KeyBox value={Native.encodeRecoveryKey(fresh)} />
      <View style={{ height: space.md }} />
      <Button testID="copy-key" title={copied ? 'Copied' : 'Copy'} icon={copied ? 'checkmark-circle' : 'copy-outline'}
              kind="secondary" onPress={copy} />
      {copied ? (
        <P testID="key-copied" muted small style={{ marginTop: space.sm, textAlign: 'center' }}>
          Copied. Paste it into your password manager or notes now.
        </P>
      ) : null}
      <View style={{ marginVertical: space.lg }}>
        <Notice kind="warn" title="Keep it somewhere safe, away from this phone">
          A password manager or a piece of paper works well. Nobody can reset it for you, and without it the
          backups cannot be opened.
        </Notice>
      </View>
      <Card>
        <Check testID="key-saved" checked={saved} onChange={setSaved} label="I have saved my recovery key." />
      </Card>
      <View style={{ height: space.md }} />
      <Button testID="have-key" title="I already have a recovery key" kind="ghost" onPress={() => setMode('existing')} />
    </Screen>
  );
}
