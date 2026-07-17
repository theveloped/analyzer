// Sheet metal viewer controls: DXF download of the current flat pattern.

import { useStore } from '../../state/store';
import { SHEET_SCHEMA } from './index';

export function SheetMetalControls() {
  const manifest = useStore((s) => s.manifest);
  if (!manifest) return null;

  const results = manifest.results.filter((r) => r.process === 'sheet_metal'
    && r.analysis === 'flat_pattern' && !r.stale
    && r.params.schema === SHEET_SCHEMA);
  if (!results.length) return null;
  const result = results[results.length - 1];

  const url = `/api/parts/${manifest.part.id}/results/sheet_metal/flat_pattern/${result.hash}/export/dxf`;
  return (
    <div className="control-group">
      <a href={url} download style={{ fontSize: 12 }}>
        ⤓ Download flat pattern DXF
      </a>
    </div>
  );
}
