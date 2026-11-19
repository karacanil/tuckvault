import { useURL } from 'expo-linking';
import { router, Stack, useGlobalSearchParams, useRootNavigationState } from 'expo-router';
import { useEffect } from 'react';

import '../lib/background'; // defines the automatic-backup task as soon as the app's code loads
import { syncAutoBackup } from '../lib/background';
import { pairingParams } from '../lib/client';
import { prepare } from '../lib/notify';
import { clearRestored } from '../lib/runner';
import { useTheme } from '../ui/theme';

let openedCode = ''; // the scanned code this run of the app has already shown

export default function Layout() {
  const t = useTheme();
  // A scanned pairing code opens the Connect screen, whatever the router made of the link.
  const link = useURL();
  const ready = !!useRootNavigationState()?.key;
  const shown = useGlobalSearchParams<{ c?: string }>().c;
  useEffect(() => {
    const scanned = pairingParams(link);
    if (!ready || !scanned || scanned.c === openedCode) return;
    openedCode = scanned.c;
    if (shown !== scanned.c) router.push({ pathname: '/connect', params: scanned });
  }, [link, ready, shown]);
  useEffect(() => {
    prepare().catch(() => {});
    syncAutoBackup();
    clearRestored();
  }, []);
  return (
    <>
      <Stack screenOptions={{
        headerStyle: { backgroundColor: t.bg },
        headerShadowVisible: false,
        headerTintColor: t.primary,
        headerTitleStyle: { color: t.text, fontWeight: '600' },
        headerBackButtonDisplayMode: 'minimal',
        contentStyle: { backgroundColor: t.bg },
      }}>
        <Stack.Screen name="index" options={{ headerShown: false }} />
        <Stack.Screen name="welcome" options={{ headerShown: false }} />
        <Stack.Screen name="connect" options={{ title: 'Connect to your computer' }} />
        <Stack.Screen name="key" options={{ title: 'Your backup key' }} />
        <Stack.Screen name="choose" options={{ title: 'What to back up' }} />
        <Stack.Screen name="(tabs)" options={{ headerShown: false }} />
        <Stack.Screen name="backup/[name]" options={{ title: 'Backup' }} />
        <Stack.Screen name="recovery-key" options={{ title: 'Recovery key' }} />
        <Stack.Screen name="history" options={{ title: 'Activity' }} />
      </Stack>
    </>
  );
}
