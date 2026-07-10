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
  overrides_url?: string;
}

export interface Manifest {
  part: Part;
  mesh: {
    counts: { verts: number; faces: number };
    verts_url: string;
    faces_url: string;
    normals_url: string;
  } | null;
  directions: number[][];
  fields: FieldDescriptor[];
  results: ResultEntry[];
  highlights_url: string | null;
}

export interface ParamSpec {
  name: string;
  type: 'bool' | 'int' | 'number' | 'string' | 'select' | 'int_list' | 'number_list' | 'tip_list';
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
