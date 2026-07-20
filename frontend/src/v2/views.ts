import { Palette, Shapes, type LucideIcon } from 'lucide-react';

/**
 * General "views" — raw visualizations for investigating the imported part,
 * as opposed to the runnable checks in `analyses.ts`. A view is static: it has
 * no threshold and nothing to run; it just paints an existing viewer mode.
 *
 * `id` is the viewer `modeId` (drives the shared store/painter); `process` must
 * be a plugin whose `modes` list includes that id (the v2 shell drives
 * everything through `injection_molding`, which registers both modes below).
 */

export interface View {
  id: string;
  process: string;
  label: string;
  blurb: string;
  icon: LucideIcon;
}

export const VIEWS: View[] = [
  {
    id: 'brep_faces',
    process: 'injection_molding',
    label: 'BREP faces',
    blurb: 'One color per source BREP face from the STEP import.',
    icon: Shapes,
  },
  {
    id: 'face_attrs',
    process: 'injection_molding',
    label: 'STEP colors / names',
    blurb: 'STEP-assigned face colors, names and PMI back-refs.',
    icon: Palette,
  },
];

export const VIEW_BY_ID = Object.fromEntries(
  VIEWS.map((v) => [v.id, v]),
) as Record<string, View>;
