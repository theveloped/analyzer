// Sheet metal plugin placeholder: proves the registry seam. New analyses
// register view modes here once the backend process grows real checks.

import { brepFacesMode, highlightsMode } from '../../colorizers/core';
import type { ProcessPlugin } from '../../registry/types';

export const sheetMetalPlugin: ProcessPlugin = {
  processId: 'sheet_metal',
  label: 'Sheet metal',
  modes: [brepFacesMode, highlightsMode],
  defaults: () => ({}),
};
