import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { Image, Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { nextSetupStep } from '../lib/setup';
import type { IconName } from '../ui/kit';
import { BRAND_BOTTOM, BRAND_TOP, radius, space } from '../ui/theme';

const POINTS: [IconName, string, string][] = [
  ['lock-closed', 'Locked before it leaves your phone', 'Photos and files are encrypted here. Nobody else can read them, not even the computer that stores them.'],
  ['desktop-outline', 'Stored on your own computer', 'No cloud account and no monthly fee. Your backups go to a computer you own.'],
  ['globe-outline', 'Works from anywhere with Tailscale', 'At home or away, your phone reaches your computer over your private Tailscale network.'],
];

export default function Welcome() {
  const insets = useSafeAreaInsets();
  return (
    <LinearGradient colors={[BRAND_TOP, BRAND_BOTTOM]} style={{ flex: 1, paddingTop: insets.top + space.xxl,
                    paddingBottom: insets.bottom + space.xl, paddingHorizontal: space.xl }}>
      <StatusBar style="light" />
      <View style={{ alignItems: 'center' }}>
        <Image source={require('../../assets/splash-icon.png')} style={{ width: 132, height: 132 }}
               accessibilityIgnoresInvertColors accessibilityLabel="Tuckvault logo" />
        <Text accessibilityRole="header" style={{ color: '#FFFFFF', fontSize: 38, fontWeight: '800', letterSpacing: -0.5 }}>Tuckvault</Text>
        <Text style={{ color: '#E6F4FF', fontSize: 17, textAlign: 'center', marginTop: space.sm }}>
          Private backups of your phone, on your own computer.
        </Text>
      </View>
      <View style={{ flex: 1, justifyContent: 'center' }}>
        {POINTS.map(([icon, title, text]) => (
          <View key={title} style={{ flexDirection: 'row', marginVertical: space.md }}>
            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: 'rgba(255,255,255,0.18)',
                           alignItems: 'center', justifyContent: 'center', marginRight: space.md }}>
              <Ionicons name={icon} size={22} color="#FFFFFF" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '700' }}>{title}</Text>
              <Text style={{ color: '#E6F4FF', fontSize: 14, lineHeight: 20, marginTop: 2 }}>{text}</Text>
            </View>
          </View>
        ))}
      </View>
      <Pressable testID="get-started" accessibilityRole="button" accessibilityLabel="Get started"
                 onPress={() => router.push(nextSetupStep())}
                 style={({ pressed }) => ({ backgroundColor: '#FFFFFF', borderRadius: radius.md, minHeight: 54,
                                            alignItems: 'center', justifyContent: 'center', opacity: pressed ? 0.85 : 1 })}>
        <Text style={{ color: BRAND_BOTTOM, fontSize: 17, fontWeight: '700' }}>Get started</Text>
      </Pressable>
    </LinearGradient>
  );
}
