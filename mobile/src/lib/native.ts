/**
 * The one place the rest of the app gets its storage code from.
 *
 * An installed build has the native module (modules/tuckvault-core, Kotlin and Swift),
 * which does the heavy work off the screen's thread. Inside Expo Go only the modules Expo
 * ships exist, so there the same work is done by jscore.ts.
 */

import Constants, { ExecutionEnvironment } from 'expo-constants';
import { requireOptionalNativeModule } from 'expo';

import type { TuckvaultCoreNative } from '../../modules/tuckvault-core/src/TuckvaultCoreModule';
import JsCore from './jscore';

const METHODS = ['newMasterKey', 'randomHex', 'keyCheck', 'hmacHex', 'sha256Hex', 'encodeRecoveryKey', 'decodeRecoveryKey',
                 'statFile', 'listTree', 'sealChunk', 'openChunk', 'sealSnapshot', 'openSnapshot', 'sha256File'] as const;
export type Storage = Pick<TuckvaultCoreNative, (typeof METHODS)[number]> & {
  onWifi(): boolean | Promise<boolean>;
  plainJs?: boolean;
};

const loaded = requireOptionalNativeModule<TuckvaultCoreNative>('TuckvaultCore');

/** True inside Expo Go, where there are no automatic backups and no Files-app folder. */
export const inExpoGo = !loaded || Constants.executionEnvironment === ExecutionEnvironment.StoreClient;

const Native: Storage = loaded ?? JsCore;
export default Native;
