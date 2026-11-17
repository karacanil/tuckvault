import * as Clipboard from 'expo-clipboard';
import { useState } from 'react';
import { View } from 'react-native';

import Native from '../../modules/tuckvault-core';
import { getMasterKey } from '../lib/store';
import { Button, Notice, P, Screen } from '../ui/kit';
import { KeyBox } from '../ui/KeyBox';
import { space } from '../ui/theme';

export default function RecoveryKey() {
  const master = getMasterKey();
  const [shown, setShown] = useState(false);
  const [copied, setCopied] = useState(false);
  if (!master) {
    return <Screen><Notice kind="bad">This phone has no backup key.</Notice></Screen>;
  }
  const key = Native.encodeRecoveryKey(master);
  return (
    <Screen>
      <P muted style={{ marginBottom: space.lg }}>
        With this key, Tuckvault on a new phone (or on your computer) can open the backups of this phone.
        Anyone who has it can read them, so show it only where nobody is watching.
      </P>
      {shown ? <KeyBox value={key} /> : (
        <Button testID="reveal-key" title="Show my recovery key" icon="eye-outline" kind="secondary" onPress={() => setShown(true)} />
      )}
      {shown ? (
        <View style={{ marginTop: space.md }}>
          <Button title={copied ? 'Copied' : 'Copy'} icon="copy-outline" kind="secondary"
                  onPress={async () => { await Clipboard.setStringAsync(key); setCopied(true); }} />
        </View>
      ) : null}
      <View style={{ marginTop: space.xl }}>
        <Notice kind="info" title="Restoring on your computer">
          Run “tuckvault phone restore” followed by a folder name on the computer that stores the backups. It asks for this key.
        </Notice>
      </View>
    </Screen>
  );
}
