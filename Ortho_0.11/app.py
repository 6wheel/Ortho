"""
app.py — Flask web application for the orthographic template generator.

Run with: python app.py
Then open http://127.0.0.1:5000 in a browser (this also now opens
automatically — see bottom of this file).

This is the "drop a file in, get a picture out" tool described in
STATUS.md. See that file for full project context and decisions.
"""

HOST = "127.0.0.1"
PORT = 5000

import sys
import webbrowser


def _check_dependencies():
    """Checks all required libraries are installed BEFORE attempting the
    real imports below, so a missing one produces a short, plain-English
    message instead of a raw Python traceback (which would be unreadable
    to someone without Python experience)."""
    required = {
        "flask": "flask",
        "trimesh": "trimesh",
        "pyrender": "pyrender",
        "OpenGL": "PyOpenGL",
        "skimage": "scikit-image",
        "numpy": "numpy",
        "scipy": "scipy",
        "collada": "pycollada",
        "matplotlib": "matplotlib",
        "rtree": "rtree",
    }
    missing = []
    for module_name, pip_name in required.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        print("=" * 70)
        print("SETUP NOT FINISHED — some required libraries are missing.")
        print("=" * 70)
        print()
        print("Missing: " + ", ".join(missing))
        print()
        print("To fix this, open a command window in this folder and run:")
        print()
        print("    python -m pip install -r requirements.txt")
        print()
        print("Then try running 'python app.py' again.")
        print("See README.md if you run into trouble.")
        print("=" * 70)
        sys.exit(1)


_check_dependencies()

import os
import json
import uuid

from flask import Flask, request, render_template, jsonify, send_from_directory, Response, stream_with_context

from model_loader import load_model, build_filtered_mesh, ModelLoadError, SUPPORTED_EXTENSIONS, load_model_from_zip
from renderer import get_model_scale, detect_front_axis, render_view, render_rib_sections, AxisConfig, compute_ambient_occlusion, rotate_mesh_around_up_axis
from compositor import compose_image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
SELECTIONS_DIR = os.path.join(BASE_DIR, "selections")
GLOBAL_PREFS_PATH = os.path.join(BASE_DIR, "global_prefs.json")
for d in (UPLOAD_DIR, OUTPUT_DIR, SELECTIONS_DIR):
    os.makedirs(d, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500MB, generous for big meshes

# In-memory cache of loaded models for this session, keyed by a session id
# we hand back to the browser. Avoids re-parsing the file on every
# checkbox change / regenerate click. NOTE: single-process, single-user
# tool (runs on the user's own machine) -- this is fine; not built for
# concurrent multi-user use.
_MODEL_CACHE = {}


def _selection_path(filename):
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    return os.path.join(SELECTIONS_DIR, safe_name + ".json")


# Global preferences: color, views, scale, AO on/off, orientation defaults.
# Unlike per-file part selections (which only make sense per-model), these
# are user requested to be GLOBAL — one shared set of preferences that
# applies no matter what file is loaded next, not remembered per-filename.
DEFAULT_GLOBAL_PREFS = {
    "views": ["front", "back", "left", "top"],
    "include_rib": False,
    "rib_cuts": 1,
    "bg_color": "#FFFFFF",
    "line_color": "#000000",
    "scale_pct": 100,
    "ao": False,
    "up_axis": "y",
    "front_flip": False,
}


def _load_global_prefs():
    if os.path.exists(GLOBAL_PREFS_PATH):
        try:
            with open(GLOBAL_PREFS_PATH) as fh:
                saved = json.load(fh)
            prefs = dict(DEFAULT_GLOBAL_PREFS)
            prefs.update(saved)
            return prefs
        except Exception:
            pass
    return dict(DEFAULT_GLOBAL_PREFS)


def _save_global_prefs(prefs):
    try:
        with open(GLOBAL_PREFS_PATH, "w") as fh:
            json.dump(prefs, fh)
    except Exception:
        pass  # non-fatal; persistence is a convenience, not a hard requirement


@app.route("/preferences", methods=["GET"])
def get_preferences():
    return jsonify(_load_global_prefs())


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "file" not in request.files:
        return jsonify({"error": "No file provided."}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "No file selected."}), 400

    ext = os.path.splitext(f.filename)[1].lower()
    is_zip = ext == ".zip"
    if not is_zip and ext not in SUPPORTED_EXTENSIONS:
        return jsonify({
            "error": f"Unsupported file type '{ext}'. Supported: "
                     f"{', '.join(sorted(SUPPORTED_EXTENSIONS))}, or a .zip containing one of those"
        }), 400

    save_path = os.path.join(UPLOAD_DIR, f.filename)
    f.save(save_path)

    chosen_filename = f.filename
    zip_warning = None
    try:
        if is_zip:
            mesh, part_face_ranges, chosen_filename, zip_warning = load_model_from_zip(save_path)
        else:
            mesh, part_face_ranges = load_model(save_path)
    except ModelLoadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error loading file: {e}"}), 500

    session_id = str(uuid.uuid4())
    _MODEL_CACHE[session_id] = {
        "mesh": mesh,
        "part_face_ranges": part_face_ranges,
        "filename": chosen_filename,
    }

    # Load saved selection state for this filename, if any.
    sel_path = _selection_path(chosen_filename)
    saved_excluded = []
    if os.path.exists(sel_path):
        try:
            with open(sel_path) as fh:
                saved_excluded = json.load(fh).get("excluded", [])
        except Exception:
            saved_excluded = []

    part_names = sorted(part_face_ranges.keys())
    response = {
        "session_id": session_id,
        "filename": chosen_filename,
        "part_names": part_names,
        "excluded": [p for p in saved_excluded if p in part_face_ranges],
        "vertex_count": len(mesh.vertices),
        "face_count": len(mesh.faces),
    }
    if zip_warning:
        response["warning"] = zip_warning
    return jsonify(response)


@app.route("/generate", methods=["POST"])
def generate():
    """Runs the render and streams Server-Sent Events (SSE) back to the
    client with progress updates as each stage completes. This keeps all
    pyrender/OpenGL calls on the main Flask request thread, which is
    required on Windows where pyrender's default Pyglet backend is
    thread-bound (EGL is Linux-only, OSMesa requires compiling Mesa from
    source — neither is viable as a user dependency on Windows). SSE lets
    the frontend show live progress without any background threading.
    """
    data = request.get_json()
    session_id = data.get("session_id")
    if session_id not in _MODEL_CACHE:
        return jsonify({"error": "Session expired or invalid. Please re-upload the file."}), 400

    def _event(msg, progress, done=False, image_url=None, error=None):
        import json as _json
        payload = {"message": msg, "progress": progress}
        if done:
            payload["done"] = True
        if image_url:
            payload["image_url"] = image_url
        if error:
            payload["error"] = error
        return f"data: {_json.dumps(payload)}\n\n"

    def stream():
        try:
            cached = _MODEL_CACHE.get(session_id)
            if cached is None:
                yield _event("Session expired.", 0, error="Session expired or invalid.")
                return

            mesh = cached["mesh"]
            part_face_ranges = cached["part_face_ranges"]
            filename = cached["filename"]

            excluded = set(data.get("excluded", []))
            views = data.get("views", ["front", "back", "left", "top"])
            include_rib = data.get("include_rib", False)
            rib_cuts = max(1, int(data.get("rib_cuts", 1)))
            bg_color = data.get("bg_color", "#FFFFFF")
            line_color = data.get("line_color", "#000000")
            scale_pct = int(data.get("scale_pct", 100))
            ao_enabled = bool(data.get("ao", False))
            up_axis = data.get("up_axis", "y")
            requested_forward = data.get("forward_axis")
            if requested_forward:
                forward_axis = requested_forward
            elif up_axis != "z":
                forward_axis = "z"
            elif up_axis != "y":
                forward_axis = "y"
            else:
                forward_axis = "x"
            front_flip = bool(data.get("front_flip", False))
            rotation_deg = float(data.get("rotation_deg", 0) or 0)

            try:
                with open(_selection_path(filename), "w") as fh:
                    json.dump({"excluded": sorted(excluded)}, fh)
            except Exception:
                pass

            _save_global_prefs({
                "views": views, "include_rib": include_rib, "rib_cuts": rib_cuts,
                "bg_color": bg_color, "line_color": line_color, "scale_pct": scale_pct,
                "ao": ao_enabled, "up_axis": up_axis, "front_flip": front_flip,
                "rotation_deg": rotation_deg,
            })

            yield _event("Preparing model…", 0.03)

            filtered = build_filtered_mesh(mesh, part_face_ranges, excluded)
            if len(filtered.faces) == 0:
                yield _event("", 0, error="All parts excluded — nothing to render. Untick at least one part.")
                return

            auto_sign = detect_front_axis(mesh, part_face_ranges, forward_axis=forward_axis)
            front_sign = -auto_sign if front_flip else auto_sign
            axis_cfg = AxisConfig(up_axis=up_axis, forward_axis=forward_axis, front_sign=front_sign)

            if rotation_deg:
                filtered = rotate_mesh_around_up_axis(filtered, axis_cfg, rotation_deg)

            center, half_span, dist = get_model_scale(filtered)
            base_res = 1800
            render_res = max(400, int(base_res * (scale_pct / 100.0)))

            ao_mesh = None
            if ao_enabled:
                yield _event("Computing shading… 0%", 0.05)
                ao_mesh = compute_ambient_occlusion(filtered)
                yield _event("Computing shading… 100%", 0.55)

            view_results = {}
            n_views = max(len(views), 1)
            view_start = 0.55 if ao_enabled else 0.05
            for i, v in enumerate(views):
                prog = view_start + (0.85 - view_start) * (i / n_views)
                yield _event(f"Rendering {v} view ({i + 1}/{n_views})…", prog)
                view_results[v] = render_view(
                    filtered, v, center, half_span, dist, axis_cfg,
                    resolution=render_res, ao_mesh=ao_mesh,
                )

            rib_sections = []
            rib_ppm = 1.0
            if include_rib:
                yield _event("Computing cross-sections…", 0.87)
                rib_sections = render_rib_sections(filtered, axis_cfg, n_cuts=rib_cuts)
                rib_ppm = render_res / (half_span * 2)

            yield _event("Composing final image…", 0.93)
            out_name = f"{uuid.uuid4()}.png"
            out_path = os.path.join(OUTPUT_DIR, out_name)
            model_display_name = os.path.splitext(filename)[0] or None
            compose_image(
                view_results, rib_sections, rib_ppm, out_path,
                bg_color=bg_color, line_color=line_color, scale_pct=scale_pct,
                model_name=model_display_name,
            )
            yield _event("Done.", 1.0, done=True, image_url=f"/outputs/{out_name}")

        except Exception as e:
            yield _event("", 0, error=f"Render failed: {e}")

    return Response(stream_with_context(stream()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/outputs/<filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    print(f"Detected OS: {sys.platform}")
    print("Starting Orthographic Template Generator...")
    print(f"Open http://{HOST}:{PORT} in your browser.")

    # Automatically open the app in the default browser, so the person never
    # has to manually type the address.
    webbrowser.open(f"http://{HOST}:{PORT}")

    # On macOS, pyrender/pyglet's underlying window/GL machinery is known to
    # crash if touched from a non-main thread ("NSWindow drag regions should
    # only be invalidated on the Main Thread!" — a documented pyglet/macOS
    # issue, confirmed via search, not guessed). Flask's default dev server
    # handles each request on its own thread, which can trigger this on Mac
    # specifically. Disabling threading keeps every render call on the main
    # thread there. Linux/Windows are unaffected and keep threading on.
    app.run(host=HOST, port=PORT, debug=False, threaded=sys.platform != "darwin")
