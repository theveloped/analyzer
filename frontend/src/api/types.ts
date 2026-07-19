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

export interface Manifest {
  part: Part;
  mesh: MeshLevel | null;
  /** cheap display preview available before the fine mesh (first-load bundle) */
  coarse_mesh?: CoarseMesh | null;
  /** the coarse preview is showing while the fine mesh is still pending */
  fine_pending?: boolean;
  directions: number[][];
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
}

export interface ParamSpec {
  name: string;
  type: 'bool' | 'int' | 'number' | 'string' | 'select' | 'int_list' | 'number_list' | 'tip_list' | 'tool_list';
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
  status: 'queued' | 'running' | 'done' | 'error';
  progress: number;
  message: string;
  error: string | null;
  result: { stats: Record<string, any>; fields: string[] } | null;
  created: string;
}
