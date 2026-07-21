import {
  ArrowUpFromLine, Axis3d, Compass, Crosshair, Drill, Droplets, Expand, Eye,
  Frame, Grid3x3, Highlighter, Layers, ListOrdered, MoveVertical, Network,
  Palette, Pin, Play, Radius, Ratio, Ruler, Scissors, Shapes, ShieldCheck,
  Snowflake, Spline, TrendingUp, Waves, type LucideIcon,
} from 'lucide-react';
import { PROCESS_PLUGINS } from '../registry';

/**
 * The inspection-lens registry: every viewer mode of every process plugin,
 * derived from `ProcessPlugin.modes` (the single source of truth) plus a
 * small curation overlay (icon, category, blurb, pinned/advanced flags).
 * A lens is anything paintable over the model — it is never a verdict; the
 * runnable checks in `analyses.ts` reference lenses but are a separate
 * concept (see docs/PLAN-ARCHITECTURE.md).
 */

export type LensCategoryId =
  | 'model' | 'geometry' | 'cnc' | 'molding' | 'sheet' | 'tube';

export const LENS_CATEGORIES: { id: LensCategoryId; label: string }[] = [
  { id: 'model', label: 'Model data' },
  { id: 'geometry', label: 'Geometry' },
  { id: 'cnc', label: 'CNC' },
  { id: 'molding', label: 'Molding' },
  { id: 'sheet', label: 'Sheet metal' },
  { id: 'tube', label: 'Tube' },
];

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
}

interface Curation {
  icon?: LucideIcon;
  category?: LensCategoryId;
  label?: string;
  blurb?: string;
  pinned?: boolean;
  advanced?: boolean;
  hidden?: boolean;
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

const CATEGORY_ICON: Record<LensCategoryId, LucideIcon> = {
  model: Shapes, geometry: Ruler, cnc: Axis3d,
  molding: Droplets, sheet: Layers, tube: Scissors,
};

const CURATION: Record<string, Curation> = {
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
  'cnc:setups': { icon: Axis3d },
  'cnc:features': { icon: Drill },
  'cnc:unified': { icon: ShieldCheck },
  'cnc:access': { icon: Eye },
  'cnc:class': { icon: Layers },
  'cnc:gap': { icon: Spline },
  'cnc:stickout': { icon: MoveVertical },
  'cnc:thinSpan': { icon: Waves },

  // molding
  'injection_molding:assignment': { icon: Layers },
  'injection_molding:sprue': { icon: Pin },
  'injection_molding:flowFill': { icon: Droplets },
  'injection_molding:cooling': { icon: Snowflake },
  'injection_molding:ejector': { icon: ArrowUpFromLine },
  'injection_molding:slenderness': { icon: TrendingUp },
  'injection_molding:skeleton': { icon: Network },
  'injection_molding:voxelField': { icon: Grid3x3, advanced: true },

  // sheet metal
  'sheet_metal:flat_pattern': { icon: Expand },
  'sheet_metal:bend_plan': { icon: ListOrdered },
  'sheet_metal:bend_sequence': { icon: Play },
  'sheet_metal:sheet_roles': { icon: Layers },
  'sheet_metal:bend_radius': { icon: Radius },

  // tube laser
  'tube_laser:tube_roles': { icon: Layers },
  'tube_laser:cut_pattern': { icon: Scissors },
};

/** Stable plugin order for building the list (categories re-group anyway). */
const PLUGIN_ORDER = [
  'injection_molding', 'directions', 'cnc', 'sheet_metal', 'tube_laser',
];

function buildLenses(): Lens[] {
  const lenses: Lens[] = [];
  for (const processId of PLUGIN_ORDER) {
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
        icon: c.icon ?? CATEGORY_ICON[category],
        category,
        pinned: c.pinned ?? false,
        advanced: c.advanced ?? false,
        hasControls: !!plugin.Controls,
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
