import { router, useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BackupError } from '../../lib/client';
import type { BackupInfo } from '../../lib/engine';
import { humanSize, localDateTime, plural } from '../../lib/format';
import { backups, useActivity } from '../../lib/runner';
import { Card, Empty, H1, Loading, Notice, P, Row } from '../../ui/kit';
import { space, useTheme } from '../../ui/theme';

export default function Backups() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const activity = useActivity();
  const [list, setList] = useState<BackupInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setList(await backups());
      setError(null);
    } catch (e) {
      if (e instanceof BackupError && e.code === 'no_backups') {
        setList([]);
        setError(null);
      } else {
        setError(e instanceof BackupError ? e.message : 'Something went wrong: ' + String(e).slice(0, 160));
      }
    } finally {
      setLoading(false);
    }
  }, []);
  // Reload when the tab is opened and when a backup has just finished.
  useFocusEffect(useCallback(() => {
    if (!activity.kind) load();
  }, [load, activity.kind]));

  return (
    <View style={{ flex: 1, backgroundColor: t.bg, paddingTop: insets.top + space.lg }}>
      <View style={{ paddingHorizontal: space.lg, marginBottom: space.md }}>
        <H1>Backups</H1>
        <P muted style={{ marginTop: 4 }}>Every backup on your computer. Open one to get files back.</P>
      </View>
      {error ? <View style={{ paddingHorizontal: space.lg, marginBottom: space.md }}>
        <Notice testID="backups-error" kind="bad" action="Try again" onAction={load}>{error}</Notice>
      </View> : null}
      {list === null && !error ? <Loading text="Asking your computer…" /> : (
        <FlatList
          data={list || []}
          keyExtractor={(b) => b.name}
          refreshControl={<RefreshControl refreshing={loading} onRefresh={load} tintColor={t.primary} />}
          contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: space.xxl }}
          ListEmptyComponent={error ? undefined : <Empty icon="time-outline" title="No backups yet"
            text="When the first backup has finished it shows up here." />}
          renderItem={({ item, index }) => (
            <Card padded={false} style={{ marginBottom: space.md }}>
              {item.error ? (
                <Row first icon="alert-circle-outline" danger title="Unreadable backup" detail={item.error} />
              ) : (
                <Row testID={'backup-' + index} first icon="albums-outline" title={localDateTime(item.created || '')}
                     detail={plural(item.files || 0, 'file') + '  ·  ' + humanSize(item.bytes) +
                       (index === 0 ? '  ·  newest' : '') + (item.host ? '\nFrom ' + item.host : '')}
                     onPress={() => router.push({ pathname: '/backup/[name]', params: { name: item.name } })} />
              )}
            </Card>
          )}
        />
      )}
    </View>
  );
}
