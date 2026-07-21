// Client-side supplement to the served BREP boundary segments: the backend's
// brep_edges.npy holds interior manifold edges only (edges shared by exactly
// two triangles whose BREP ids differ), so open sheet/surface parts would be
// missing their naked boundaries. This derives them from the mesh itself:
// any undirected edge owned by exactly one triangle.

/** Segment endpoints (N*2*3 floats) of every naked (single-owner) mesh edge. */
export function nakedEdgeSegments(
  verts: Float32Array, faces: Uint32Array,
): Float32Array {
  // undirected edge key: lo * 2^26 + hi — exact in a double for < 67M verts
  const KEY = 67108864;
  const count = new Map<number, number>();
  const edgeOf = (a: number, b: number) => (a < b ? a * KEY + b : b * KEY + a);
  for (let i = 0; i < faces.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const key = edgeOf(faces[i + k], faces[i + ((k + 1) % 3)]);
      count.set(key, (count.get(key) ?? 0) + 1);
    }
  }
  let n = 0;
  for (const c of count.values()) if (c === 1) n++;
  const out = new Float32Array(n * 6);
  let o = 0;
  for (const [key, c] of count) {
    if (c !== 1) continue;
    const a = Math.floor(key / KEY);
    const b = key % KEY;
    for (let axis = 0; axis < 3; axis++) out[o + axis] = verts[3 * a + axis];
    for (let axis = 0; axis < 3; axis++) out[o + 3 + axis] = verts[3 * b + axis];
    o += 6;
  }
  return out;
}
