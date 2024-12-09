from meshlib import mrmeshpy as mm
from meshlib import mrmeshnumpy as mn

def get_inside(mesh_a, mesh_b):
    bOperation = mm.BooleanOperation.InsideA
    bResMapper = mm.BooleanResultMapper()
    bResult = mm.boolean(mesh_a, mesh_b, bOperation, None, bResMapper)

    inner_faces = bResMapper.map(mesh_a.topology.getValidFaces(), mm.BooleanResMapObj.A)
    inner_verts = bResMapper.map(mesh_a.topology.getValidVerts(), mm.BooleanResMapObj.A)
    
    return mn.getNumpyBitSet(inner_faces), mn.getNumpyBitSet(inner_verts)

torusIntersected = mm.makeTorusWithSelfIntersections(2, 1, 10, 10, None)
mm.fixSelfIntersections(torusIntersected, 0.1)

torus = mm.makeTorus(2, 1, 10, 10, None)

transVector = mm.Vector3f()
transVector.x = 0.5
transVector.y = 1
transVector.z = 1

diffXf = mm.AffineXf3f.translation(transVector)

torus2 = mm.makeTorus(2, 1, 10, 10, None)
torus2.transform(diffXf)

print(f"Valid faces: {torus.topology.getValidFaces().size()}")
print(f"Valid verts: {torus.topology.getValidVerts().size()}")

inside_faces, inside_verts = get_inside(mesh_a=torus, mesh_b=torus2)

print(f"Inside faces: {inside_faces.size}")
print(f"Inside verts: {inside_verts.size}")

print(inside_faces)
print(inside_verts)
