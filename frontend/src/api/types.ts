// TS mirrors of the backend JSON contract (api/schemas + api/manifest).

export interface Part {
  id: string;
  name: string;
  source: string | null;
  /** 'preview' = the first-load bundle landed (renderable and inspectable)
   * but the fine mesh, which every result field indexes into, is not built */
  status: 'raw' | 'preview' | 'meshed';
  counts: { verts: number; faces: number } | null;
  has_directions: boolean;
  created: string | null;
}

/** Mirror of FIELD_ROLES in processes/base.py, which validates it at write time. */
export type FieldRole = 'scalar' | 'mask' | 'category' | 'lines' | 'data'
  | 'fold' | 'nodes' | 'radii' | 'edges' | 'vert_map';

/** Mirror of FIELD_ASSOCIATIONS in processes/base.py. Which index space the
 * array lives in — the one thing standing between the coarse preview and a
 * silently wrong paint. 'brep_face' is indexed by BREP face id, not by mesh
 * face: such a field is valid against the coarse preview as well as the fine
 * mesh, and the viewer joins it through the BREP id map. */
export type FieldAssociation = 'vertex' | 'face' | 'brep_face' | 'none' | 'graph';

/** Mirror of FIELD_DTYPES in processes/base.py. */
export type FieldDtype = 'f4' | 'u1' | 'u4';

export interface FieldDescriptor {
  id: string;
  association: FieldAssociation;
  dtype: FieldDtype;
  role: FieldRole;
  units?: string;
  length: number | null;
  url: string;
  params: Record<string, any>;
}

export interface ResultEntry {
  process: string;
  analysis: string;
  hash: string;
  params: Record<string, any>;
  stats: Record<string, any>;
  fields: string[];
  /** Direction indices no longer match the current direction set. */
  stale?: boolean;
  overrides_url?: string;
}

/** STEP-import artifacts (step_import.py): face colors/names + PMI refs. */
export interface FaceAttrs {
  part_color: [number, number, number] | null;
  face_count: number;
  faces: Record<string, {
    color: [number, number, number] | null;
    name: string | null;
    pmi_refs: number[];
  }>;
}

/** Semantic PMI / GD&T (pmi.json, PMI_SCHEMA = 5). Face ids are 0-based BREP ids
 * (same space as brep_faces). Ids that could not be bridged to the workdir
 * geometry are dropped upstream, so datum_refs may name datums absent here. */
export interface PmiDatumRef {
  name: string | null;
  position: number;      // 1/2/3 precedence; 0 = unset
  modifiers: string[];
}
export interface PmiDimension {
  id: number;
  kind: 'dimension';
  type: string | null;
  value: number;
  upper_tolerance: number | null;
  lower_tolerance: number | null;
  qualifier: string | null;   // Min / Max / Avg
  modifiers: string[];
  angular: boolean;
  face_ids: number[];
  secondary_face_ids?: number[];
  edge_ids: number[];
  /** ISO tolerance class / fit, e.g. H7 (hole) or n6 (shaft) */
  fit_class?: { deviation: string; grade: number; hole: boolean } | null;
  /** thread spec, e.g. { designation: "M6x1", class: "6H" } */
  thread?: { designation: string; class: string | null } | null;
}
export interface PmiTolerance {
  id: number;
  kind: 'tolerance';
  name: string | null;        // semantic name, e.g. "Position.1"
  type: string | null;        // Position, Flatness, ProfileOfSurface, …
  value: number | null;
  type_of_value: string | null;   // Diameter / … (zone value type)
  modifiers: string[];
  material_modifier: string | null;  // M (MMC) / L (LMC)
  zone_modifier: string | null;      // Projected / Runout / NonUniform
  zone_value: number | null;
  max_value: number | null;
  datum_refs: PmiDatumRef[];
  datum_names: string[];      // derived, ordered by precedence
  face_ids: number[];
  edge_ids: number[];
}
export interface PmiDatum {
  id: number;
  kind: 'datum';
  name: string | null;
  face_ids: number[];
  edge_ids: number[];
}
export interface PmiData {
  schema: number;
  dimensions: PmiDimension[];
  tolerances: PmiTolerance[];
  datums: PmiDatum[];
  /** constructs that won't survive an AP242 round-trip (informational) */
  warnings?: string[];
  /** OCCT's GD&T transfer crashed at import; entities are empty */
  degraded?: boolean;
}

/** the fine mesh: raw typed-array URLs the viewer fetches directly */
export interface MeshLevel {
  counts: { verts: number; faces: number };
  verts_url: string;
  faces_url: string;
  normals_url: string;
}

/** the cheap coarse display preview (partial counts; display-only geometry) */
export interface CoarseMesh {
  counts: { verts?: number; faces?: number };
  verts_url: string;
  faces_url: string;
  normals_url: string;
  /** per-coarse-triangle BREP id for preview coloring */
  brep_faces_url?: string;
}

/** One candidate approach direction's provenance, index-aligned to
 * Manifest.directions (both rows of an antipodal pair share source/detail). */
export interface DirectionSource {
  index: number;
  source: 'uniform' | 'principal_axis' | 'bbox_axis' | 'hole_axis'
    | 'face_normal' | 'average_normal' | 'manual';
  label: string;
  detail: Record<string, any>;
  /** Area-weighted share of the part visible from this direction, computed
   * with the accessibility rows so a per-direction overview needs no field
   * fetches (absent on sets built before this was stored). */
  accessible_area?: number;
  accessible_fraction?: number;
}

/** A geometric candidate axis the client can add to the direction set live
 * (hole/cylinder axes need the analytic BREP surfaces, so the server ships them). */
export interface HoleCandidate {
  axis: [number, number, number];
  detail: Record<string, any>;
}

/** Mirror of OPERATION_KINDS in route.py, which rejects an unknown kind in
 * validate_route. Dispatches the operation card's icon and its configurable
 * fields, so a kind nobody handles is a card with neither. */
export type OperationKind = 'laser' | 'milling' | 'turning' | 'press_brake';

/** One ordered operation on the part's route (route.py, route.json).
 *
 * ATOMIC: one approach direction, one bend, one turning axis. Grouping
 * several onto one machine setup is a later inference over the list, never
 * authored here — which is why there is no tilt cone on an operation. */
export interface Operation {
  id: string;
  kind?: OperationKind;
  label?: string;
  config?: Record<string, any>;
  /** Machine profile NAME from the catalogue library, not a copy. */
  machine?: string;
  /** Declarative workpiece-state annotation: what this operation produces
   * over the final-part face space (e.g. {features: "holes"}). */
  produces?: Record<string, any>;
}

export interface MachineSummary {
  name: string;
  label: string;
  kind?: string | null;
  /** Repo-relative catalogue path, for check params that name a machine. */
  path: string;
}

/** One result a check reads. A check that interprets several fields carries
 * `sources` INSTEAD of `analysis`/`params` — route.py validates that it is
 * one or the other, never both. */
export interface CheckSource {
  id: string;
  /** Backend analysis id, "process/analysis". */
  analysis: string;
  /** Declared analysis params, literal — these ARE the cache key's input. */
  params: Record<string, any>;
}

export interface RouteCheck {
  id: string;
  /** Author-supplied name (expression checks; others read theirs off the
   * catalog). */
  label?: string;
  /** Backend analysis id, "process/analysis" — single-source checks. */
  analysis?: string;
  /** Declared analysis params, literal — these ARE the cache key's input. */
  params?: Record<string, any>;
  /** Several results, when one is not enough. */
  sources?: CheckSource[];
  /** Pinned interpretation thresholds — the verdict's inputs. */
  policy?: Record<string, any>;
  operation?: string | null;
  /** Preferred inspection lens key ("processId:modeId"). */
  lens?: string;
}

export interface Route {
  schema: number;
  revision: number;
  operations: Operation[];
  checks: RouteCheck[];
}

/** Server-derived execution facts for one check (never authored). */
export interface RouteCheckStatus {
  /** Null on a multi-source check — it has one hash per source, not one. */
  expected_hash: string | null;
  /** Merged params — submit these verbatim to run the check. */
  params: Record<string, any> | null;
  /** Rolled up over `sources`: `exists` only when EVERY source is on disk,
   * because an expression over two fields cannot run on one of them. */
  exists: boolean;
  stale: boolean;
  error: string | null;
  /** Present on multi-source checks: the same facts, per source id. */
  sources?: Record<string, Omit<RouteCheckStatus, 'sources'>>;
}

export interface RouteSection {
  route: Route;
  checks: Record<string, RouteCheckStatus>;
}

export interface Manifest {
  part: Part;
  mesh: MeshLevel | null;
  /** cheap display preview available before the fine mesh (first-load bundle) */
  coarse_mesh?: CoarseMesh | null;
  directions: number[][];
  /** where each direction came from (uniform, axis, hole, manual, …) */
  direction_sources?: DirectionSource[];
  /** analytic hole/cylinder axes the client can add live */
  hole_candidates?: HoleCandidate[];
  /** directions were computed on an older mesh — re-run prep/directions */
  directions_stale?: boolean;
  fields: FieldDescriptor[];
  results: ResultEntry[];
  highlights_url: string | null;
  /** present when the part carries STEP colors/names (face_attrs.json) */
  face_attrs_url?: string;
  /** per-BREP-face surface types + analytic params (brep_meta.json) */
  brep_meta_url?: string;
  /** present when the part carries semantic PMI (pmi.json) */
  pmi_url?: string;
  /** PMI summary for the viewer: degraded flag, round-trip warnings, counts,
   * and the AP242 export endpoints */
  pmi?: {
    url: string;
    degraded: boolean;
    warnings: string[];
    counts: { dimensions: number; tolerances: number; datums: number };
    export_url: string;
    export_report_url: string;
  };
  /** present on imported assembly records (assembly.json) */
  assembly_url?: string;
  /** AAG stage summary (prep/aag): stats + mesh staleness */
  aag?: { schema: number; stats: Record<string, any>; stale: boolean };
  /** operations + checks + derived check status (docs/ROUTE-ARCHITECTURE.md) */
  route?: RouteSection;
}

/** Mirror of PARAM_TYPES in processes/base.py, which validates it in
 * Param.__post_init__ — i.e. at backend import time. */
export type ParamType = 'bool' | 'int' | 'number' | 'string' | 'select'
  | 'int_list' | 'number_list' | 'tip_list' | 'tool_list' | 'vector_list'
  | 'group_list';

export interface ParamSpec {
  name: string;
  type: ParamType;
  default: any;
  label?: string;
  unit?: string;
  min?: number;
  max?: number;
  options?: string[];
}

export interface AnalysisInfo {
  id: string;
  label: string;
  description: string;
  requires: string[];
  params: ParamSpec[];
}

export interface ProcessInfo {
  id: string;
  label: string;
  description: string;
  analyses: AnalysisInfo[];
}

export interface Job {
  id: number;
  part_id: string;
  process: string;
  analysis: string;
  params: Record<string, any>;
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled';
  progress: number;
  message: string;
  error: string | null;
  result: { stats: Record<string, any>; fields: string[] } | null;
  created: string;
}
