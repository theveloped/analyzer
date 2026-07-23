/**
 * The authoring vocabulary for the PMI editor: which GD&T constructs a user may
 * add, and which are lossy on the AP242 round-trip. The editor authors *tolerance
 * features* (entities), never raw faces — a face set is only an entity's geometry.
 *
 * This mirrors the backend single source of truth (`pmi_edit.TOLERANCE_TYPES` +
 * `pmi_support`'s WRITER_UNSUPPORTED_* / reader-drop sets). Keep the two in sync:
 * the server validates and re-derives the authoritative warnings on save, but the
 * editor greys out / inline-warns the lossy picks here so a user never authors a
 * construct the exporter would silently drop. Glyphs come from ControlFrame.
 */

export type CharGroup = 'form' | 'profile' | 'orientation' | 'location' | 'runout';

export interface Characteristic {
  /** OCP GeomToleranceType enum name written into pmi.json */
  type: string;
  glyph: string;
  label: string;
  group: CharGroup;
  /** whether a datum reference frame is required to be meaningful */
  needsDatum: boolean;
  /** non-null → authorable but lossy on export; the string is shown inline */
  lossy?: string;
}

/** The 15 characteristics, grouped. Coaxiality is authorable but writer-lossy
 * (OpenCASCADE emits no STEP entity for it) — flagged, never hidden. */
export const CHARACTERISTICS: Characteristic[] = [
  { type: 'Straightness', glyph: '⏤', label: 'Straightness', group: 'form', needsDatum: false },
  { type: 'Flatness', glyph: '⏥', label: 'Flatness', group: 'form', needsDatum: false },
  { type: 'CircularityOrRoundness', glyph: '○', label: 'Circularity', group: 'form', needsDatum: false },
  { type: 'Cylindricity', glyph: '⌭', label: 'Cylindricity', group: 'form', needsDatum: false },
  { type: 'ProfileOfLine', glyph: '⌒', label: 'Profile of a line', group: 'profile', needsDatum: false },
  { type: 'ProfileOfSurface', glyph: '⌓', label: 'Profile of a surface', group: 'profile', needsDatum: false },
  { type: 'Angularity', glyph: '∠', label: 'Angularity', group: 'orientation', needsDatum: true },
  { type: 'Perpendicularity', glyph: '⟂', label: 'Perpendicularity', group: 'orientation', needsDatum: true },
  { type: 'Parallelism', glyph: '∥', label: 'Parallelism', group: 'orientation', needsDatum: true },
  { type: 'Position', glyph: '⌖', label: 'Position', group: 'location', needsDatum: true },
  { type: 'Concentricity', glyph: '◎', label: 'Concentricity', group: 'location', needsDatum: true },
  { type: 'Coaxiality', glyph: '◎', label: 'Coaxiality', group: 'location', needsDatum: true,
    lossy: 'not written by OpenCASCADE — no STEP entity is emitted on export' },
  { type: 'Symmetry', glyph: '⌯', label: 'Symmetry', group: 'location', needsDatum: true },
  { type: 'CircularRunout', glyph: '↗', label: 'Circular runout', group: 'runout', needsDatum: true },
  { type: 'TotalRunout', glyph: '⌰', label: 'Total runout', group: 'runout', needsDatum: true },
];

export const CHARACTERISTIC_BY_TYPE: Record<string, Characteristic> =
  Object.fromEntries(CHARACTERISTICS.map((c) => [c.type, c]));

/** Material-condition modifiers (M = MMC, L = LMC, S = RFS/regardless). */
export const MATERIAL_MODIFIERS = [
  { value: 'M', glyph: 'Ⓜ', label: 'Maximum material (MMC)' },
  { value: 'L', glyph: 'Ⓛ', label: 'Least material (LMC)' },
  { value: 'S', glyph: 'Ⓢ', label: 'Regardless of feature size (RFS)' },
];

/** Zone-shape (type_of_value) — Ø for a cylindrical/positional zone. */
export const ZONE_VALUE_TYPES = [
  { value: '', label: 'Total-wide zone' },
  { value: 'Diameter', label: 'Diameter (Ø) zone' },
];

/** Frame-level modifiers; the lossy ones are offered but flagged. */
export const TOLERANCE_MODIFIERS = [
  { value: 'Free_State', glyph: 'Ⓕ', label: 'Free state' },
  { value: 'Tangent_Plane', glyph: 'Ⓣ', label: 'Tangent plane' },
  { value: 'All_Around', glyph: '◠', label: 'All around', lossy: 'all-around modifier is not carried by AP242 export' },
  { value: 'All_Over', glyph: '⌆', label: 'All over', lossy: 'all-over modifier is not carried by AP242 export' },
];

/** Zone modifier (Projected zone) — authorable but writer-lossy. */
export const ZONE_MODIFIERS = [
  { value: '', label: 'None' },
  { value: 'Projected', glyph: 'Ⓟ', label: 'Projected zone', lossy: 'projected tolerance-zone modifier is not carried by AP242 export' },
];

/** Feature-of-size / location dimension kinds the exporter round-trips. */
export const DIMENSION_KINDS = [
  { type: 'Size_Diameter', label: 'Diameter (Ø)', angular: false, diameter: true },
  { type: 'Location_LinearDistance', label: 'Linear distance', angular: false, diameter: false },
  { type: 'Location_Angular', label: 'Angular', angular: true, diameter: false },
];
export const DIMENSION_KIND_BY_TYPE: Record<string, (typeof DIMENSION_KINDS)[number]> =
  Object.fromEntries(DIMENSION_KINDS.map((k) => [k.type, k]));
