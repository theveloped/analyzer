import type { FC } from 'react';
import { PmiRail } from './PmiRail';
import { AssignmentRail } from './rails/AssignmentRail';
import {
  BendSequenceRail, FlatPatternDownload,
} from './rails/BendSequenceRail';
import { SetupsRail } from './rails/SetupsRail';
import { TurningRail } from './rails/TurningRail';
import { EjectorRail } from './rails/EjectorRail';
import { FlowFillRail } from './rails/FlowFillRail';
import { SprueRail } from './rails/SprueRail';
import { VoxelFieldRail } from './rails/VoxelFieldRail';

/**
 * Lenses that own the whole rail instead of a settings section.
 *
 * PMI and the candidate directions already worked this way — each dispatched
 * ahead of `LensRail` by its own branch in `Workspace`. This is that pattern
 * as a table, so the next one is an entry rather than a twelfth ternary arm.
 *
 * When does a lens belong here rather than declaring `ViewMode.params`?
 * When it has per-mode LOGIC no declaration can carry:
 *
 *  - it submits a job whose analysis params are NOT its viewer params —
 *    `flowVoxel` becomes `voxel`, `flowSkinCoef` becomes `skin_coef`, and the
 *    mapping is code;
 *  - it consumes viewer clicks (a gate point, an ejector pin, a proposal);
 *  - it is an authoring surface rather than a view (PMI).
 *
 * Everything else declares params and gets the generated form. The test is
 * "could a ParamSpec say this?", not "is it complicated".
 */
export const MODE_RAILS: Record<string, FC> = {
  'cnc:setups': SetupsRail,
  'sheet_metal:bend_sequence': BendSequenceRail,
  'cnc:turning': TurningRail,
  'injection_molding:pmi': PmiRail,
  'injection_molding:assignment': AssignmentRail,
  'injection_molding:sprue': SprueRail,
  'injection_molding:ejector': EjectorRail,
  'injection_molding:flowFill': FlowFillRail,
  'injection_molding:voxelField': VoxelFieldRail,
};

export function modeRailFor(processId: string, modeId: string): FC | null {
  return MODE_RAILS[`${processId}:${modeId}`] ?? null;
}

/**
 * Header actions a lens carries — an export, a link out. Distinct from a mode
 * rail: the lens keeps the normal rail, it just gains one control.
 *
 * The DXF download used to live in the sheet-metal process panel and render
 * for EVERY sheet lens whenever a flat-pattern result existed, so opening
 * `brep_faces` on a sheet part offered to download one.
 */
export const LENS_ACTIONS: Record<string, FC> = {
  'sheet_metal:flat_pattern': FlatPatternDownload,
};

export function lensActionFor(processId: string, modeId: string): FC | null {
  return LENS_ACTIONS[`${processId}:${modeId}`] ?? null;
}
