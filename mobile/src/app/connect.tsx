import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { ComponentRef } from 'react';
import { KeyboardAvoidingView, Platform, TextInput, View } from 'react-native';

import { BackupError, hello, normalizeAddress, pair, scannedPairing } from '../lib/client';
import { device } from '../lib/device';
import { nextSetupStep } from '../lib/setup';
import { getSettings, setPairingSecret, updateSettings } from '../lib/store';
import { Button, Card, Field, H2, Notice, P, Screen } from '../ui/kit';
import { space } from '../ui/theme';

export default function Connect() {
  // Scanning the code on the computer opens this screen with the address and a one-time token.
  const params = useLocalSearchParams<{ u?: string; c?: string }>();
  const fromLink = useMemo(() => scannedPairing(params.u, params.c), [params.u, params.c]);
  const [typing, setTyping] = useState(false);
  const scanned = typing ? null : fromLink;
  const [connected] = useState(getSettings().server); // as it was when this screen opened

  const [address, setAddress] = useState(connected?.url || '');
  const [code, setCode] = useState('');
  const [name, setName] = useState(getSettings().deviceName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const codeBox = useRef<ComponentRef<typeof TextInput>>(null);

  useEffect(() => { // a newly scanned code replaces whatever this screen was showing
    setTyping(false);
    setError(null);
  }, [params.u, params.c]);

  async function connect() {
    setError(null);
    setBusy(true);
    try {
      const url = scanned ? scanned.url : normalizeAddress(address);
      if (!scanned && code.replace(/[^0-9A-Za-z]/g, '').length < 8) {
        throw new BackupError('code', 'Enter the 8-character code shown on your computer.');
      }
      const computer = await hello(device, url);
      const server = await pair(device, url, scanned ? scanned.token : code, name.trim() || 'Phone', Platform.OS);
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
      <Screen footer={<Button testID="connect" title={scanned ? 'Connect to this computer' : 'Connect'} icon="link"
                              onPress={connect} busy={busy} />}>
        {scanned ? (
          <>
            <Card style={{ marginBottom: space.lg }}>
              <H2>Code scanned</H2>
              <P muted style={{ marginTop: space.sm }}>This phone will back up to the computer at</P>
              <P testID="scanned-host" style={{ marginTop: space.xs, fontWeight: '600' }}>{scanned.host}</P>
              <P muted small style={{ marginTop: space.sm }}>
                Connect only if you just scanned this from your own computer{'’'}s Tuckvault window.
                Tailscale must be switched on on this phone.
              </P>
            </Card>
            {connected ? (
              <View style={{ marginBottom: space.lg }}>
                <Notice testID="replaces-connection" kind="warn">
                  {`This phone is already connected to ${connected.name || 'a computer'}. Connecting replaces that connection.`}
                </Notice>
              </View>
            ) : null}
          </>
        ) : (
          <Card style={{ marginBottom: space.xl }}>
            <H2>On your computer</H2>
            <P muted style={{ marginTop: space.sm }}>
              1. Make sure Tailscale is running there and on this phone.{'\n'}
              2. Open Tuckvault, go to Phones and choose Pair a phone.{'\n'}
              3. Scan the square code it shows with this phone{'’'}s camera. That fills in everything here.
            </P>
            <P muted small style={{ marginTop: space.sm }}>
              No camera at hand? Type the address and the short code it shows into the boxes below.
              Without the window: run “tuckvault phone pair” in a terminal.
            </P>
          </Card>
        )}
        {scanned ? null : (
          <>
            <Field testID="address" label="Address" value={address} onChangeText={setAddress} autoCapitalize="none"
                   keyboardType="url" placeholder="my-pc.tailnet.ts.net or 100.x.y.z" returnKeyType="next"
                   submitBehavior="submit" onSubmitEditing={() => codeBox.current?.focus()}
                   hint="As shown on your computer. The port :8743 is added for you if you leave it out." />
            <Field testID="code" label="Pairing code" value={code} onChangeText={setCode} autoCapitalize="characters"
                   placeholder="ABCD-EFGH" maxLength={12} returnKeyType="go" inputRef={codeBox} onSubmitEditing={connect}
                   hint="Works once, for 30 minutes." />
          </>
        )}
        <Field testID="device-name" label="Name of this phone" value={name} onChangeText={setName} maxLength={40}
               hint="Shown on your computer and in your backups." />
        {error ? <View style={{ marginBottom: space.lg }}><Notice testID="connect-error" kind="bad">{error}</Notice></View> : null}
        {scanned ? (
          <Button testID="type-instead" title="Type the address and code instead" kind="ghost"
                  onPress={() => setTyping(true)} />
        ) : null}
      </Screen>
    </KeyboardAvoidingView>
  );
}
