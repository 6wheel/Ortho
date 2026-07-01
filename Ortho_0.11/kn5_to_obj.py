"""
kn5_to_obj.py — Converts a parsed KN5 scene (from kn5_reader.py) into a single
OBJ file, with one named group per KN5 mesh node, all vertices transformed
into world space using the node's accumulated parent transform chain.
"""

import sys
import kn5_reader


def transform_point(matrix, x, y, z):
    # matrix is row-major 4x4; KN5 transforms are typically stored so that
    # row-vector * matrix gives the transformed point (common in this kind
    # of exporter convention) — verified empirically against known part
    # positions (see verification step in conversation).
    px = x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0] + matrix[3][0]
    py = x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1] + matrix[3][1]
    pz = x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2] + matrix[3][2]
    return px, py, pz


def transform_normal(matrix, x, y, z):
    # Normals use only the rotation/scale part (no translation).
    nx = x * matrix[0][0] + y * matrix[1][0] + z * matrix[2][0]
    ny = x * matrix[0][1] + y * matrix[1][1] + z * matrix[2][1]
    nz = x * matrix[0][2] + y * matrix[1][2] + z * matrix[2][2]
    return nx, ny, nz


def convert(kn5_path, obj_path):
    textures, materials, roots = kn5_reader.read_kn5(kn5_path)
    mesh_nodes = list(kn5_reader.iter_mesh_nodes(roots))

    with open(obj_path, "w") as out:
        vertex_offset = 0
        for node in mesh_nodes:
            if not node.vertices:
                continue
            n_verts = len(node.vertices) // 3
            mat = node.world_matrix

            out.write(f"g {node.name}\n")
            for i in range(n_verts):
                x, y, z = node.vertices[i*3:i*3+3]
                wx, wy, wz = transform_point(mat, x, y, z)
                out.write(f"v {wx} {wy} {wz}\n")
            for i in range(n_verts):
                nx, ny, nz = node.normals[i*3:i*3+3]
                wnx, wny, wnz = transform_normal(mat, nx, ny, nz)
                out.write(f"vn {wnx} {wny} {wnz}\n")
            for i in range(n_verts):
                u, v = node.uvs[i*2:i*2+2]
                out.write(f"vt {u} {v}\n")

            n_tris = len(node.indices) // 3
            for t in range(n_tris):
                i0, i1, i2 = node.indices[t*3:t*3+3]
                # OBJ indices are 1-based and global across the whole file
                a = vertex_offset + i0 + 1
                b = vertex_offset + i1 + 1
                c = vertex_offset + i2 + 1
                out.write(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")

            vertex_offset += n_verts

    print(f"Wrote {obj_path}: {len(mesh_nodes)} groups")


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
