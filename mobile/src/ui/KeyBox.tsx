import { Platform, Text, View } from 'react-native';

import { radius, space, useTheme } from './theme';

/** A recovery key, set in groups so it can be copied down by hand. */
export function KeyBox({ value }: { value: string }) {
  const t = useTheme();
  const groups = value.split('-');
  return (
    <View testID="recovery-key" accessible accessibilityLabel={'Recovery key: ' + groups.join(' ')}
          style={{ backgroundColor: t.card, borderColor: t.border, borderWidth: 1, borderRadius: radius.md,
                   padding: space.md, flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center' }}>
      {groups.map((group, i) => (
        <Text key={i} selectable style={{ color: t.text, fontSize: 17, letterSpacing: 1.5, fontWeight: '600',
                                         fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
                                         width: '25%', textAlign: 'center', paddingVertical: 5 }}>
          {group}
        </Text>
      ))}
    </View>
  );
}
