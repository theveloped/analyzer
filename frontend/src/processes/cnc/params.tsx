import type { FC } from 'react';
import type { ParamSpec } from '../../api/types';
import { Select } from '../../catalyst/select';
import { useStore } from '../../state/store';
import { cncSources } from './sources';

/**
 * What each CNC paint actually reads, declared per mode.
 *
 * `CncControls` showed nine knobs — direction, tool tip, tolerance, stickout,
 * holder, face rule, wall tolerance, side-mill and the grey-out toggle — to
 * fourteen of eighteen modes, because `ProcessPlugin.Controls` is one
 * component per process and cannot know which lens is active. The accessibility
 * lens reads exactly one of those nine.
 *
 * These sets were read off the paints, not inferred:
 *   unified   source tip tolerance rule stickout holder wallTol sideMill
 *   access    source
 *   class     source wallTol mask
 *   gap       source tip tolerance rule wallTol scale mask
 *   stickout  source tip tolerance scale mask
 */

const SOURCE: ParamSpec = {
  name: 'source', type: 'int', default: 0, label: 'Direction',
};
const TIP: ParamSpec = {
  name: 'tip', type: 'int', default: 0, label: 'Tool tip',
};
const TOLERANCE: ParamSpec = {
  name: 'tolerance', type: 'number', default: 0.1, label: 'Gap threshold',
  unit: 'mm', min: 0,
};
const STICKOUT: ParamSpec = {
  name: 'stickout', type: 'number', default: null, label: 'Stickout',
  unit: 'mm', min: 0,
};
const HOLDER: ParamSpec = {
  name: 'holder', type: 'string', default: '', label: 'Holder cylinders',
};
const SCALE: ParamSpec = {
  name: 'scale', type: 'number', default: null, label: 'Heatmap maximum',
  unit: 'mm', min: 0,
};
const RULE: ParamSpec = {
  name: 'rule', type: 'select', default: 'all', label: 'Face rule',
  options: ['all', 'any', 'centroid'],
};
const WALL_TOL: ParamSpec = {
  name: 'wallTol', type: 'number', default: 1.0, label: 'Wall tolerance',
  unit: '°', min: 0,
};
const SIDE_MILL: ParamSpec = {
  name: 'sideMill', type: 'bool', default: true, label: 'Walls side-milled',
};
const MASK: ParamSpec = {
  name: 'mask', type: 'bool', default: true,
  label: 'Grey out inaccessible faces',
};

export const UNIFIED_PARAMS = [
  SOURCE, TIP, TOLERANCE, RULE, STICKOUT, HOLDER, WALL_TOL, SIDE_MILL];
export const ACCESS_PARAMS = [SOURCE];
export const CLASS_PARAMS = [SOURCE, WALL_TOL, MASK];
export const GAP_PARAMS = [SOURCE, TIP, TOLERANCE, RULE, WALL_TOL, SCALE, MASK];
export const STICKOUT_PARAMS = [SOURCE, TIP, TOLERANCE, SCALE, MASK];

// --- the two widgets a ParamSpec cannot describe ----------------------------

/**
 * Direction and tool tip are `select`s whose OPTIONS come from the manifest,
 * not from a static `options` array: `cncSources` scans the cached tool fields
 * and groups them, so the list changes whenever a job lands. And the tip list
 * belongs to the selected direction, which is why a widget is handed the whole
 * values bag rather than just its own value.
 *
 * `ParamSpec` is a backend-served DTO mirrored from `processes/base.py`, so it
 * must not grow a callback. This is the case `paramWidgets` exists for.
 */
const SourceWidget: FC<{
  value: unknown; onChange: (v: unknown) => void;
}> = ({ value, onChange }) => {
  const manifest = useStore((s) => s.manifest);
  const sources = manifest ? cncSources(manifest) : [];
  if (!sources.length) {
    return <p className="text-xs/5 text-zinc-500 dark:text-zinc-400">
      No cached direction fields — run cnc/precompute.
    </p>;
  }
  return (
    <Select
      value={String(value ?? 0)}
      aria-label="Direction"
      onChange={(e) => {
        // the tip list belongs to the direction, so a stale index would point
        // into another direction's tools
        onChange(parseInt(e.target.value));
        useStore.getState().setViewerParam('cnc', 'tip', 0);
      }}
    >
      {sources.map((s, i) => {
        const d = manifest?.directions[s.direction]
          ?.map((x) => x.toFixed(2)).join(', ');
        const bare = !s.tips.length && !s.clearances.length;
        return (
          <option key={s.key} value={i}>
            {`dir ${s.direction} [${d}]${bare ? ' — accessibility only' : ''}`}
          </option>
        );
      })}
    </Select>
  );
};

const TipWidget: FC<{
  value: unknown; values: Record<string, unknown>; onChange: (v: unknown) => void;
}> = ({ value, values, onChange }) => {
  const manifest = useStore((s) => s.manifest);
  const sources = manifest ? cncSources(manifest) : [];
  const source = sources[Number(values.source ?? 0)] ?? sources[0];
  const tips = source?.tips ?? [];
  if (!tips.length) {
    return <p className="text-xs/5 text-zinc-500 dark:text-zinc-400">
      No tip fields for this direction.
    </p>;
  }
  return (
    <Select value={String(value ?? 0)} aria-label="Tool tip"
      onChange={(e) => onChange(parseInt(e.target.value))}>
      {tips.map((t, i) => {
        const kind = t.corner_radius === 0 ? 'flat'
          : (t.corner_radius >= t.diameter / 2 ? 'ball' : 'bull');
        return (
          <option key={i} value={i}>
            {`D${t.diameter} rc${t.corner_radius} (${kind})`}
          </option>
        );
      })}
    </Select>
  );
};

export const CNC_PARAM_WIDGETS = {
  source: SourceWidget as any,
  tip: TipWidget as any,
};
