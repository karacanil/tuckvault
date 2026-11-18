import { Ionicons } from '@expo/vector-icons';
import { Tabs } from 'expo-router';

import { useTheme } from '../../ui/theme';

export default function TabsLayout() {
  const t = useTheme();
  return (
    <Tabs screenOptions={{
      headerShown: false,
      tabBarActiveTintColor: t.primary,
      tabBarInactiveTintColor: t.faint,
      tabBarStyle: { backgroundColor: t.card, borderTopColor: t.border },
      tabBarLabelStyle: { fontSize: 12, fontWeight: '600' },
      sceneStyle: { backgroundColor: t.bg },
    }}>
      <Tabs.Screen name="index" options={{ title: 'Home', tabBarButtonTestID: 'tab-home',
        tabBarIcon: ({ color, size }) => <Ionicons name="shield-checkmark" size={size} color={String(color)} /> }} />
      <Tabs.Screen name="backups" options={{ title: 'Backups', tabBarButtonTestID: 'tab-backups',
        tabBarIcon: ({ color, size }) => <Ionicons name="time" size={size} color={String(color)} /> }} />
      <Tabs.Screen name="settings" options={{ title: 'Settings', tabBarButtonTestID: 'tab-settings',
        tabBarIcon: ({ color, size }) => <Ionicons name="settings-sharp" size={size} color={String(color)} /> }} />
    </Tabs>
  );
}
