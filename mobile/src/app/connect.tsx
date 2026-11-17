import { router } from 'expo-router';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, View } from 'react-native';

import { BackupError, hello, normalizeAddress, pair } from '../lib/client';
import { device } from '../lib/device';
import { nextSetupStep } from '../lib/setup';
import { getSettings, setPairingSecret, updateSettings } from '../lib/store';
import { Button, Card, Field, H2, Notice, P, Screen } from '../ui/kit';
import { space } from '../ui/theme';

export default function Connect() {
  const [address, setAddress] = useState(getSettings().server?.url || '');
  const [code, setCode] = useState('');
  const [name, setName] = useState(getSettings().deviceName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setError(null);
    setBusy(true);
    try {
      const url = normalizeAddress(address);
      if (code.replace(/[^0-9A-Za-z]/g, '').length < 8) {
        throw new BackupError('code', 'Enter the 8-character code shown on your computer.');
      }
      const computer = await hello(device, url);
      const server = await pair(device, url, code, name.trim() || 'Phone', Platform.OS);
      try {
        setPairingSecret(server.secret);
      } catch {
        throw new BackupError('secure_storage', 'This phone could not keep the connection in its secure storage, ' +
          'so it is not connected. Restart the phone and try again with a new code.');
      }
      updateSettings({ server: { url, device: server.device, name: server.name || computer.name },
                       deviceName: name.trim() || 'Phone' });
      router.replace(nextSetupStep());
    } catch (e) {
      setError(e instanceof BackupError ? e.message : 'Something went wrong: ' + String(e).slice(0, 160));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <Screen footer={<Button testID="connect" title="Connect" icon="link" onPress={connect} busy={busy} />}>
        <Card style={{ marginBottom: space.xl }}>
          <H2>On your computer</H2>
          <P muted style={{ marginTop: space.sm }}>
            1. Make sure Tailscale is running there and on this phone.{'\n'}
            2. Open Tuckvault, go to Phones and choose Pair a phone.{'\n'}
            3. Type the address and the code it shows into the boxes below.
          </P>
          <P muted small style={{ marginTop: space.sm }}>
            Without the window: run “tuckvault phone pair” in a terminal.
          </P>
        </Card>
        <Field testID="address" label="Address" value={address} onChangeText={setAddress} autoCapitalize="none"
               keyboardType="url" placeholder="my-pc.tailnet.ts.net or 100.x.y.z" returnKeyType="next"
               hint="As shown on your computer. The port :8743 is added for you if you leave it out." />
        <Field testID="code" label="Pairing code" value={code} onChangeText={setCode} autoCapitalize="characters"
               placeholder="ABCD-EFGH" maxLength={12} returnKeyType="next"
               hint="Works once, for ten minutes." />
        <Field testID="device-name" label="Name of this phone" value={name} onChangeText={setName} maxLength={40}
               hint="Shown on your computer and in your backups." />
        {error ? <View style={{ marginBottom: space.lg }}><Notice testID="connect-error" kind="bad">{error}</Notice></View> : null}
      </Screen>
    </KeyboardAvoidingView>
  );
}
