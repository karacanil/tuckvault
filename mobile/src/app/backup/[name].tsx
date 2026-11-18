import { Ionicons } from '@expo/vector-icons';
import { Asset } from 'expo-media-library';
import { Stack, useLocalSearchParams } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Alert, FlatList, Modal, Pressable, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BackupError } from '../../lib/client';
import type { FileEntry, Manifest } from '../../lib/engine';
import { baseName, humanSize, localDateTime, plural } from '../../lib/format';
import { backupContents, fetchFile, isMedia } from '../../lib/runner';
import { Bar, Button, Empty, Loading, Notice, P, ProgressBar } from '../../ui/kit';
import { radius, space, useTheme } from '../../ui/theme';

type Job = { title: string; done: number; total: number; text: string } | null;

function explain(e: unknown): string {
  return e instanceof BackupError ? e.message : 'Something went wrong: ' + String(e).slice(0, 160);
}

/** The files in one backup, with ways to get them back onto the phone. */
export default function BackupDetail() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const { name } = useLocalSearchParams<{ name: string }>();
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState<FileEntry | null>(null);
  const [job, setJob] = useState<Job>(null);
  const stop = useRef(false);

  useEffect(() => {
    backupContents(String(name)).then(setManifest).catch((e) => setError(explain(e)));
  }, [name]);

  const files = useMemo(() => {
    if (!manifest) return [];
    const q = query.trim().toLowerCase();
    return q ? manifest.files.filter((f) => f.p.toLowerCase().includes(q)) : manifest.files;
  }, [manifest, query]);
  const media = useMemo(() => (manifest ? manifest.files.filter((f) => isMedia(f.p)) : []), [manifest]);

  async function share(entry: FileEntry) {
    setChosen(null);
    setJob({ title: 'Getting the file…', done: 0, total: entry.s, text: baseName(entry.p) });
    try {
      const uri = await fetchFile(entry, (done, total) => setJob({ title: 'Getting the file…', done, total, text: baseName(entry.p) }));
      setJob(null);
      await Sharing.shareAsync(uri, { dialogTitle: baseName(entry.p) });
    } catch (e) {
      setJob(null);
      Alert.alert('Could not get the file', explain(e));
    }
  }

  async function toGallery(entries: FileEntry[]) {
    setChosen(null);
    stop.current = false;
    const total = entries.reduce((sum, f) => sum + f.s, 0);
    let done = 0;
    let saved = 0;
    const problems: string[] = [];
    for (const entry of entries) {
      if (stop.current) break;
      const before = done;
      setJob({ title: entries.length > 1 ? 'Restoring ' + (saved + 1) + ' of ' + entries.length : 'Restoring…', done, total, text: baseName(entry.p) });
      try {
        const uri = await fetchFile(entry, (n) => setJob((j) => (j ? { ...j, done: before + n } : j)));
        await Asset.create(uri);
        saved += 1;
      } catch (e) {
        problems.push(baseName(entry.p) + ': ' + explain(e));
        if (e instanceof BackupError && ['unreachable', 'not_paired', 'clock'].includes(e.code)) break; // the rest would fail the same way
      }
      done = before + entry.s;
    }
    setJob(null);
    Alert.alert(problems.length ? 'Restored with problems' : 'Restored',
      plural(saved, 'item') + ' saved to your photo library.' +
      (problems.length ? '\n\n' + plural(problems.length, 'item') + ' could not be restored:\n' + problems.slice(0, 3).join('\n') : ''));
  }

  if (error) {
    return <View style={{ flex: 1, backgroundColor: t.bg, padding: space.lg }}><Notice kind="bad">{error}</Notice></View>;
  }
  if (!manifest) return <View style={{ flex: 1, backgroundColor: t.bg }}><Loading text="Opening the backup…" /></View>;

  return (
    <View style={{ flex: 1, backgroundColor: t.bg }}>
      <Bar />
      <Stack.Screen options={{ title: localDateTime(manifest.created) }} />
      <View style={{ paddingHorizontal: space.lg, paddingBottom: space.md }}>
        <P muted>{plural(manifest.stats.files, 'file')}  ·  {humanSize(manifest.stats.bytes)}  ·  from {manifest.host}</P>
        <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: t.card, borderRadius: radius.md, borderWidth: 1,
                       borderColor: t.border, paddingHorizontal: space.md, marginTop: space.md }}>
          <Ionicons name="search" size={18} color={t.faint} />
          <TextInput testID="file-search" accessibilityLabel="Search files" value={query} onChangeText={setQuery} placeholder="Search by name"
                     placeholderTextColor={t.faint} autoCapitalize="none" autoCorrect={false}
                     style={{ flex: 1, color: t.text, fontSize: 16, paddingVertical: 11, marginLeft: space.sm }} />
        </View>
        {media.length > 0 ? (
          <Button testID="restore-all-media" title={'Restore all photos and videos (' + media.length.toLocaleString('en') + ')'}
                  icon="download-outline" kind="secondary" style={{ marginTop: space.md }}
                  onPress={() => Alert.alert('Restore to this phone?',
                    plural(media.length, 'item') + ' (' + humanSize(media.reduce((s, f) => s + f.s, 0)) + ') will be saved to your photo library. ' +
                    'Items that are already on this phone will appear twice.',
                    [{ text: 'Cancel', style: 'cancel' }, { text: 'Restore', onPress: () => toGallery(media) }])} />
        ) : null}
      </View>
      <FlatList
        data={files}
        keyExtractor={(f) => f.p}
        initialNumToRender={20}
        contentContainerStyle={{ paddingHorizontal: space.lg, paddingBottom: insets.bottom + space.xl }}
        ListEmptyComponent={<Empty icon="search-outline" title="Nothing matches" text="Try a shorter name." />}
        renderItem={({ item, index }) => (
          <Pressable testID={'file-' + index} accessibilityRole="button" accessibilityLabel={baseName(item.p)} onPress={() => setChosen(item)}
                     style={({ pressed }) => ({ flexDirection: 'row', alignItems: 'center', paddingVertical: space.md,
                                                borderBottomWidth: 1, borderBottomColor: t.border, opacity: pressed ? 0.6 : 1 })}>
            <Ionicons name={isMedia(item.p) ? (/\.(mp4|mov|m4v|3gp|mkv|webm|avi)$/i.test(item.p) ? 'videocam-outline' : 'image-outline') : 'document-outline'}
                      size={22} color={t.primary} style={{ marginRight: space.md }} />
            <View style={{ flex: 1 }}>
              <Text numberOfLines={1} style={{ color: t.text, fontSize: 15, fontWeight: '500' }}>{baseName(item.p)}</Text>
              <Text numberOfLines={1} style={{ color: t.muted, fontSize: 12, marginTop: 2 }}>
                {item.p.slice(0, Math.max(0, item.p.lastIndexOf('/')))}  ·  {humanSize(item.s)}
              </Text>
            </View>
            <Ionicons name="ellipsis-horizontal" size={18} color={t.faint} />
          </Pressable>
        )}
      />

      <Modal visible={chosen !== null} transparent animationType="slide" onRequestClose={() => setChosen(null)}>
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)' }} onPress={() => setChosen(null)} accessibilityLabel="Close" />
        {chosen ? (
          <View style={{ backgroundColor: t.card, padding: space.lg, paddingBottom: insets.bottom + space.lg,
                         borderTopLeftRadius: radius.lg, borderTopRightRadius: radius.lg }}>
            <Text style={{ color: t.text, fontSize: 17, fontWeight: '700' }} numberOfLines={2}>{baseName(chosen.p)}</Text>
            <Text style={{ color: t.muted, fontSize: 13, marginTop: 4, marginBottom: space.lg }}>
              {humanSize(chosen.s)}  ·  changed {localDateTime(new Date(chosen.m / 1_000_000).toISOString())}
            </Text>
            {isMedia(chosen.p) ? (
              <Button testID="save-to-photos" title="Save to my photos" icon="download-outline" onPress={() => toGallery([chosen])} style={{ marginBottom: space.md }} />
            ) : null}
            <Button testID="share-file" title="Open or share…" icon="share-outline" kind="secondary" onPress={() => share(chosen)} />
          </View>
        ) : null}
      </Modal>

      <Modal visible={job !== null} transparent animationType="fade">
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.45)', justifyContent: 'center', padding: space.xl }}>
          {job ? (
            <View style={{ backgroundColor: t.card, borderRadius: radius.lg, padding: space.xl }}>
              <Text style={{ color: t.text, fontSize: 17, fontWeight: '700', marginBottom: 4 }}>{job.title}</Text>
              <Text numberOfLines={1} style={{ color: t.muted, fontSize: 14, marginBottom: space.lg }}>{job.text}</Text>
              <ProgressBar value={job.total ? job.done / job.total : null} />
              <Text style={{ color: t.muted, fontSize: 13, marginTop: space.sm }}>{humanSize(job.done)} of {humanSize(job.total)}</Text>
              <Button title="Stop" kind="ghost" onPress={() => { stop.current = true; }} style={{ marginTop: space.md }} />
            </View>
          ) : null}
        </View>
      </Modal>
    </View>
  );
}
