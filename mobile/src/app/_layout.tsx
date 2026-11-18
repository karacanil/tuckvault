import { Stack } from 'expo-router';
import { useEffect } from 'react';

import '../lib/background'; // defines the automatic-backup task as soon as the app's code loads
import { syncAutoBackup } from '../lib/background';
import { prepare } from '../lib/notify';
import { clearRestored } from '../lib/runner';
import { useTheme } from '../ui/theme';

export default function Layout() {
  const t = useTheme();
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
