import { cncPlugin } from '../processes/cnc';
import { injectionPlugin } from '../processes/injection';
import { sheetMetalPlugin } from '../processes/sheetmetal';
import type { ProcessPlugin } from './types';

export const PROCESS_PLUGINS: Record<string, ProcessPlugin> = {
  [cncPlugin.processId]: cncPlugin,
  [injectionPlugin.processId]: injectionPlugin,
  [sheetMetalPlugin.processId]: sheetMetalPlugin,
};

export function getPlugin(processId: string): ProcessPlugin | null {
  return PROCESS_PLUGINS[processId] ?? null;
}
