import os
import os
import sys
if "PYOPENGL_PLATFORM" not in os.environ and sys.platform.startswith("linux"):
    # EGL only verified working on Linux during development. On Windows/Mac,
    # leave PYOPENGL_PLATFORM unset so pyrender falls back to its default
    # (Pyglet-based) platform instead, which doesn't need EGL at all.
    # (Real bug hit during user testing: forcing EGL unconditionally caused
    # "Unable to load EGL library" on Windows, since EGL is a Linux/NVIDIA
    # technology not generally present on Windows. See STATUS.md.)
    os.environ["PYOPENGL_PLATFORM"] = "egl"

import pickle
import numpy as np
import trimesh
import pyrender


def make_camera_pose(eye, target, up):
    """Build a 4x4 camera-to-world matrix for pyrender (camera looks down -Z in its local frame)."""
    eye = np.array(eye, dtype=float)
    target = np.array(target, dtype=float)
    up = np.array(up, dtype=float)

    forward = target - eye
    forward = forward / np.linalg.norm(forward)
    # camera looks down -Z, so cam_z (world) = -forward
    cam_z = -forward
    cam_x = np.cross(up, cam_z)
    cam_x = cam_x / np.linalg.norm(cam_x)
    cam_y = np.cross(cam_z, cam_x)

    pose = np.eye(4)
    pose[:3, 0] = cam_x
    pose[:3, 1] = cam_y
    pose[:3, 2] = cam_z
    pose[:3, 3] = eye
    return pose


def render_depth(mesh, eye, target, up, xmag, ymag, resolution=2000, znear=0.01, zfar=50.0):
    scene = pyrender.Scene(bg_color=[0, 0, 0, 0], ambient_light=[1.0, 1.0, 1.0])
    pmesh = pyrender.Mesh.from_trimesh(mesh, smooth=False)
    scene.add(pmesh)

    cam = pyrender.OrthographicCamera(xmag=xmag, ymag=ymag, znear=znear, zfar=zfar)
    pose = make_camera_pose(eye, target, up)
    scene.add_node(pyrender.Node(camera=cam, matrix=pose))

    r = pyrender.OffscreenRenderer(resolution, resolution)
    depth = r.render(scene, flags=pyrender.RenderFlags.DEPTH_ONLY)
    r.delete()

    return depth, pose, cam


if __name__ == '__main__':
    mesh = trimesh.load('filtered_probox.obj', process=False)
    mesh.remove_unreferenced_vertices()
    mesh.merge_vertices(merge_tex=False, merge_norm=False)

    bmin, bmax = mesh.bounds
    center = (bmin + bmax) / 2.0
    extent = bmax - bmin
    print("center:", center, "extent:", extent)

    # quick sanity test: front view
    eye = center + np.array([0, 0, -10])  # in front of car, looking toward +Z (car front is -Z)
    depth, pose, cam = render_depth(mesh, eye, center, up=[0,1,0], xmag=extent[0]*0.7, ymag=extent[1]*0.7, resolution=800)
    print("depth nonzero px:", (depth>0).sum(), "/", depth.size)
    np.save('test_depth_front.npy', depth)
