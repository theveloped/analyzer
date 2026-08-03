import type { FC } from 'react';
import { PmiRail } from './PmiRail';
import { AssignmentRail } from './rails/AssignmentRail';
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
