import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { AppState, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { backgroundAllowed } from '../../lib/background';
import { BackupError } from '../../lib/client';
import type { Result } from '../../lib/engine';
import { ago, baseName, describeRun, humanSize, plural } from '../../lib/format';
import { autoBlocker, cancel, session, startBackup, useActivity } from '../../lib/runner';
import { useRunState, useSettings } from '../../lib/store';
import { Bar, Card, Notice, P, ProgressBar, Row, SectionTitle } from '../../ui/kit';
import type { IconName } from '../../ui/kit';
import { BRAND_BOTTOM, BRAND_TOP, radius, space, useTheme } from '../../ui/theme';

type Reach = { state: 'checking' | 'ok' | 'down'; text: string };

function useReachability(refreshKey: unknown): Reach {
  const settings = useSettings();
  const [reach, setReach] = useState<Reach>({ state: 'checking', text: 'Checking…' });
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const who = await session().client.whoami();
        if (alive) setReach({ state: 'ok', text: 'Connected to ' + (who.name || settings.server?.name || 'your computer') });
      } catch (e) {
        if (!alive) return;
        const code = e instanceof BackupError ? e.code : '';
        setReach({ state: 'down', text: code === 'not_paired' ? 'This phone was removed on the computer. Connect again in Settings.'
          : code === 'clock' ? 'The clock on this phone or the computer is wrong.'
          : 'Cannot reach ' + (settings.server?.name || 'your computer') + ' right now' });
      }
    })();
    return () => {
      alive = false;
    };
  }, [refreshKey, settings.server?.url, settings.server?.name]);
  return reach;
}

function headline(last: Result | null, lastSuccess: string | null): { icon: IconName; title: string; tone: 'good' | 'warn' | 'bad' | 'new' } {
  if (!lastSuccess) {
    if (last && last.status === 'failed') return { icon: 'alert-circle', title: 'The first backup did not work', tone: 'bad' };
    if (last && last.status === 'partial') return { icon: 'hourglass', title: 'The first backup is not finished', tone: 'warn' };
    return { icon: 'shield-outline', title: 'Not backed up yet', tone: 'new' };
  }
  const days = (Date.now() - Date.parse(lastSuccess)) / 86400_000;
  if (last && last.kind === 'backup' && last.status === 'failed') return { icon: 'alert-circle', title: 'The last backup did not work', tone: 'bad' };
  if (last && last.kind === 'verify' && last.status === 'failed') return { icon: 'alert-circle', title: 'A check found a problem', tone: 'bad' };
  if (days > 7) return { icon: 'alert-circle', title: 'No backup for ' + Math.floor(days) + ' days', tone: 'warn' };
  if (last && last.status === 'warning') return { icon: 'shield-checkmark', title: 'Backed up, with a few files skipped', tone: 'warn' };
  return { icon: 'shield-checkmark', title: 'Your phone is backed up', tone: 'good' };
}

export default function Home() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const settings = useSettings();
  const run = useRunState();
  const activity = useActivity();
  const [tick, setTick] = useState(0);
  const [blocker, setBlocker] = useState<string | null>(null);
  const [background, setBackground] = useState(true);
  const reach = useReachability(tick + (activity.kind ? 0 : run.history.length));

  const refresh = useCallback(() => {
    setTick((n) => n + 1);
    autoBlocker().then(setBlocker).catch(() => {});
    backgroundAllowed().then(setBackground).catch(() => {});
  }, []);
  useFocusEffect(refresh);
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => { if (s === 'active') refresh(); });
    return () => sub.remove();
  }, [refresh]);

  const busy = activity.kind !== null;
  const p = activity.progress;
  const head = headline(run.lastRun, run.lastSuccess);
  const last = run.lastRun;

  let stage = 'Looking for photos and files…';
  let detail = '';
  let fraction: number | null = null;
  if (activity.kind === 'verify') stage = 'Checking your backups…';
  if (p) {
    if (p.phase === 'connect') stage = 'Connecting to your computer…';
    else if (p.phase === 'backup') {
      stage = 'Backing up';
      if (p.files) {
        fraction = Math.min(1, (p.file || 0) / p.files);
        detail = ((p.file || 0) + 1).toLocaleString('en') + ' of ' + p.files.toLocaleString('en') + '  ·  ' + baseName(p.text);
      }
    } else if (p.phase === 'verify') stage = 'Checking the new backup…';
    else if (p.phase === 'scrub') {
      stage = 'Checking everything stored…';
      fraction = p.total ? p.done / p.total : null;
    } else if (p.phase === 'prune') stage = 'Tidying up old backups…';
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <Bar light />
      <ScrollView contentContainerStyle={{ paddingBottom: space.xxl }}>
        <LinearGradient colors={[BRAND_TOP, BRAND_BOTTOM]} start={{ x: 0, y: 0 }} end={{ x: 0.6, y: 1 }}
                        style={{ paddingTop: insets.top + space.xl, paddingHorizontal: space.xl, paddingBottom: space.xl,
                                 borderBottomLeftRadius: 28, borderBottomRightRadius: 28 }}>
          <Text style={{ color: '#E6F4FF', fontSize: 14, fontWeight: '700', letterSpacing: 1 }}>TUCKVAULT</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: space.lg }}>
            <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: 'rgba(255,255,255,0.18)',
                           alignItems: 'center', justifyContent: 'center', marginRight: space.lg }}>
              <Ionicons name={busy ? 'cloud-upload' : head.icon} size={34} color="#FFFFFF" />
            </View>
            <View style={{ flex: 1 }}>
              <Text testID="headline" accessibilityRole="header" style={{ color: '#FFFFFF', fontSize: 23, fontWeight: '800', letterSpacing: -0.3 }}>
                {busy ? stage : head.title}
              </Text>
              <Text testID="subline" style={{ color: '#E6F4FF', fontSize: 14, marginTop: 4 }} numberOfLines={2}>
                {busy ? (detail || 'Keep Tuckvault open until it is done.')
                  : run.lastSuccess ? 'Last backup ' + ago(run.lastSuccess) + '  ·  ' + plural(run.protectedFiles, 'file') + '  ·  ' + humanSize(run.protectedBytes)
                  : 'Tap the button to make your first backup.'}
              </Text>
            </View>
          </View>
          {busy ? (
            <View style={{ marginTop: space.xl }}>
              <ProgressBar value={fraction} color="#FFFFFF" track="rgba(255,255,255,0.25)" />
              {activity.kind === 'backup' ? (
                <Pressable testID="stop-backup" accessibilityRole="button" accessibilityLabel="Stop" onPress={cancel} disabled={activity.cancelling}
                           style={({ pressed }) => ({ marginTop: space.lg, minHeight: 50, borderRadius: radius.md, borderWidth: 1.5,
                                                      borderColor: 'rgba(255,255,255,0.7)', alignItems: 'center', justifyContent: 'center',
                                                      opacity: activity.cancelling ? 0.5 : pressed ? 0.8 : 1 })}>
                  <Text style={{ color: '#FFFFFF', fontSize: 16, fontWeight: '700' }}>{activity.cancelling ? 'Stopping…' : 'Stop'}</Text>
                </Pressable>
              ) : null}
            </View>
          ) : (
            <Pressable testID="backup-now" accessibilityRole="button" accessibilityLabel="Back up now" onPress={() => { startBackup(false); }}
                       style={({ pressed }) => ({ marginTop: space.xl, minHeight: 54, borderRadius: radius.md, backgroundColor: '#FFFFFF',
                                                  flexDirection: 'row', alignItems: 'center', justifyContent: 'center', opacity: pressed ? 0.85 : 1 })}>
              <Ionicons name="cloud-upload-outline" size={20} color={BRAND_BOTTOM} style={{ marginRight: space.sm }} />
              <Text style={{ color: BRAND_BOTTOM, fontSize: 17, fontWeight: '700' }}>Back up now</Text>
            </Pressable>
          )}
        </LinearGradient>

        <View style={{ paddingHorizontal: space.lg, marginTop: space.lg }}>
          {!busy && last && last.error && last.status !== 'cancelled' ? (
            <View style={{ marginBottom: space.md }}>
              <Notice testID="last-error" kind={last.status === 'partial' ? 'warn' : 'bad'}
                      title={last.kind === 'verify' ? 'Check' : last.status === 'partial' ? 'Not finished yet' : 'What went wrong'}>
                {last.error}
              </Notice>
            </View>
          ) : null}
          {!busy && last && last.kind === 'backup' && last.warnings.length ? (
            <View style={{ marginBottom: space.md }}>
              <Notice testID="last-warnings" kind="warn" title={plural(last.warnings.length, 'thing') + ' to know about the last backup'}
                      action="Show" onAction={() => router.push('/history')}>
                {last.warnings[0]}
              </Notice>
            </View>
          ) : null}
          {!background && settings.auto ? (
            <View style={{ marginBottom: space.md }}>
              <Notice kind="warn" title="Background activity is switched off for Tuckvault">
                Automatic backups cannot run until you allow background activity for Tuckvault in the phone's settings.
              </Notice>
            </View>
          ) : null}

          <Card padded={false}>
            <Row testID="row-computer" first icon="desktop-outline" title="Your computer" detail={reach.text}
                 right={<View style={{ width: 12, height: 12, borderRadius: 6,
                                       backgroundColor: reach.state === 'ok' ? t.good : reach.state === 'down' ? t.bad : t.faint }} />} />
            <Row icon="sync-outline" title="Automatic backup"
                 detail={!settings.auto ? 'Off. Tuckvault only backs up when you tap the button.'
                   : blocker === 'waiting for Wi-Fi' ? 'On. Waiting for Wi-Fi.'
                   : blocker === 'waiting for the charger' ? 'On. Waiting for the charger.'
                   : 'On. Runs a few times a day in the background.'}
                 onPress={() => router.push('/(tabs)/settings')} />
            <Row icon="list-outline" title="Activity" detail={run.history.length ? 'Last: ' + describeRun(run.history[0]) : 'Nothing yet'}
                 onPress={() => router.push('/history')} />
          </Card>

          <SectionTitle>What is backed up</SectionTitle>
          <Card padded={false}>
            <Row first icon="images-outline" title="Photos and videos"
                 detail={settings.photos ? (settings.videos ? 'Everything in your library' : 'Photos only, no videos') : 'Off'}
                 onPress={() => router.push('/(tabs)/settings')} />
            <Row icon="folder-outline" title="Files"
                 detail={Platform.OS === 'ios' ? (settings.inbox ? 'The Tuckvault folder in the Files app' : 'Off')
                   : settings.folders.length ? plural(settings.folders.length, 'folder') : 'No folders yet. Add some in Settings.'}
                 onPress={() => router.push('/(tabs)/settings')} />
          </Card>
          <P muted small style={{ textAlign: 'center', marginTop: space.lg }}>
            Encrypted on this phone with a key only you have.
          </P>
        </View>
      </ScrollView>
    </View>
  );
}

