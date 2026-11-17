import { Text, View } from 'react-native';

import Core from '../../modules/tuckvault-core';

export default function Home() {
  const key = '00'.repeat(32);
  return (
    <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
      <Text testID="check">check {Core.keyCheck(key).slice(0, 24)}</Text>
    </View>
  );
}
