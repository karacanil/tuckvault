import { getMasterKey, getPairingSecret, getSettings } from './store';

/** Where setup continues: a step is skipped once it has been done. */
export function nextSetupStep(): '/connect' | '/key' | '/choose' {
  if (!getSettings().server || !getPairingSecret()) return '/connect';
  if (!getMasterKey()) return '/key';
  return '/choose';
}
