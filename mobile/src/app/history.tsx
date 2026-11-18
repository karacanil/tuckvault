import { Ionicons } from '@expo/vector-icons';
import { Text, View } from 'react-native';

import type { Result } from '../lib/engine';
import { humanSize, localDateTime, plural } from '../lib/format';
import { useRunState } from '../lib/store';
import { Card, Empty, P, Screen } from '../ui/kit';
import { space, useTheme } from '../ui/theme';

const WHAT = { backup: 'Backup', verify: 'Check', restore: 'Restore' };
const HOW = { success: 'Finished', warning: 'Finished, some files skipped', failed: 'Did not work', partial: 'Not finished',
              cancelled: 'Stopped' };

function Entry({ r }: { r: Result }) {
  const t = useTheme();
  const ok = r.status === 'success';
  const color = ok ? t.good : r.status === 'failed' ? t.bad : t.warn;
  return (
    <Card style={{ marginBottom: space.md }}>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        <Ionicons name={ok ? 'checkmark-circle' : r.status === 'failed' ? 'close-circle' : 'alert-circle'} size={22} color={color} />
        <Text style={{ color: t.text, fontSize: 16, fontWeight: '600', marginLeft: space.sm, flex: 1 }}>
          {WHAT[r.kind]}: {HOW[r.status]}
        </Text>
      </View>
      <P muted small style={{ marginTop: 4 }}>{localDateTime(r.finished)}</P>
      {r.kind === 'backup' && (ok || r.status === 'warning') ? (
        <P small style={{ marginTop: space.sm }}>
          {plural(r.files, 'file')} ({humanSize(r.bytes)}) in the backup. {plural(r.newFiles, 'new or changed file')}, {humanSize(r.uploadedBytes)} sent.
        </P>
      ) : null}
      {r.error ? <P small style={{ marginTop: space.sm, color }}>{r.error}</P> : null}
      {r.warnings.slice(0, 8).map((w, i) => <P key={i} small muted style={{ marginTop: 4 }}>• {w}</P>)}
      {r.warnings.length > 8 ? <P small muted style={{ marginTop: 4 }}>… and {r.warnings.length - 8} more</P> : null}
    </Card>
  );
}

export default function History() {
  const run = useRunState();
  return (
    <Screen>
      {run.history.length === 0 ? <Empty icon="list-outline" title="Nothing yet" text="Backups and checks are listed here as they happen." />
        : run.history.map((r, i) => <Entry key={r.started + i} r={r} />)}
    </Screen>
  );
}
