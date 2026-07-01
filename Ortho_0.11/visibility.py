import os
import sys
if "PYOPENGL_PLATFORM" not in os.environ and sys.platform.startswith("linux"):
    os.environ["PYOPENGL_PLATFORM"] = "egl"

import numpy as np
import pyrender
from depth_render import render_depth


def pyrender_depth_to_true_distance(D, znear, zfar):
    """pyrender applies a perspective-style hyperbolic remap to depth regardless of
    camera type, which is WRONG for orthographic cameras. This inverts that remap to
    recover the raw NDC depth d, then converts to true linear world-space distance.
    Verified empirically against known geometry (exact match, < 1e-6 error).
    D <= 0 means background (nothing rendered) -> returns np.inf.
    """
    D = np.asarray(D, dtype=np.float64)
    out = np.full(D.shape, np.inf)
    mask = D > 0
    Dm = D[mask]
    d = (zfar + znear - 2 * znear * zfar / Dm) / (zfar - znear)
    out[mask] = znear + (d + 1) / 2.0 * (zfar - znear)
    return out


def project_points(points, pose, xmag, ymag, resolution):
    """Project Nx3 world points into camera space.
    Returns (px, py, cam_depth) - pixel coords and TRUE world-space distance
    from camera along viewing axis (exact for orthographic camera)."""
    world_to_cam = np.linalg.inv(pose)
    pts_h = np.hstack([points, np.ones((len(points), 1))])
    cam_pts = (world_to_cam @ pts_h.T).T

    cam_depth = -cam_pts[:, 2]

    ndc_x = cam_pts[:, 0] / xmag
    ndc_y = cam_pts[:, 1] / ymag

    px = (ndc_x * 0.5 + 0.5) * resolution
    py = (1.0 - (ndc_y * 0.5 + 0.5)) * resolution

    return px, py, cam_depth


def compute_visible_edges(mesh, edges_v, eye, target, up, xmag, ymag,
                           resolution=1500, n_samples=12, znear=0.01, zfar=50.0,
                           depth_eps=0.006):
    """Render depth buffer, then vectorized-test all edges for visibility.
    Returns list of (p0,p1) world-space visible segments, plus depth/pose/cam for reuse."""
    depth_raw, pose, cam = render_depth(mesh, eye, target, up, xmag, ymag,
                                          resolution=resolution, znear=znear, zfar=zfar)
    depth_true = pyrender_depth_to_true_distance(depth_raw, znear, zfar)

    verts = mesh.vertices
    i0 = edges_v[:, 0]
    i1 = edges_v[:, 1]
    v0 = verts[i0]
    v1 = verts[i1]

    E = len(edges_v)
    ts = np.linspace(0, 1, n_samples)

    pts = v0[:, None, :] + ts[None, :, None] * (v1 - v0)[:, None, :]
    pts_flat = pts.reshape(-1, 3)

    px, py, cdepth = project_points(pts_flat, pose, xmag, ymag, resolution)
    px = px.reshape(E, n_samples)
    py = py.reshape(E, n_samples)
    cdepth = cdepth.reshape(E, n_samples)

    xi = np.round(px).astype(int)
    yi = np.round(py).astype(int)
    in_bounds = (xi >= 0) & (xi < resolution) & (yi >= 0) & (yi < resolution)

    xi_c = np.clip(xi, 0, resolution - 1)
    yi_c = np.clip(yi, 0, resolution - 1)
    buf_d_all = depth_true[yi_c, xi_c]
    buf_d = np.where(in_bounds, buf_d_all, np.inf)

    visible = in_bounds & (cdepth <= buf_d + depth_eps)

    visible_segments_3d = []
    for e in range(E):
        vis_row = visible[e]
        start = None
        for s in range(n_samples):
            if vis_row[s] and start is None:
                start = s
            elif not vis_row[s] and start is not None:
                t0, t1 = ts[start], ts[s - 1]
                p0 = v0[e] + t0 * (v1[e] - v0[e])
                p1 = v0[e] + t1 * (v1[e] - v0[e])
                visible_segments_3d.append((p0, p1))
                start = None
        if start is not None:
            t0, t1 = ts[start], ts[-1]
            p0 = v0[e] + t0 * (v1[e] - v0[e])
            p1 = v0[e] + t1 * (v1[e] - v0[e])
            visible_segments_3d.append((p0, p1))

    return visible_segments_3d, depth_true, pose, cam
