// TS mirrors of the backend JSON contract (api/schemas + api/manifest).

export interface Part {
  id: string;
  name: string;
  source: string | null;
  status: 'raw' | 'meshed';
  counts: { verts: number; faces: number } | null;
  has_directions: boolean;
  created: string | null;
}

export type FieldRole = 'scalar' | 'mask' | 'category' | 'lines' | 'data'
  | 'nodes' | 'radii' | 'edges' | 'vert_map';

export interface FieldDescriptor {
  id: string;
  association: 'vertex' | 'face' | 'none' | 'graph';
  dtype: 'f4' | 'u1' | 'u4';
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

/** Semantic PMI / GD&T (pmi.json, schema 2). Face ids are 0-based BREP ids
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
}

/** A geometric candidate axis the client can add to the direction set live
 * (hole/cylinder axes need the analytic BREP surfaces, so the server ships them). */
export interface HoleCandidate {
  axis: [number, number, number];
  detail: Record<string, any>;
}

/** Production plan sidecars + derived check status (plans.py, plan.json). */
export interface PlanOperation {
  id: string;
  kind?: string;
  label?: string;
  config?: Record<string, any>;
  machine?: { template: string; sha: string };
  /** Declarative workpiece-state annotation: what this operation produces
   * over the final-part face space (e.g. {features: "holes"}). */
  produces?: Record<string, any>;
  /** Structured quotation inputs (setup count, bend count, …). */
  outputs?: Record<string, any>;
}

export interface RouteSummary {
  name: string;
  title: string;
  operations: number;
}

export interface PlanCheck {
  id: string;
  /** Backend analysis id, "process/analysis". */
  analysis: string;
  /** Declared analysis params; values may be {"$plan": "dotted.path"}. */
  params: Record<string, any>;
  /** Pinned interpretation thresholds — the verdict's inputs. */
  policy?: Record<string, any>;
  operation?: string | null;
  /** Preferred inspection lens key ("processId:modeId"). */
  lens?: string;
  visible?: boolean;
}

export interface Plan {
  schema: number;
  revision: number;
  decisions: Record<string, any>;
  operations: PlanOperation[];
  checks: PlanCheck[];
}

/** Server-derived execution facts for one plan check (never authored). */
export interface PlanCheckStatus {
  expected_hash: string | null;
  /** Materialized params — submit these verbatim to run the check. */
  params: Record<string, any> | null;
  exists: boolean;
  stale: boolean;
  error: string | null;
}

export interface DispositionEvent {
  finding_id: string;
  state: 'open' | 'accepted' | 'customer_approval' | 'resolved';
  by: string;
  at: string;
  why: string;
  evidence: Record<string, any>;
}

export interface PlanSection {
  plan: Plan;
  checks: Record<string, PlanCheckStatus>;
  /** Latest disposition per finding id. */
  dispositions: Record<string, DispositionEvent>;
}

/** Published report bundle (plans.py reports/<rid>/report.json). */
export interface ReportCheck {
  id: string;
  label: string;
  verdict: string;
  findings: { id: string; code?: string; label?: string; detail?: string;
    severity?: string }[];
  evidence: Record<string, any>;
  /** Bundle-relative shot filename, when captured. */
  shot?: string | null;
}

export interface Report {
  schema: number;
  rid: string;
  title: string;
  part: string;
  plan_revision: number;
  published_at: string;
  dispositions: Record<string, DispositionEvent>;
  checks: ReportCheck[];
}

export interface ReportSummary {
  rid: string;
  title: string;
  part: string;
  plan_revision: number;
  published_at: string;
  check_count: number;
}

export interface Manifest {
  part: Part;
  mesh: MeshLevel | null;
  /** cheap display preview available before the fine mesh (first-load bundle) */
  coarse_mesh?: CoarseMesh | null;
  /** the coarse preview is showing while the fine mesh is still pending */
  fine_pending?: boolean;
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
  /** present when the part carries semantic PMI (pmi.json) */
  pmi_url?: string;
  /** present on imported assembly records (assembly.json) */
  assembly_url?: string;
  /** AAG stage summary (prep/aag): stats + mesh staleness */
  aag?: { schema: number; stats: Record<string, any>; stale: boolean };
  /** production plan + derived check status (docs/PLAN-ARCHITECTURE.md) */
  plan?: PlanSection;
}

export interface ParamSpec {
  name: string;
  type: 'bool' | 'int' | 'number' | 'string' | 'select' | 'int_list' | 'number_list' | 'tip_list' | 'tool_list' | 'vector_list' | 'group_list';
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
