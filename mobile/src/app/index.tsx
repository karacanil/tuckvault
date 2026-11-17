import { Redirect } from 'expo-router';

import { getMasterKey, useSettings } from '../lib/store';

/** Sends the user to where they left off: setup, or the app itself. */
export default function Start() {
  const settings = useSettings();
  if (settings.onboarded && settings.server && getMasterKey()) return <Redirect href="/(tabs)" />;
  return <Redirect href="/welcome" />;
}
