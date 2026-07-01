"""
renderer.py — Consolidated rendering logic for the orthographic template app.

Takes a clean trimesh.Trimesh (as returned by model_loader.load_model /
build_filtered_mesh) and produces line-art data for any of the 6 standard
orthographic views plus the width-wise "rib" cross-section, with optional
Lambertian shading-contour detail.

This module is the single place that knows about: depth-buffer hidden-line
removal, the pyrender orthographic-depth bug fix, vertex welding, feature-
edge extraction, silhouette contours, and shading contours. Everything here
was developed and verified against real files earlier in the project — see
STATUS.md for the history. This file consolidates that into clean,
reusable functions instead of the copy-pasted scratch-script style used
during development.
"""

import os
import sys
if "PYOPENGL_PLATFORM" not in os.environ and sys.platform.startswith("linux"):
    os.environ["PYOPENGL_PLATFORM"] = "egl"

import numpy as np
import trimesh
import pyrender
from skimage import measure
from scipy.spatial import cKDTree

from depth_render import make_camera_pose, render_depth
from visibility import compute_visible_edges, pyrender_depth_to_true_distance, project_points


AXIS_VECTORS = {
    "x": np.array([1.0, 0.0, 0.0]),
    "y": np.array([0.0, 1.0, 0.0]),
    "z": np.array([0.0, 0.0, 1.0]),
}


class AxisConfig:
    """Describes which world axis is 'up' and which is the model's
    front-back ('forward') axis, plus the sign of the forward direction.

    Defaults match every file tested so far (Y-up, Z-forward), but BeamNG
    exports in particular are not guaranteed to follow this — user-reported
    real models loading with the wrong orientation. This makes both the
    up-axis and the front sign explicit, overridable settings instead of
    values hardcoded throughout the view/cut geometry.
    """

    def __init__(self, up_axis="y", forward_axis="z", front_sign=1):
        if up_axis == forward_axis:
            raise ValueError("up_axis and forward_axis must be different.")
        self.up_axis = up_axis
        self.forward_axis = forward_axis
        self.front_sign = front_sign

    @property
    def up_vec(self):
        return AXIS_VECTORS[self.up_axis]

    @property
    def forward_vec(self):
        return AXIS_VECTORS[self.forward_axis] * self.front_sign

    @property
    def side_axis(self):
        """The third axis, perpendicular to up and forward."""
        all_axes = {"x", "y", "z"}
        return next(iter(all_axes - {self.up_axis, self.forward_axis}))

    def axis_index(self, axis_name):
        return {"x": 0, "y": 1, "z": 2}[axis_name]


# Standard view definitions, expressed relative to an AxisConfig rather than
# a hardcoded Y-up/Z-forward assumption. (Earlier version of this function
# hardcoded up=[0,1,0] and Z as forward everywhere -- fine for every file
# tested so far, but a real user-reported BeamNG model loaded with the
# wrong orientation, since BeamNG exports don't guarantee this convention.
# AxisConfig makes both overridable from the UI instead of only being
# guessable from part names.)
def _view_geometry(view_name, center, dist, axis_cfg):
    up_vec = axis_cfg.up_vec
    fwd_vec = axis_cfg.forward_vec
    side_idx = axis_cfg.axis_index(axis_cfg.side_axis)
    side_vec = np.zeros(3)
    side_vec[side_idx] = 1.0

    views = {
        # Camera must sit on the SAME side as the front and look back
        # toward the model (sign convention verified during development —
        # see git history / earlier STATUS.md notes on the front/back
        # camera-placement bug).
        "front":  (center - fwd_vec * dist, up_vec),
        "back":   (center + fwd_vec * dist, up_vec),
        "left":   (center - side_vec * dist, up_vec),
        "right":  (center + side_vec * dist, up_vec),
        "top":    (center + up_vec * dist, -fwd_vec),
        "bottom": (center - up_vec * dist, fwd_vec),
    }
    if view_name not in views:
        raise ValueError(f"Unknown view '{view_name}'. Valid: {list(views.keys())}")
    return views[view_name]


def detect_front_axis(mesh, hint_names=None, forward_axis="z"):
    """Best-effort guess at which direction along forward_axis is 'front'.

    Heuristic: look for parts whose names suggest front/rear (e.g. contain
    'front'/'bump' vs 'rear'/'back'/'tail') and compare their average
    position along forward_axis; whichever side the 'front'-ish parts
    cluster on is +front. Falls back to +1 (arbitrary) if no naming signal
    is available — the UI should let the user flip this with one click
    rather than trust it blindly, since this is a genuine per-model
    judgment call (confirmed: Probox front=-Z, Holden VY front=+Z, no
    universal convention exists; some BeamNG models give no usable naming
    signal at all and need the manual flip).

    hint_names: optional dict of {part_name: (face_start, face_end)} so this
    can use real part names; if None, returns +1 with no attempt to guess.
    """
    if not hint_names:
        return 1

    axis_i = {"x": 0, "y": 1, "z": 2}[forward_axis]
    front_pos = []
    back_pos = []
    verts = mesh.vertices
    n_faces = len(mesh.faces)
    for name, (start, end) in hint_names.items():
        nl = name.lower()
        is_front = any(k in nl for k in ("front", "bump_f", "bumper_f", "fbump", "hood", "bonnet"))
        is_back = any(k in nl for k in ("rear", "back", "bump_r", "bumper_r", "rbump", "trunk", "tail"))
        if not (is_front or is_back):
            continue
        # Defensive bounds check: hint_names' offsets only make sense against
        # the SAME mesh they were computed from. If a caller accidentally
        # passes a filtered/different mesh (a real bug hit during
        # development: part_face_ranges from the unfiltered mesh was used
        # to index into a filtered one, which has fewer faces after
        # exclusions and threw IndexError), skip out-of-range entries
        # instead of crashing -- correctness here is best-effort heuristic
        # anyway, a partial guess is better than a hard crash.
        if start < 0 or end > n_faces or start >= end:
            continue
        face_idx = np.arange(start, end)
        vert_idx = np.unique(mesh.faces[face_idx].flatten())
        avg_pos = verts[vert_idx][:, axis_i].mean()
        (front_pos if is_front else back_pos).append(avg_pos)

    if not front_pos or not back_pos:
        return 1
    return 1 if np.mean(front_pos) < np.mean(back_pos) else -1


def get_model_scale(mesh, margin=1.10):
    """Returns (center, half_span, dist) for consistent cross-view framing.
    half_span is derived from the SINGLE LARGEST dimension across the whole
    model and used identically for every view's camera — confirmed essential
    for true relative scale between panels (fitting each view independently
    was an early mistake during development that broke this)."""
    bmin, bmax = mesh.bounds
    center = (bmin + bmax) / 2.0
    extent = bmax - bmin
    max_extent = max(extent)
    half_span = (max_extent / 2.0) * margin
    dist = max_extent * 3
    return center, half_span, dist


def rotate_mesh_around_up_axis(mesh, axis_cfg, degrees):
    """Rotates a COPY of mesh by `degrees` around the up axis (in-place
    around the mesh's own centroid, so the model doesn't drift off-center).

    Added for a real gap: AxisConfig's up_axis/forward_axis/front_sign
    settings can only choose between camera directions that are aligned to
    the world's X/Y/Z axes. A model that's genuinely Y-up (so up_axis="y"
    is correct) but whose front faces some OTHER direction within the
    XZ plane — not aligned to +-X or +-Z at all, e.g. rotated 35 degrees,
    or even a clean 90 degrees in a way that front_flip's sign-only flip
    can't fix — has no axis/sign combination that corrects it; every
    camera-facing direction in render_view() is locked to a world axis.
    User-reported real case: a model needed a 90-degree turn to align,
    previously requiring opening it in Blender, rotating, and re-exporting
    just to use this app — which defeats the point of a quick reference
    tool. This makes that an in-app slider instead.

    degrees=0 is a no-op (returns an unrotated copy, not a no-op skip,
    so callers can always treat the return value uniformly).
    """
    rotated = mesh.copy()
    if degrees == 0:
        return rotated
    center = rotated.vertices.mean(axis=0)
    angle_rad = np.radians(degrees)
    rotation_matrix = trimesh.transformations.rotation_matrix(
        angle_rad, axis_cfg.up_vec, point=center
    )
    rotated.apply_transform(rotation_matrix)
    return rotated


def render_view(mesh, view_name, center, half_span, dist, axis_cfg,
                 resolution=1800, n_samples=30, depth_eps=0.018,
                 crease_angle_deg=25.0, ao_mesh=None, ao_levels=9,
                 min_contour_area=60):
    """Renders one orthographic view of mesh, returning a dict with:
        outer_contours: list of Nx2 pixel-space silhouette contours
        ao_raster: (gray uint8 array, alpha bool mask) for AO shading as a
            multiply-blended raster underlay, or None unless ao_mesh given
        edge_segments: list of (p0_world, p1_world) visible feature/boundary edges
        pose, xmag, ymag, resolution: camera params needed to project edge_segments to pixels

    axis_cfg: an AxisConfig describing up/forward axes and front sign.
    ao_mesh: optional, a mesh with AO baked in as vertex colors (see
    compute_ambient_occlusion() below). Computed ONCE per render request
    and passed into every view's render_view() call, since AO is a property
    of the mesh's geometry, not of any particular camera angle — recomputing
    it per-view would be needlessly slow for an identical result.
    """
    znear, zfar = 0.01, dist * 2.2
    eye, up = _view_geometry(view_name, center, dist, axis_cfg)

    # Feature edges (dihedral angle) + true mesh boundaries. On fragmented
    # meshes this may find almost nothing — that's expected and handled
    # gracefully (silhouette contours still carry the result), not an error.
    mesh_welded = mesh.copy()
    mesh_welded.merge_vertices(merge_tex=False, merge_norm=False)
    try:
        angles = mesh_welded.face_adjacency_angles
        edges_adj = mesh_welded.face_adjacency_edges
        feature_edges = edges_adj[angles > np.radians(crease_angle_deg)]
        boundary_edges = mesh_welded.edges[
            trimesh.grouping.group_rows(mesh_welded.edges_sorted, require_count=1)
        ]
        edges_v = np.vstack([feature_edges, boundary_edges]) if len(feature_edges) or len(boundary_edges) else np.empty((0, 2), dtype=int)
    except Exception:
        edges_v = np.empty((0, 2), dtype=int)

    if len(edges_v) > 0:
        segs, depth_true, pose, cam = compute_visible_edges(
            mesh_welded, edges_v, eye, center, up, half_span, half_span,
            resolution=resolution, n_samples=n_samples, znear=znear, zfar=zfar, depth_eps=depth_eps
        )
    else:
        # Still need a depth render for the silhouette even with no edges to test.
        depth_raw, pose, cam = render_depth(mesh_welded, eye, center, up, half_span, half_span,
                                              resolution=resolution, znear=znear, zfar=zfar)
        depth_true = pyrender_depth_to_true_distance(depth_raw, znear, zfar)
        segs = []

    mask = (depth_true < np.inf).astype(float)
    outer_contours = [c for c in measure.find_contours(mask, 0.5) if len(c) > 25]

    ao_raster = None  # (gray uint8 array, alpha bool mask) or None if AO not requested
    if ao_mesh is not None:
        ao_raster = _render_ao_raster(ao_mesh, eye, center, up, half_span, resolution, znear, zfar)

    return {
        "outer_contours": outer_contours,
        "ao_raster": ao_raster,
        "edge_segments": segs,
        "pose": pose, "xmag": half_span, "ymag": half_span, "resolution": resolution,
    }


class AOPerformanceError(Exception):
    """Raised when AO is requested but the fast (embree) ray intersector
    isn't actually active. Without it, trimesh silently falls back to a
    pure-Python ray intersector that is 100-1000x slower (confirmed via
    direct timing during development) — running AO on that path is the
    exact problem already hit once (a ~5 minute render that still produced
    a poor-quality, banded result). Failing loudly here, with a clear fix,
    is better than silently reproducing that experience again.
    """
    pass


def _check_embree_active(mesh):
    ray_class_name = type(mesh.ray).__name__
    if "pyembree" not in type(mesh.ray).__module__:
        raise AOPerformanceError(
            "Ambient occlusion requires the 'embreex' package for fast ray "
            "casting, but it doesn't seem to be active (using "
            f"{ray_class_name} instead). Without it, AO would take several "
            "minutes and still look poor. Run 'python -m pip install -r "
            "requirements.txt' to install it, then restart the app."
        )


def compute_ambient_occlusion(mesh, n_rays=24, progress_callback=None):
    """Computes a real geometric ambient-occlusion value per vertex by
    casting rays into the hemisphere above each vertex and checking how
    many hit nearby geometry (true occlusion — darkens creases, panel
    gaps, and recesses near OTHER geometry, unlike the old fake Lambertian
    shading this originally replaced, which only responded to surface
    angle and produced unhelpful "wiggly lines" with no real occlusion
    happening there).

    REWRITTEN after a real bug found via user testing: the first version
    of this function downsampled to ~11,000 sample points with only 4 rays
    each (a workaround for the pure-Python ray intersector being far too
    slow at full resolution). With only 4 rays, occlusion could only take
    5 distinct values (0/4 .. 4/4), which produced sharp banded contour
    rings instead of smooth shading — visually identical to a topographic
    map, exactly what the user reported ("looks nothing like AO"). Root
    cause was the quantization, not a cosmetic issue to tune around.

    FIX: install `embreex` (a fast, compiled ray-mesh intersection
    library — confirmed via direct timing test during this fix: 4000 rays
    went from 8.77s on the pure-Python fallback to 0.23s with embree, and
    trimesh automatically uses it for `mesh.ray` once installed, no other
    code changes needed for that part). This is fast enough to compute AO
    at FULL per-vertex resolution with many rays per vertex directly —
    measured: 351,543 vertices (Probox) x 32 rays = ~11.2 million rays in
    under 4 seconds. The voxel-downsampling + nearest-neighbor-propagation
    workaround from the first version is no longer needed and has been
    removed entirely, eliminating the quantization artifact at its root
    rather than increasing ray count within the old downsampled approach
    (which would still have looked banded, just with more, smaller bands).

    n_rays=24 default chosen for visibly smooth gradation (25 distinct
    levels) while staying well within a fast, comfortable time budget now
    that embree is in use.

    `embreex` MUST be installed for this to be fast — added to
    requirements.txt and the app.py dependency self-check. If it is
    somehow missing despite that, trimesh silently falls back to its
    pure-Python ray intersector, which would make this function very slow
    again (the original problem) without an explicit error — this is a
    known soft spot, not yet defended against with an explicit check (see
    STATUS.md).

    SEAM-NORMAL FIX (this round): even after the speed/quantization/raster
    fixes above, real user files (Liana, ATCC touring car) still showed a
    streaky, "smudgy" look on otherwise flat painted body panels. Measured
    directly on the Liana's front-left door, restricted to a region with
    no real geometric detail (no handle, no badge, no crease): vertices
    within 2mm of each other — essentially the same physical point on a
    flat panel — had vertex normals 24-55 degrees apart, and computed AO
    brightness differing by up to 95/255. This is a genuine property of
    the source mesh, not a bug in this function: many real-time-engine
    assets (confirmed on both KN5 files tested) deliberately split/duplicate
    vertices along UV-island or smoothing-group seams with hard (non-
    averaged) normals on each side — invisible in the original game
    renderer's lighting model, but directly visible here because AO ray
    direction is sampled from the hemisphere around each vertex's own
    normal, so a hard seam in the MIDDLE of a visually flat panel produces
    a real, large discontinuity in ray sampling direction, and therefore
    in computed occlusion, right at the seam. A scan across the whole
    Liana mesh found 41,605 vertex clusters (by near-coincident position)
    with multiple disagreeing normals — this is widespread, not a one-off.

    FIX: before ray-casting, merge vertices that are near-coincident in
    POSITION (within `merge_tol`, tuned below) into clusters and assign
    each cluster a single averaged, re-normalized normal, used only for
    AO sampling — this does not alter mesh.vertices, faces, or the visual
    shading normals used anywhere else, only the hemisphere orientation
    used internally by this function. `merge_tol` was tuned empirically
    against the Liana's actual nearest-neighbour vertex distance
    distribution: over half of all vertices already have an exact (0.0
    distance) duplicate (confirming hard seam-splitting is the norm, not
    rare), pair count is nearly flat from 0.1mm-0.5mm (77.4k -> 79.8k
    pairs), then grows sharply past 1mm (79.8k -> 106k -> 209k at 1mm/2mm)
    as the radius starts catching genuinely distinct nearby detail instead
    of true seam duplicates. 0.25mm sits in the flat plateau, comfortably
    past real seam duplicates and well short of where false merges start.
    Expressed as a fraction of bbox_diag so it scales sensibly across
    differently-sized models rather than being a fixed-mm constant.

    Returns a new trimesh.Trimesh (copy of input) with AO baked in as
    grayscale vertex colors (darker = more occluded), ready to pass as
    `ao_mesh` to render_view().
    """
    m = mesh.copy()
    _check_embree_active(m)
    m.fix_normals()
    pts = m.vertices
    raw_normals = m.vertex_normals.copy()
    bbox_diag = np.linalg.norm(m.bounds[1] - m.bounds[0])
    if bbox_diag <= 0:
        bbox_diag = 1.0

    # Degenerate/zero-length vertex normals are a REAL problem on fragmented
    # meshes (confirmed: ~30% of vertices on the Holden VY KN5-derived mesh
    # have a near-zero normal, likely from degenerate slivers in that
    # mesh's heavy fragmentation — see STATUS.md). A zero normal breaks the
    # hemisphere-sampling basis construction below (cross product of a zero
    # vector is zero, causing a divide-by-zero that NaNs out and crashes
    # the ray intersector with "Coordinates must not have minimums more
    # than maximums"). Fix: replace any degenerate normal with a
    # reasonable fallback before any basis construction happens.
    normal_lengths = np.linalg.norm(raw_normals, axis=1)
    degenerate = normal_lengths < 0.5
    if degenerate.any():
        raw_normals[degenerate] = np.array([0.0, 0.0, 1.0])
        normal_lengths = np.linalg.norm(raw_normals, axis=1)
    raw_normals = raw_normals / normal_lengths[:, None]

    # Merge near-coincident vertices (true seam/UV-split duplicates) and
    # average their normals -- see SEAM-NORMAL FIX docstring above.
    merge_tol = bbox_diag * 0.00005  # ~0.25mm on a ~5m-bbox-diagonal car
    tree = cKDTree(pts)
    pairs = tree.query_pairs(r=merge_tol)
    n_verts = len(pts)
    if pairs:
        parent = np.arange(n_verts)

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path halving
                x = parent[x]
            return x

        for i, j in pairs:
            ri, rj = _find(i), _find(j)
            if ri != rj:
                parent[ri] = rj
        roots = np.array([_find(i) for i in range(n_verts)])
        unique_roots, inverse = np.unique(roots, return_inverse=True)
        sums = np.zeros((len(unique_roots), 3))
        np.add.at(sums, inverse, raw_normals)
        counts = np.bincount(inverse, minlength=len(unique_roots)).astype(float)
        avg = sums / counts[:, None]
        avg_lens = np.maximum(np.linalg.norm(avg, axis=1, keepdims=True), 1e-8)
        avg /= avg_lens
        normals = avg[inverse]
    else:
        normals = raw_normals

    rng = np.random.default_rng(0)  # fixed seed: deterministic output for the same mesh/settings
    occlusion = np.zeros(n_verts)

    # Chunking is still used even with embree's speed, partly to bound peak
    # memory for very large meshes and partly to support progress reporting.
    # embree comfortably handles chunks far larger than the pure-Python
    # intersector ever could (tested up to ~11M rays in one logical pass
    # via several chunks in ~4s total) so this can be generous.
    POINTS_PER_CHUNK = 20000
    offset = bbox_diag * 0.0005  # nudge ray origins off the surface to avoid self-intersection

    for i in range(0, n_verts, POINTS_PER_CHUNK):
        chunk_pts = pts[i:i + POINTS_PER_CHUNK]
        chunk_normals = normals[i:i + POINTS_PER_CHUNK]
        n_chunk = len(chunk_pts)

        # Cosine-weighted hemisphere sampling around each point's normal.
        u1 = rng.random((n_chunk, n_rays))
        u2 = rng.random((n_chunk, n_rays))
        r = np.sqrt(u1)
        theta = 2 * np.pi * u2
        lx = r * np.cos(theta)
        ly = r * np.sin(theta)
        lz = np.sqrt(np.maximum(0, 1 - u1))

        # Branchless orthonormal basis (Duff et al., "Building an
        # Orthonormal Basis, Revisited") -- varies continuously with the
        # normal, unlike the old fixed-arbitrary-axis + cross-product
        # method, which picked between two hardcoded axes based on a hard
        # threshold on abs(normal.x) < 0.9. That threshold caused the
        # tangent frame (and therefore the actual ray directions) to flip
        # discontinuously wherever a surface's normal.x hovered near 0.9 --
        # measured: ~11% of all Liana vertices sit within +/-0.05 of that
        # exact threshold, and the median |normal.x| on side-facing panels
        # is 0.87, i.e. almost exactly on top of the flip boundary. Found
        # while investigating the door-panel streaking; turned out not to
        # be the dominant cause (the seam-normal issue above is), but is a
        # genuine correctness fix worth keeping regardless.
        nx, ny, nz = chunk_normals[:, 0], chunk_normals[:, 1], chunk_normals[:, 2]
        sign = np.where(nz >= 0, 1.0, -1.0)
        a = -1.0 / (sign + nz)
        b = nx * ny * a
        tangent = np.stack([1.0 + sign * nx * nx * a, sign * b, -sign * nx], axis=1)
        bitangent = np.stack([b, sign + ny * ny * a, -ny], axis=1)

        dirs = (lx[:, :, None] * tangent[:, None, :]
                + ly[:, :, None] * bitangent[:, None, :]
                + lz[:, :, None] * chunk_normals[:, None, :])

        origins_flat = np.repeat(chunk_pts, n_rays, axis=0) + np.repeat(chunk_normals, n_rays, axis=0) * offset
        dirs_flat = dirs.reshape(-1, 3)

        hits = m.ray.intersects_any(origins_flat, dirs_flat)
        hits_per_point = hits.reshape(n_chunk, n_rays).sum(axis=1)
        occlusion[i:i + n_chunk] = hits_per_point / n_rays

        if progress_callback:
            progress_callback(min(i + n_chunk, n_verts), n_verts)

    # Convert occlusion (0=fully open, 1=fully occluded) to a brightness value,
    # same convention the old shading used (darker = more enclosed), with a
    # floor so nothing goes pure black.
    brightness = np.clip(1.0 - occlusion, 0.15, 1.0)
    colors = np.stack([brightness, brightness, brightness, np.ones_like(brightness)], axis=1)
    m.visual = trimesh.visual.ColorVisuals(m, vertex_colors=(colors * 255).astype(np.uint8))
    return m


def _render_ao_raster(ao_mesh, eye, center, up, half_span, resolution, znear, zfar):
    """Renders the AO-baked mesh and returns the raw grayscale brightness
    image (+ alpha mask), for use as a multiply-blended raster underlay in
    the final composite — NOT contour lines.

    REPLACES the previous contour-line approach entirely. User feedback,
    confirmed by inspecting the actual delivered output: the contour-line
    version drew ONLY the boundaries between AO brightness levels (literal
    iso-lines, like a topographic map), with the shaded regions between
    those lines left blank. That was never going to read as a gradient no
    matter how many ray/level/material parameters got tuned, because the
    actual shaded pixel data was being discarded after extraction — only
    the contour boundaries were ever passed to the compositor. This was a
    pipeline design gap (no raster ever reached compose_image), not a
    rendering-quality bug, and tuning AO inputs further could never have
    fixed it.

    The PBR-vs-FLAT render fix from the previous round still applies and
    is kept here (FLAT bypasses pyrender's lighting pipeline so the baked
    vertex-color AO values pass straight through undistorted).
    """
    scene = pyrender.Scene(bg_color=[0, 0, 0, 0], ambient_light=[1, 1, 1])
    scene.add(pyrender.Mesh.from_trimesh(ao_mesh, smooth=True))
    pose = make_camera_pose(eye, center, up)
    cam = pyrender.OrthographicCamera(xmag=half_span, ymag=half_span, znear=znear, zfar=zfar)
    scene.add_node(pyrender.Node(camera=cam, matrix=pose))
    r = pyrender.OffscreenRenderer(resolution, resolution)
    color, depth = r.render(scene, flags=pyrender.RenderFlags.FLAT)
    r.delete()

    gray = color[:, :, 0].astype(np.uint8)  # 0=fully occluded .. 255=fully open
    alpha = (depth > 0)
    return gray, alpha


def _render_ao_contours(ao_mesh, eye, center, up, half_span, resolution, znear, zfar,
                         ao_levels, min_contour_area):
    """Renders the AO-baked mesh and extracts iso-brightness contour lines.

    NOTE: superseded by _render_ao_raster() above for the main AO-shading
    path, kept here only in case contour-style AO is wanted again later as
    an alternative rendering mode. Not currently called by render_view().
    """


    gray = color[:, :, 0].astype(float) / 255.0
    mask = depth > 0
    if not mask.any():
        return []
    finite = gray[mask]
    levels = np.linspace(finite.min(), finite.max(), ao_levels)[1:-1]
    filled = np.where(mask, gray, -1)

    contours = []
    for lev in levels:
        for c in measure.find_contours(filled, lev):
            if len(c) < 5:
                continue
            w_c = c[:, 1].max() - c[:, 1].min()
            h_c = c[:, 0].max() - c[:, 0].min()
            if w_c * h_c > min_contour_area or max(w_c, h_c) > 40:
                contours.append(c)
    return contours


def rib_cut_fractions(n_cuts):
    """Returns the list of forward-axis fractions (0..1) at which
    render_rib_sections() above places its cuts, WITHOUT actually running
    the (relatively expensive) mesh-plane intersection — used by the
    compositor to draw a faint position-indicator line on the other views
    showing where each cut is taken from, without needing the real mesh
    section geometry for that purpose. Kept as a separate function rather
    than having render_rib_sections() also return fractions, so a caller
    that only needs the fractions (no rendering yet) doesn't have to wait
    on the real section computation. Logic must stay IDENTICAL to the
    fraction calculation inside render_rib_sections() above — same
    formula, both reference n_cuts the same way — since these two
    functions describing the same cuts diverging would silently draw
    indicator lines in the wrong place.
    """
    if n_cuts < 1:
        raise ValueError("n_cuts must be >= 1 (1 = midpoint cut, matching the rib section's prior behavior).")
    return [k / (n_cuts + 1) for k in range(1, n_cuts + 1)]


def render_rib_sections(mesh, axis_cfg, n_cuts=1):
    """Width-wise cross-section(s) perpendicular to the model's forward
    axis. True geometric mesh-plane intersection (trimesh's mesh.section),
    independent of mesh topology quality.

    n_cuts divides the model's extent along the forward axis into
    (n_cuts + 1) EQUAL segments, with a cut plane at each internal
    boundary (exact spec, confirmed with user — not approximate):
        n_cuts=1 (minimum): one cut, at exactly the midpoint (1/2).
        n_cuts=2: cuts at 1/3 and 2/3.
        n_cuts=3: cuts at 1/4, 1/2, 3/4.
        general: cut k (1-indexed) sits at fraction k/(n_cuts+1) of the
        model's extent along the forward axis, measured from the minimum
        to the maximum extent on that axis (NOT from front_sign direction
        specifically — the fractions are purely spatial, front_sign only
        matters for labeling/ordering if ever needed, not for cut position).

    Returns a list of length n_cuts, each element a list of
    (p0_world, p1_world) segment pairs for that cut (some cuts may return
    an empty list if the plane happens to miss all geometry, e.g. a
    degenerate cut at the very tip — not expected with this spec since
    n_cuts=1 is forced to the midpoint and others are always interior
    fractions, but handled gracefully regardless).
    """
    if n_cuts < 1:
        raise ValueError("n_cuts must be >= 1 (1 = midpoint cut, matching the rib section's prior behavior).")

    fwd_idx = axis_cfg.axis_index(axis_cfg.forward_axis)
    bmin, bmax = mesh.bounds
    lo, hi = bmin[fwd_idx], bmax[fwd_idx]

    fwd_vec = np.zeros(3)
    fwd_vec[fwd_idx] = 1.0

    all_segments = []
    for k in range(1, n_cuts + 1):
        frac = k / (n_cuts + 1)
        pos = lo + frac * (hi - lo)
        plane_origin = mesh.bounds.mean(axis=0)  # any point; only the forward-axis component matters
        plane_origin[fwd_idx] = pos

        section = mesh.section(plane_origin=plane_origin, plane_normal=fwd_vec)
        segments = []
        if section is not None:
            for entity in section.entities:
                pts = section.vertices[entity.points]
                for i in range(len(pts) - 1):
                    segments.append((pts[i], pts[i + 1]))
        all_segments.append(segments)

    return all_segments
