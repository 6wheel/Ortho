"""
kn5_reader.py — Original, from-scratch reader for Assetto Corsa .kn5 model files.

This is an independent implementation written by reasoning about the documented
KN5 structure (three sections: textures, materials, node tree; see
https://site.hagn.io/assettocorsa/modding/kn5-files for the high-level layout),
NOT copied or adapted from any existing converter's source code.

KN5 is a simple sequential binary format:
    1. Header: magic bytes + version int
    2. Texture table: count, then [type, name, byte-length, raw bytes] per texture
    3. Material table: count, then [name, shader name, flags, float properties,
       texture-slot bindings] per material
    4. Node tree: recursive — each node is one of:
         - "base" node: just a name + 4x4 transform matrix + children
         - "mesh" node: static geometry (positions, normals, UVs, triangle indices)
         - "skinned mesh" node: mesh with bone weights (rare on car exteriors;
           treated as a static mesh here since we only need the rendered shape,
           not animation — bone data is read and discarded so the byte offsets
           stay correct for whatever follows)

ASSUMPTIONS (to be verified against a real file — flagged clearly since this
is a reverse-engineered format with no official public spec):
    - All integers are 4-byte little-endian signed ('<i').
    - All floats are 4-byte little-endian ('<f').
    - Strings are stored as [4-byte length][raw UTF-8 bytes], no null terminator.
    - The node tree is stored as a flat pre-order sequence: each node record is
      immediately followed by its children's records, with an explicit
      "child count" field telling the reader how many children follow.

If parsing fails partway through (e.g. lengths don't line up), this module
raises a clear error naming which section/byte-offset failed, rather than
silently producing garbage geometry.
"""

import struct
import os


class Kn5Texture:
    __slots__ = ("name", "tex_type", "data")

    def __init__(self, name, tex_type, data):
        self.name = name
        self.tex_type = tex_type
        self.data = data


class Kn5Material:
    __slots__ = ("name", "shader", "properties", "textures")

    def __init__(self, name, shader):
        self.name = name
        self.shader = shader
        self.properties = {}   # property name -> float value
        self.textures = {}     # shader sample slot name -> texture filename


class Kn5Node:
    __slots__= (
        "node_type", "name", "parent", "children", "local_matrix", "world_matrix",
        "material_id", "vertices", "normals", "uvs", "indices",
    )

    BASE = 1
    MESH = 2
    SKINNED_MESH = 3

    def __init__(self):
        self.node_type = None
        self.name = ""
        self.parent = None
        self.children = []
        self.local_matrix = None     # 4x4, row-major list of lists, or None for mesh/skinned nodes with no own transform
        self.world_matrix = None     # computed after full tree is read
        self.material_id = -1
        self.vertices = []           # flat list of floats, 3 per vertex (local space)
        self.normals = []            # flat list of floats, 3 per vertex
        self.uvs = []                # flat list of floats, 2 per vertex
        self.indices = []            # flat list of ints, 3 per triangle


class Kn5ParseError(Exception):
    pass


def _read_exact(f, n, context):
    data = f.read(n)
    if len(data) != n:
        raise Kn5ParseError(
            f"Unexpected end of file while reading {context}: "
            f"wanted {n} bytes, got {len(data)} at byte offset {f.tell()}"
        )
    return data


def _read_i32(f, context="int32"):
    return struct.unpack("<i", _read_exact(f, 4, context))[0]


def _read_f32(f, context="float32"):
    return struct.unpack("<f", _read_exact(f, 4, context))[0]


def _read_string(f, context="string"):
    length = _read_i32(f, f"{context} length")
    if length < 0 or length > 50_000_000:
        raise Kn5ParseError(
            f"Implausible string length {length} for {context} at byte offset {f.tell()}; "
            f"file is likely not a valid KN5 or the parser has desynced."
        )
    raw = _read_exact(f, length, context)
    return raw.decode("utf-8", errors="replace")


def identity_matrix():
    return [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]


def matmul4(a, b):
    result = [[0.0] * 4 for _ in range(4)]
    for i in range(4):
        for j in range(4):
            result[i][j] = sum(a[i][k] * b[k][j] for k in range(4))
    return result


def _read_local_matrix(f):
    """Reads 16 floats, row-major, as written by the exporter."""
    vals = struct.unpack("<16f", _read_exact(f, 64, "4x4 transform matrix"))
    return [list(vals[i * 4:(i + 1) * 4]) for i in range(4)]


def read_kn5(path):
    """Parses a .kn5 file and returns (textures: list[Kn5Texture],
    materials: list[Kn5Material], root_nodes: list[Kn5Node])."""
    with open(path, "rb") as f:
        magic = _read_exact(f, 6, "magic header")
        if magic != b"sc6969":
            raise Kn5ParseError(
                f"File does not start with the expected KN5 magic bytes "
                f"(got {magic!r}). This may not be a valid .kn5 file, or uses "
                f"an unsupported variant."
            )
        version = _read_i32(f, "version")
        if version > 5:
            # Newer versions carry one extra int here (purpose undocumented
            # publicly; some converters skip it unconditionally for v6+).
            _read_i32(f, "post-version extra int")

        # ---- Textures ----
        textures = []
        tex_count = _read_i32(f, "texture count")
        for i in range(tex_count):
            tex_type = _read_i32(f, f"texture[{i}] type")
            tex_name = _read_string(f, f"texture[{i}] name")
            tex_size = _read_i32(f, f"texture[{i}] byte size")
            tex_data = _read_exact(f, tex_size, f"texture[{i}] data")
            textures.append(Kn5Texture(tex_name, tex_type, tex_data))

        # ---- Materials ----
        materials = []
        mat_count = _read_i32(f, "material count")
        for i in range(mat_count):
            name = _read_string(f, f"material[{i}] name")
            shader = _read_string(f, f"material[{i}] shader name")
            mat = Kn5Material(name, shader)

            _read_exact(f, 2, f"material[{i}] flags (2 bytes)")
            if version > 4:
                _read_i32(f, f"material[{i}] post-flags extra int")

            prop_count = _read_i32(f, f"material[{i}] property count")
            for p in range(prop_count):
                prop_name = _read_string(f, f"material[{i}] property[{p}] name")
                prop_value = _read_f32(f, f"material[{i}] property[{p}] value")
                # Each property is documented elsewhere as carrying a fixed
                # block of extra float data (min/max/default range) we don't
                # need for rendering; skip it. (3 floats observed in known
                # converters -> 12 bytes.)
                _read_exact(f, 36, f"material[{i}] property[{p}] padding")
                mat.properties[prop_name] = prop_value

            tex_slot_count = _read_i32(f, f"material[{i}] texture slot count")
            for t in range(tex_slot_count):
                sample_name = _read_string(f, f"material[{i}] texslot[{t}] sample name")
                _read_i32(f, f"material[{i}] texslot[{t}] slot index")
                tex_name = _read_string(f, f"material[{i}] texslot[{t}] texture name")
                mat.textures[sample_name] = tex_name

            materials.append(mat)

        # ---- Node tree (recursive, pre-order) ----
        def read_node(parent):
            node = Kn5Node()
            node.node_type = _read_i32(f, "node type")
            node.name = _read_string(f, "node name")
            child_count = _read_i32(f, "node child count")
            _read_exact(f, 1, "node active flag byte")

            if node.node_type == Kn5Node.BASE:
                node.local_matrix = _read_local_matrix(f)

            elif node.node_type == Kn5Node.MESH:
                _read_exact(f, 3, "mesh cast-shadows/visible/transparent flag bytes")
                vertex_count = _read_i32(f, "mesh vertex count")
                for _ in range(vertex_count):
                    node.vertices.extend(struct.unpack("<3f", _read_exact(f, 12, "vertex position")))
                    node.normals.extend(struct.unpack("<3f", _read_exact(f, 12, "vertex normal")))
                    u, v = struct.unpack("<2f", _read_exact(f, 8, "vertex uv"))
                    node.uvs.extend((u, 1.0 - v))  # flip V to match standard OBJ/OpenGL convention
                    _read_exact(f, 12, "vertex tangent (unused)")

                index_count = _read_i32(f, "mesh index count")
                if os.environ.get("KN5_DEBUG"):
                    print(f"[DEBUG] node '{node.name}': vertex_count={vertex_count} index_count={index_count} "
                          f"(as triangles: {index_count/3})")
                node.indices = list(struct.unpack(f"<{index_count}H", _read_exact(f, index_count * 2, "mesh indices")))
                node.material_id = _read_i32(f, "mesh material id")
                _read_exact(f, 29, "mesh layer/lod/bounding-sphere trailer (unused)")

            elif node.node_type == Kn5Node.SKINNED_MESH:
                _read_exact(f, 3, "skinned mesh flag bytes")
                bone_count = _read_i32(f, "skinned mesh bone count")
                for b in range(bone_count):
                    _read_string(f, f"skinned mesh bone[{b}] name")
                    _read_exact(f, 64, f"skinned mesh bone[{b}] transform (unused)")
                vertex_count = _read_i32(f, "skinned mesh vertex count")
                for _ in range(vertex_count):
                    node.vertices.extend(struct.unpack("<3f", _read_exact(f, 12, "vertex position")))
                    node.normals.extend(struct.unpack("<3f", _read_exact(f, 12, "vertex normal")))
                    u, v = struct.unpack("<2f", _read_exact(f, 8, "vertex uv"))
                    node.uvs.extend((u, 1.0 - v))
                    _read_exact(f, 28, "vertex tangent + bone weights/indices (unused)")
                index_count = _read_i32(f, "skinned mesh index count")
                node.indices = list(struct.unpack(f"<{index_count}H", _read_exact(f, index_count * 2, "skinned mesh indices")))
                node.material_id = _read_i32(f, "skinned mesh material id")
                _read_exact(f, 12, "skinned mesh trailer (unused)")
                # Skinned meshes have no local_matrix of their own; world position
                # comes from bone transforms, which we are not animating. Treat
                # as already in parent's local space (no extra transform).
                node.local_matrix = identity_matrix()

            else:
                raise Kn5ParseError(
                    f"Unknown node type {node.node_type} for node '{node.name}' "
                    f"at byte offset {f.tell()}; parser has likely desynced."
                )

            node.parent = parent
            for _ in range(child_count):
                child = read_node(node)
                node.children.append(child)
            return node

        root_count_marker = f.tell()
        root = read_node(None)
        # Most KN5 files have a single root node containing everything else.
        root_nodes = [root]

        # sanity check: we should be at end of file now (or very close —
        # some files have small trailing metadata blocks we don't need)
        remaining = f.read()
        if len(remaining) > 4096:
            raise Kn5ParseError(
                f"Parsing finished but {len(remaining)} unexpected bytes remain "
                f"in the file — this strongly suggests a field was misread "
                f"upstream and the parser desynced silently. Refusing to "
                f"trust the result."
            )

    _compute_world_matrices(root_nodes, identity_matrix())
    return textures, materials, root_nodes


def _compute_world_matrices(nodes, parent_world):
    for node in nodes:
        if node.local_matrix is not None:
            node.world_matrix = matmul4(node.local_matrix, parent_world)
        else:
            node.world_matrix = parent_world
        _compute_world_matrices(node.children, node.world_matrix)


def iter_mesh_nodes(root_nodes):
    """Yields every mesh/skinned-mesh node in the tree, depth-first."""
    for node in root_nodes:
        if node.node_type in (Kn5Node.MESH, Kn5Node.SKINNED_MESH):
            yield node
        yield from iter_mesh_nodes(node.children)
