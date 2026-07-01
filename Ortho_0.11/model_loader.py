"""
model_loader.py — Unified entry point for loading a 3D model file of any
supported format into a clean trimesh.Trimesh, ready for rendering.

Supported formats:
    .obj            - direct trimesh load
    .dae            - direct trimesh load (via pycollada)
    .stl            - direct trimesh load
    .gltf / .glb    - direct trimesh load
    .kn5            - converted via our own from-scratch parser (kn5_reader.py
                       / kn5_to_obj.py) to a temporary OBJ, then loaded normally

Every path strips texture visuals before returning, because trimesh
auto-generates a TextureVisuals object whenever UV coordinates are present
(even with no actual image), and pyrender's texture upload crashes on that
in this environment. We don't need textures for line-art rendering anyway.
"""

import os
import re
import tempfile
import numpy as np
import trimesh

from kn5_to_obj import convert as kn5_convert

SUPPORTED_EXTENSIONS = {".obj", ".dae", ".stl", ".gltf", ".glb", ".kn5"}


class ModelLoadError(Exception):
    pass


def strip_texture_visuals(mesh):
    """Removes any texture/material visual data, replacing with plain
    color visuals. Required before any pyrender render call in this
    environment (see STATUS.md for the crash this avoids)."""
    mesh.visual = trimesh.visual.ColorVisuals(mesh)
    return mesh


def _parse_obj_groups(path):
    """Manually parses an OBJ file's 'o'/'g' group declarations and tracks
    which faces belong to which named group.

    trimesh's scene loader groups faces by MATERIAL, not by the original
    o/g group names, which destroys real per-part names (confirmed: a
    41-group OBJ collapses to ~16 material-based groups under trimesh's
    default loading). This function preserves the real names instead.

    Tolerant of mixed/non-standard line endings and of group lines that
    have a leading space before 'o '/'g ' (both real quirks seen in actual
    exported files during development).
    """
    vertices = []
    groups = {}
    current = "(ungrouped)"
    groups[current] = []

    with open(path, "r", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s[0] == "v" and (len(s) == 1 or s[1] == " "):
                parts = s.split()
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            elif s[0] in ("o", "g") and (len(s) == 1 or s[1] == " "):
                name = s.split(None, 1)[1].strip() if len(s.split(None, 1)) > 1 else "(unnamed)"
                if name not in groups:
                    groups[name] = []
                current = name
            elif s[0] == "f" and (len(s) == 1 or s[1] == " "):
                face_parts = s.split()[1:]
                idx = [int(p.split("/")[0]) - 1 for p in face_parts]
                groups[current].append(idx)

    if not groups.get("(ungrouped)"):
        groups.pop("(ungrouped)", None)

    return np.array(vertices), groups


def load_model(path):
    """Loads any supported 3D model file and returns (mesh, part_face_ranges)
    where part_face_ranges maps {part_name: (face_start_index, face_end_index)}
    against mesh.faces, so the caller can map checkbox selections back to
    actual faces regardless of input format.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ModelLoadError(
            f"Unsupported file type '{ext}'. Supported types: "
            f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if ext == ".kn5":
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_obj = os.path.join(tmpdir, "converted.obj")
            try:
                kn5_convert(path, tmp_obj)
            except Exception as e:
                raise ModelLoadError(f"Failed to convert KN5 file: {e}") from e
            return _load_obj_with_real_groups(tmp_obj)

    if ext == ".obj":
        return _load_obj_with_real_groups(path)

    return _load_with_parts(path)


def _load_obj_with_real_groups(path):
    """OBJ-specific path: uses our manual parser to preserve real o/g group
    names (our own KN5->OBJ exporter also writes 'g' lines per part, so this
    path is shared by both native OBJ files and KN5-converted ones)."""
    vertices, groups = _parse_obj_groups(path)
    if len(vertices) == 0:
        raise ModelLoadError("OBJ file contains no vertices.")

    all_faces = []
    part_face_ranges = {}
    face_offset = 0
    for name, faces in groups.items():
        if not faces:
            continue
        all_faces.extend(faces)
        part_face_ranges[name] = (face_offset, face_offset + len(faces))
        face_offset += len(faces)

    if not all_faces:
        raise ModelLoadError("OBJ file contains no faces.")

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.array(all_faces), process=False)
    mesh.remove_unreferenced_vertices()
    strip_texture_visuals(mesh)
    return mesh, part_face_ranges


def _load_with_parts(path):
    """Loads a model and tracks which face-index range belongs to which
    named part/group, so the UI can offer per-part checkboxes regardless
    of input format."""
    ext = os.path.splitext(path)[1].lower()

    try:
        scene_or_mesh = trimesh.load(path, process=False, force='scene')
    except Exception as e:
        raise ModelLoadError(f"Failed to load {ext} file: {e}") from e

    part_face_ranges = {}
    all_vertices = []
    all_faces = []
    vertex_offset = 0
    face_offset = 0

    if isinstance(scene_or_mesh, trimesh.Scene):
        geometries = scene_or_mesh.geometry
        if not geometries:
            raise ModelLoadError("File loaded but contains no mesh geometry.")
        # Apply each geometry's scene-graph transform so parts end up in
        # correct world-space position (matters for any format that uses
        # per-node transforms, e.g. DAE/GLTF scene graphs).
        for node_name in scene_or_mesh.graph.nodes_geometry:
            transform, geom_name = scene_or_mesh.graph[node_name]
            geom = geometries[geom_name]
            verts = trimesh.transform_points(geom.vertices, transform)
            faces = geom.faces + vertex_offset

            all_vertices.append(verts)
            all_faces.append(faces)

            n_faces = len(faces)
            part_name = geom_name if geom_name else node_name
            # Disambiguate duplicate names (some formats reuse geometry across nodes)
            base_name = part_name
            suffix = 1
            while part_name in part_face_ranges:
                part_name = f"{base_name}_{suffix}"
                suffix += 1
            part_face_ranges[part_name] = (face_offset, face_offset + n_faces)

            vertex_offset += len(verts)
            face_offset += n_faces

        import numpy as np
        merged = trimesh.Trimesh(
            vertices=np.vstack(all_vertices),
            faces=np.vstack(all_faces),
            process=False,
        )
    else:
        merged = scene_or_mesh
        part_face_ranges = {"(whole model)": (0, len(merged.faces))}

    merged.remove_unreferenced_vertices()
    strip_texture_visuals(merged)
    return merged, part_face_ranges


def build_filtered_mesh(mesh, part_face_ranges, excluded_parts):
    """Given the full loaded mesh and a set of excluded part names, returns
    a new trimesh.Trimesh containing only the faces from non-excluded parts.
    """
    import numpy as np
    keep_mask = np.ones(len(mesh.faces), dtype=bool)
    for name, (start, end) in part_face_ranges.items():
        if name in excluded_parts:
            keep_mask[start:end] = False

    faces = mesh.faces[keep_mask]
    filtered = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=faces, process=False)
    filtered.remove_unreferenced_vertices()
    strip_texture_visuals(filtered)
    return filtered


# ---------------------------------------------------------------------------
# ZIP upload support
# ---------------------------------------------------------------------------
# Real mod downloads (Assetto Corsa cars in particular, but the same pattern
# shows up for generic OBJ/FBX-style "free car model" zips too) commonly
# bundle multiple LOD ("level of detail") meshes of the SAME car in one zip
# — confirmed via AC's own documented modding pipeline: a car has
# carname.kn5 (LOD_0, full detail), then carname_B.kn5, carname_C.kn5,
# carname_D.kn5 (progressively simplified, used at increasing camera
# distance for performance). Silently picking the wrong one would produce
# a technically-valid but visually wrong (way too simple) reference sheet,
# which is worse than refusing to guess — this is explicitly why the
# selection logic below has two tiers: an authoritative one when the real
# answer is available, and a conservative, explained fallback otherwise.

# Suffixes/keywords that mark a file as deliberately NOT the main/highest
# detail mesh, ordered roughly by how strong a signal they are. Checked
# case-insensitively against the filename (without extension).
_LOD_DEMOTION_PATTERNS = [
    # AC's own documented convention: carname_B/_C/_D.kn5 are LOD 1/2/3.
    # Matches a trailing _B, _C, or _D (whole suffix, not e.g. "_Bumper").
    re.compile(r"_[bcd]$", re.IGNORECASE),
    re.compile(r"_lod[1-9]\b", re.IGNORECASE),
    re.compile(r"\blod[1-9]\b", re.IGNORECASE),
]
_NON_MAIN_KEYWORDS = [
    "collider", "collision", "proxy", "shadow", "_low", "lowpoly",
    "low_poly", "simple", "_dummy", "preview", "thumb", "icon",
]


def _score_candidate_for_main_mesh(filename, filesize):
    """Higher score = more likely to be the main/highest-detail mesh.
    Used only as a FALLBACK when no lods.ini is present to give a real
    answer — see find_main_model_in_zip()'s docstring for why this is
    deliberately conservative rather than clever.
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    score = 0.0

    for pattern in _LOD_DEMOTION_PATTERNS:
        if pattern.search(stem):
            score -= 1000.0  # near-disqualifying: this IS a lower LOD by name
            break

    lower_stem = stem.lower()
    for kw in _NON_MAIN_KEYWORDS:
        if kw in lower_stem:
            score -= 500.0
            break

    # Among files with no demoting signal at all, file size is the most
    # reliable remaining proxy: a LOD_0/main mesh is, by definition, the
    # most detailed and therefore (almost always) the largest file of the
    # same car. This only matters as a tie-breaker once naming-based
    # demotion above has already ruled out anything that LOOKS like a
    # lower LOD by name.
    score += filesize / 1e6  # MB, small contribution relative to the
                              # naming penalties above, which dominate
    return score


def find_main_model_in_zip(zip_path):
    """Inspects a zip archive and decides which single file inside is the
    main/highest-detail 3D model to load, returning its name within the
    archive (a path like "mycar/mycar.kn5"), or raises ModelLoadError with
    a clear explanation if it can't make a confident choice.

    TWO-TIER approach, deliberately not "just guess the biggest file" as
    the only rule, because picking the wrong LOD silently is worse than
    refusing:

    1. AUTHORITATIVE: if the zip contains a `lods.ini` (a real, standard
       Assetto Corsa file — confirmed via AC's own published modding
       pipeline docs — found at any depth, commonly under a `data/`
       folder), parse its `[LOD_0]` section's `FILE=` value. This is the
       mod author's own explicit statement of which file is the highest
       detail model, used by the game itself for the same purpose — by
       far the most trustworthy signal when present, and used whenever it
       is, even if a scoring heuristic might have guessed differently.
    2. FALLBACK: if no usable lods.ini is found, score every supported
       model file in the archive via _score_candidate_for_main_mesh():
       filenames matching a known "this is a lower LOD" pattern (AC's own
       _B/_C/_D suffix convention, generic _LOD1/_LOD2/etc, or keywords
       like "collider"/"low_poly"/"proxy") are penalized heavily; among
       whatever remains, the largest file wins, since a full-detail mesh
       is, by definition, the most complex version of the same car. If
       every candidate has a demotion signal (e.g. a zip containing ONLY
       carname_B.kn5 with no LOD_0 file at all), still pick the
       least-penalized one rather than refusing outright — a worse-than-
       ideal mesh that still loads is more useful than a hard failure,
       but the caller is told via a returned warning so this isn't silent.

    Returns (member_name, warning_or_None).
    """
    import zipfile
    import configparser

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]

        # --- Tier 1: lods.ini, if present -----------------------------
        lods_ini_candidates = [n for n in names if os.path.basename(n).lower() == "lods.ini"]
        for ini_name in lods_ini_candidates:
            try:
                raw = zf.read(ini_name).decode("utf-8", errors="replace")
                parser = configparser.ConfigParser(strict=False)
                parser.read_string(raw)
                if "LOD_0" in parser and "FILE" in parser["LOD_0"]:
                    lod0_file = parser["LOD_0"]["FILE"].strip()
                    # The FILE= value is just a filename, e.g. "mycar.kn5" —
                    # find the matching archive member regardless of which
                    # folder it's actually nested in, since lods.ini itself
                    # doesn't store a full path.
                    matches = [n for n in names if os.path.basename(n).lower() == lod0_file.lower()]
                    if matches:
                        return matches[0], None
                    # lods.ini named a file that isn't actually in this zip
                    # (a real, observed failure mode per AC modding forums —
                    # incomplete/corrupted mod packages). Fall through to
                    # the heuristic instead of trusting a dangling reference.
            except Exception:
                pass  # malformed ini; fall through to the heuristic below

        # --- Tier 2: scored filename + size heuristic -----------------
        candidates = [n for n in names if os.path.splitext(n)[1].lower() in SUPPORTED_EXTENSIONS]
        if not candidates:
            raise ModelLoadError(
                "No supported 3D model file found inside this zip. "
                f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        infos = {info.filename: info for info in zf.infolist()}
        scored = [
            (name, _score_candidate_for_main_mesh(name, infos[name].file_size))
            for name in candidates
        ]
        scored.sort(key=lambda pair: -pair[1])
        best_name, best_score = scored[0]

        warning = None
        if best_score < 0:
            # Every candidate looked like a lower-detail/non-main file by
            # name — still proceed with the least-bad one, but say so.
            warning = (
                f"Picked \"{os.path.basename(best_name)}\" as the closest "
                "match, but every model file in this zip looked like a "
                "lower-detail or non-main version by name (no full-detail "
                "model or lods.ini found) — double check the result."
            )
        elif len(scored) > 1:
            warning = (
                f"This zip had {len(scored)} model files; picked "
                f"\"{os.path.basename(best_name)}\" as the main/highest-"
                "detail one (largest file with no \"lower detail\" naming "
                "signal). If that's wrong, extract the right file yourself "
                "and upload it directly instead."
            )
        return best_name, warning


def load_model_from_zip(zip_path):
    """Extracts the main/highest-detail model file from a zip archive (see
    find_main_model_in_zip()) into a temp directory and loads it via the
    normal load_model() path. Returns (mesh, part_face_ranges, chosen_name,
    warning_or_None) so the caller can tell the user which file was used.
    """
    import zipfile

    member_name, warning = find_main_model_in_zip(zip_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        with zipfile.ZipFile(zip_path) as zf:
            extracted_path = zf.extract(member_name, path=tmpdir)
        mesh, part_face_ranges = load_model(extracted_path)
        return mesh, part_face_ranges, os.path.basename(member_name), warning
