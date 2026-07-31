import {
  ArrowUpFromLine, Axis3d, Box, CircleDot, Compass, Crosshair, Disc3, Drill,
  Droplets, Expand, Eye, Frame, Grid3x3, Highlighter, Layers, ListOrdered,
  MoveVertical, Network, Palette, Pin, Play, Radius, Ratio, Ruler, Scissors,
  Shapes, ShieldCheck, Snowflake, Spline, TrendingUp, Waves, type LucideIcon,
} from 'lucide-react';
import { PROCESS_PLUGINS } from '../registry';

/**
 * The inspection-lens registry: every viewer mode of every process plugin,
 * derived from `ProcessPlugin.modes` (the single source of truth) plus a
 * small curation overlay (icon, category, blurb, pinned/advanced flags).
 * A lens is anything paintable over the model — it is never a verdict; the
 * runnable checks in `analyses.ts` reference lenses but are a separate
 * concept (see docs/ROUTE-ARCHITECTURE.md).
 */

/** The lens categories, in rail order. One table: the id union, the rail's
 * section list and the fallback icon all derive from it, so adding a category
 * is one entry rather than three edits that must agree. */
const CATEGORIES = {
  model: { label: 'Model data', icon: Shapes },
  geometry: { label: 'Geometry', icon: Ruler },
  cnc: { label: 'CNC', icon: Axis3d },
  molding: { label: 'Molding', icon: Droplets },
  sheet: { label: 'Sheet metal', icon: Layers },
  tube: { label: 'Tube', icon: Scissors },
} as const satisfies Record<string, { label: string; icon: LucideIcon }>;

export type LensCategoryId = keyof typeof CATEGORIES;

export const LENS_CATEGORIES: { id: LensCategoryId; label: string }[] =
  (Object.keys(CATEGORIES) as LensCategoryId[])
    .map((id) => ({ id, label: CATEGORIES[id].label }));

export interface Lens {
  /** `${processId}:${modeId}` — unique across plugins. */
  key: string;
  processId: string;
  modeId: string;
  label: string;
  blurb?: string;
  icon: LucideIcon;
  category: LensCategoryId;
  /** Shown as a persistent icon in the viewer toolbar. */
  pinned: boolean;
  /** Only listed when advanced mode is on (debug/expert lenses). */
  advanced: boolean;
  /** The hosting plugin ships a Controls panel (Configure tab). */
  hasControls: boolean;
  /** The backend analysis whose stored result this lens paints, when it has
   * exactly one. Lets the rail show run state and offer a Run button instead
   * of throwing "run it in the Compute panel" at the user. Scalar-field
   * lenses declare theirs in `fieldLenses.ts` and self-materialize instead. */
  analysis?: { process: string; analysis: string };
}

interface Curation {
  icon?: LucideIcon;
  category?: LensCategoryId;
  label?: string;
  blurb?: string;
  pinned?: boolean;
  advanced?: boolean;
  hidden?: boolean;
  analysis?: { process: string; analysis: string };
}

/** Modes registered by several plugins; hosted once, under injection_molding
 * (matching what the v2 shell drove them through before this registry). */
const SHARED_MODES = new Set(['brep_faces', 'face_attrs', 'pmi', 'highlights']);
const SHARED_HOST = 'injection_molding';

const DEFAULT_CATEGORY: Record<string, LensCategoryId> = {
  directions: 'model',
  cnc: 'cnc',
  injection_molding: 'molding',
  sheet_metal: 'sheet',
  tube_laser: 'tube',
};

/** Exported so a test can assert every key resolves to a real lens — a
 * typo'd `process:mode` key is otherwise a silent no-op. */
export const CURATION: Record<string, Curation> = {
  // model data (shared, hosted under injection_molding)
  'injection_molding:brep_faces': {
    icon: Shapes, category: 'model', pinned: true,
    blurb: 'One color per source BREP face from the STEP import.',
  },
  'injection_molding:face_attrs': {
    icon: Palette, category: 'model', pinned: true,
    label: 'STEP colors / names',
    blurb: 'STEP-assigned face colors, names and PMI back-refs.',
  },
  'injection_molding:pmi': {
    icon: Frame, category: 'model', pinned: true,
    label: 'PMI / GD&T',
    blurb: 'Semantic dimensions, tolerances and datums from the STEP.',
  },
  'injection_molding:highlights': {
    icon: Highlighter, category: 'model', advanced: true,
  },
  // the candidate-directions view keeps its dedicated toolbar button
  'directions:directions': { icon: Crosshair, hidden: true },
  // an expression paints only what a check configured, so it is meaningless
  // to open from the ribbon — the check activates it with its own terms
  'injection_molding:expression': { hidden: true },

  // geometry (process-independent measures, hosted by injection_molding)
  'injection_molding:thickness': { icon: Ruler, category: 'geometry', pinned: true },
  'injection_molding:gaps': { icon: Spline, category: 'geometry', pinned: true },
  'injection_molding:rayThickness': { icon: Ratio, category: 'geometry', pinned: true },
  'injection_molding:rayGap': { icon: Radius, category: 'geometry', pinned: true },
  'injection_molding:thinSpan': { icon: Waves, category: 'geometry', pinned: true },
  'injection_molding:thicknessAngle': { icon: Compass, category: 'geometry', advanced: true },
  'injection_molding:gapAngle': { icon: Compass, category: 'geometry', advanced: true },

  // cnc
  'cnc:reach_study': {
    icon: Eye,
    blurb: 'One (direction × tool) machinable mask from the reach study.',
  },
  'cnc:reach_op': {
    icon: Axis3d,
    blurb: 'Faces no tool reaches within one operation\'s tilt cone.',
  },
  'cnc:reach_aggregate': {
    icon: ShieldCheck,
    blurb: 'Faces unreachable in every operation — the route verdict.',
  },
  'cnc:setups': { icon: Axis3d, analysis: { process: 'cnc', analysis: 'setups' } },
  'cnc:features': {
    icon: Drill, analysis: { process: 'cnc', analysis: 'features' },
  },
  'cnc:turning': {
    icon: Disc3,
    analysis: { process: 'cnc', analysis: 'turning' },
    blurb: 'Faces a lathe can produce — OD turning, facing and boring — with '
      + 'the maximal turned state as a section, and the milled remainder.',
  },
  'cnc:coverage': {
    icon: ShieldCheck,
    advanced: true,
    label: 'Combined coverage',
    blurb: 'What a set of directions covers together — the union the '
      + 'directions study totals.',
  },
  'cnc:axis_role': {
    icon: CircleDot,
    advanced: true,
    label: 'Turnability about one axis',
    blurb: 'One candidate axis from the directions study: what a lathe could '
      + 'sweep about it, and what it could not.',
  },
  'cnc:turning_residual': {
    icon: CircleDot,
    advanced: true,
    analysis: { process: 'cnc', analysis: 'turning' },
    blurb: 'How far each face is from being a surface of revolution about the '
      + 'turning axis.',
  },
  'cnc:hull': {
    icon: Box,
    analysis: { process: 'cnc', analysis: 'hull' },
    blurb: 'Faces on the convex hull — machinable from outside with an infinitely large tool.',
  },
  'cnc:unified': { icon: ShieldCheck },
  'cnc:access': { icon: Eye },
  'cnc:class': { icon: Layers },
  'cnc:gap': { icon: Spline },
  'cnc:stickout': { icon: MoveVertical },

  // molding
  'injection_molding:assignment': {
    icon: Layers,
    analysis: { process: 'injection_molding', analysis: 'mold_orientation' },
  },
  'injection_molding:sprue': {
    icon: Pin,
    analysis: { process: 'injection_molding', analysis: 'sprue_proposals' },
  },
  // flowFill needs a user-picked gate point, so it has no meaningful
  // default run — it stays driven by its own controls
  'injection_molding:flowFill': { icon: Droplets },
  'injection_molding:cooling': { icon: Snowflake },
  'injection_molding:ejector': {
    icon: ArrowUpFromLine,
    analysis: { process: 'injection_molding', analysis: 'ejection_sticking' },
  },
  'injection_molding:slenderness': {
    icon: TrendingUp,
    analysis: { process: 'injection_molding', analysis: 'slenderness' },
  },
  'injection_molding:skeleton': {
    icon: Network,
    analysis: { process: 'injection_molding', analysis: 'wall_skeleton' },
  },
  'injection_molding:voxelField': {
    icon: Grid3x3, advanced: true,
    analysis: { process: 'prep', analysis: 'voxels' },
  },

  // sheet metal
  'sheet_metal:flat_pattern': {
    icon: Expand,
    analysis: { process: 'sheet_metal', analysis: 'flat_pattern' },
  },
  'sheet_metal:bend_plan': {
    icon: ListOrdered,
    analysis: { process: 'sheet_metal', analysis: 'bend_plan' },
  },
  'sheet_metal:bend_sequence': {
    icon: Play,
    analysis: { process: 'sheet_metal', analysis: 'bend_plan' },
  },
  'sheet_metal:sheet_roles': {
    icon: Layers, analysis: { process: 'sheet_metal', analysis: 'detect' },
  },
  'sheet_metal:bend_radius': {
    icon: Radius, analysis: { process: 'sheet_metal', analysis: 'detect' },
  },

  // tube laser
  'tube_laser:tube_roles': {
    icon: Layers, analysis: { process: 'tube_laser', analysis: 'profile' },
  },
  'tube_laser:cut_pattern': {
    icon: Scissors, analysis: { process: 'tube_laser', analysis: 'profile' },
  },
};

/** Preferred display order. Not a membership list: every registered plugin is
 * built, listed ones first, so registering a plugin and forgetting it here
 * costs you the ordering — not all of its lenses, silently. */
const PLUGIN_ORDER = [
  'injection_molding', 'directions', 'cnc', 'sheet_metal', 'tube_laser',
];

function orderedPluginIds(): string[] {
  const all = Object.keys(PROCESS_PLUGINS);
  const ranked = PLUGIN_ORDER.filter((id) => id in PROCESS_PLUGINS);
  return [...ranked, ...all.filter((id) => !PLUGIN_ORDER.includes(id))];
}

function buildLenses(): Lens[] {
  const lenses: Lens[] = [];
  for (const processId of orderedPluginIds()) {
    const plugin = PROCESS_PLUGINS[processId];
    if (!plugin) continue;
    for (const mode of plugin.modes) {
      if (SHARED_MODES.has(mode.id) && processId !== SHARED_HOST) continue;
      const key = `${processId}:${mode.id}`;
      const c = CURATION[key] ?? {};
      if (c.hidden) continue;
      const category = c.category ?? DEFAULT_CATEGORY[processId] ?? 'model';
      lenses.push({
        key,
        processId,
        modeId: mode.id,
        label: c.label ?? mode.label,
        blurb: c.blurb,
        icon: c.icon ?? CATEGORIES[category].icon,
        category,
        pinned: c.pinned ?? false,
        advanced: c.advanced ?? false,
        hasControls: !!plugin.Controls,
        analysis: c.analysis,
      });
    }
  }
  return lenses;
}

export const LENSES: Lens[] = buildLenses();

export const PINNED_LENSES: Lens[] = LENSES.filter((l) => l.pinned);

/** Lenses of one category, respecting the advanced reveal. */
export function lensesIn(category: LensCategoryId, advanced: boolean): Lens[] {
  return LENSES.filter(
    (l) => l.category === category && (advanced || !l.advanced),
  );
}

/** The lens for a live store state, if that mode is registered as one. */
export function lensFor(processId: string, modeId: string): Lens | null {
  return LENSES.find((l) => l.processId === processId && l.modeId === modeId)
    ?? null;
}

/** First lens carrying a mode id (for shared modes: the hosting plugin). */
export function lensByMode(modeId: string): Lens | null {
  return LENSES.find((l) => l.modeId === modeId) ?? null;
}
