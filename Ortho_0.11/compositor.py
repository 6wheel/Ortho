"""
compositor.py — Composes rendered view data (from renderer.py) into a single
output image, using pixel-exact bounding-box layout (NOT matplotlib GridSpec,
which fights aspect-equal scaling across panels of different aspect ratios —
confirmed during earlier development).
"""

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors
from matplotlib.collections import LineCollection

from visibility import project_points
from renderer import rib_cut_fractions


def _view_used_bbox(result, pad_frac=0.05):
    xs, ys = [], []
    for c in result["outer_contours"]:
        xs.extend(c[:, 1]); ys.extend(c[:, 0])
    if result.get("ao_raster") is not None:
        _, alpha = result["ao_raster"]
        rows = np.any(alpha, axis=1)
        cols = np.any(alpha, axis=0)
        if rows.any():
            y_idx = np.where(rows)[0]
            x_idx = np.where(cols)[0]
            ys.extend([y_idx.min(), y_idx.max()])
            xs.extend([x_idx.min(), x_idx.max()])
    pose, xmag, ymag, resolution = result["pose"], result["xmag"], result["ymag"], result["resolution"]
    for p0, p1 in result["edge_segments"]:
        pts = np.array([p0, p1])
        px, py, _ = project_points(pts, pose, xmag, ymag, resolution)
        xs.extend(px); ys.extend(py)
    if not xs:
        return (0, resolution, 0, resolution)
    xs = np.array(xs); ys = np.array(ys)
    w = xs.max() - xs.min(); h = ys.max() - ys.min()
    pad = pad_frac * max(w, h) if max(w, h) > 0 else 1
    return xs.min() - pad, xs.max() + pad, ys.min() - pad, ys.max() + pad


def _rib_used_bbox(rib_segments, ppm, pad_frac=0.05):
    if not rib_segments:
        return (0, 1, 0, 1)
    xs, ys = [], []
    for p0, p1 in rib_segments:
        xs += [p0[0] * ppm, p1[0] * ppm]
        ys += [-p0[1] * ppm, -p1[1] * ppm]
    xs = np.array(xs); ys = np.array(ys)
    w = xs.max() - xs.min(); h = ys.max() - ys.min()
    pad = pad_frac * max(w, h) if max(w, h) > 0 else 1
    return xs.min() - pad, xs.max() + pad, ys.min() - pad, ys.max() + pad


def _draw_view(ax, result, bbox, line_color, bg_color="#FFFFFF",
                lw_outer=0.9, lw_edge=0.55, label=None,
                rib_marker_x_fracs=None, rib_marker_y_fracs=None):
    """Draws one view: AO shading as a raster underlay, tinted between the
    background colour and the line colour, then clean line art on top.

    label: optional view name ("FRONT", "LEFT", etc.) drawn beneath the
    view in line_color, small caps, DejaVu Sans (chosen over a system font
    like Gill Sans specifically because it ships bundled inside matplotlib
    itself — guaranteed present and rendering identically on every machine
    this app runs on, regardless of what's installed on that PC; Gill Sans
    is a commercial font not reliably present even on most Windows
    installs, and a missing-font silent fallback would make the look
    inconsistent across different users' output for no good reason).

    rib_marker_x_fracs / rib_marker_y_fracs: optional lists of fractional
    positions (0..1 across this view's own bbox) at which to draw a faint
    dashed line showing where a rib/cross-section cut is taken from.
    x_fracs draws a VERTICAL line (used on left/right, where the car's
    length runs horizontally in the image); y_fracs draws a HORIZONTAL
    line (used on top/bottom, where the car's length runs vertically in
    the image) — confirmed empirically by projecting known forward-axis
    bounds through each view's actual camera, not assumed. front/back
    views look directly ALONG the forward axis (a cut position projects
    to the same pixel regardless of where along that axis it actually
    sits), so there is no meaningful line to draw there; compose_image()
    simply never passes markers for those two views.
    """
    pose, xmag, ymag, resolution = result["pose"], result["xmag"], result["ymag"], result["resolution"]
    xmin, xmax, ymin, ymax = bbox

    if result.get("ao_raster") is not None:
        gray, alpha = result["ao_raster"]
        bg_rgb = np.array(matplotlib.colors.to_rgb(bg_color))
        line_rgb = np.array(matplotlib.colors.to_rgb(line_color))
        gray_norm = gray.astype(float) / 255.0  # 0..1, 1=fully open/bright, 0=fully occluded
        # Linear interpolation per channel: t=1 (open) -> bg_color,
        # t=0 (occluded) -> line_color. Equivalent to the old multiply
        # blend exactly when line_color is black (line_rgb=0 reduces this
        # to gray_norm * bg_rgb), so the default black-on-white look is
        # unchanged; any other line colour now tints correctly instead.
        rgb = gray_norm[:, :, None] * bg_rgb[None, None, :] + (1 - gray_norm[:, :, None]) * line_rgb[None, None, :]
        rgba = np.dstack([rgb, alpha.astype(float)])
        # Raster is in its own (resolution x resolution) pixel space, same
        # convention used elsewhere in this file: row 0 = top, col 0 = left.
        # imshow's default origin matches that, so plot directly with
        # extent mapping pixel index -> (x, y) in the same space the line
        # art below already uses (no flip needed here).
        ax.imshow(rgba, extent=(0, resolution, resolution, 0), interpolation="bilinear", zorder=0)

    if rib_marker_x_fracs or rib_marker_y_fracs:
        # Faint, thin dashed line spanning the bbox's full height (for an
        # x-fraction marker, used on left/right) or full width (for a
        # y-fraction marker, used on top/bottom) at each requested
        # position — drawn BELOW the line art (zorder=1, between the AO
        # raster at 0 and the line art at 2) so it never competes
        # visually with the actual model linework, and ABOVE the AO
        # raster so it stays visible over shaded regions too. front/back
        # views look ALONG the forward axis (confirmed empirically: a
        # rib cut's position along that axis projects to the exact same
        # pixel regardless of where along the axis it actually is), so a
        # cut position has no meaningful line to draw there at all —
        # callers simply don't pass markers for those views.
        for frac in (rib_marker_x_fracs or []):
            x = xmin + frac * (xmax - xmin)
            ax.plot([x, x], [ymin, ymax], linestyle=(0, (2, 4)), linewidth=0.5,
                    color=line_color, alpha=0.35, zorder=1, solid_capstyle="butt")
        for frac in (rib_marker_y_fracs or []):
            y = ymin + frac * (ymax - ymin)
            ax.plot([xmin, xmax], [y, y], linestyle=(0, (2, 4)), linewidth=0.5,
                    color=line_color, alpha=0.35, zorder=1, solid_capstyle="butt")

    outer_segs = [np.column_stack([c[:, 1], c[:, 0]]) for c in result["outer_contours"]]
    if outer_segs:
        ax.add_collection(LineCollection(outer_segs, colors=line_color, linewidths=lw_outer,
                                          capstyle="round", joinstyle="round", zorder=2,
                                          antialiased=True))

    edge_pts = result["edge_segments"]
    if edge_pts:
        # Project all segment endpoints in one batched call rather than per-segment.
        pts = np.array(edge_pts).reshape(-1, 3)  # (2*N, 3): p0,p1,p0,p1,...
        px, py, _ = project_points(pts, pose, xmag, ymag, resolution)
        px = px.reshape(-1, 2)
        py = py.reshape(-1, 2)
        edge_segs = np.stack([np.column_stack([px[i], py[i]]) for i in range(len(px))])
        ax.add_collection(LineCollection(edge_segs, colors=line_color, linewidths=lw_edge,
                                          capstyle="round", joinstyle="round", zorder=2,
                                          antialiased=True))

    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)
    ax.set_aspect("equal")
    ax.axis("off")

    if label:
        # Placed just below the view's own bbox, in axes-fraction y (< 0,
        # since matplotlib axes fraction coords grow upward but this view's
        # y-axis is inverted for image-pixel convention above — using
        # transform=ax.transAxes with a small negative y keeps the label
        # glued to the bottom of this specific view regardless of its
        # height, rather than fighting the inverted data coordinates).
        ax.text(0.5, -0.04, label, transform=ax.transAxes, ha="center", va="top",
                fontsize=9, family="DejaVu Sans", color=line_color, alpha=0.85,
                clip_on=False)


def _draw_rib(ax, rib_segments, ppm, bbox, line_color, lw=1.0):
    """Same batching fix as _draw_view, applied to rib/cross-section
    segments — these can also number in the thousands on fragmented
    meshes and would hit the identical per-Line2D overhead otherwise."""
    if rib_segments:
        segs = np.array([[[p0[0] * ppm, -p0[1] * ppm], [p1[0] * ppm, -p1[1] * ppm]]
                          for p0, p1 in rib_segments])
        ax.add_collection(LineCollection(segs, colors=line_color, linewidths=lw,
                                          capstyle="round", joinstyle="round"))
    xmin, xmax, ymin, ymax = bbox
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymax, ymin)
    ax.set_aspect("equal")
    ax.axis("off")


RIB_MAX_PER_ROW = 2  # user-requested cap: keeps rib rows close to a
                      # square/rectangle rather than a long thin strip,
                      # minimizing wasted canvas space.

# Per-view mapping of "forward-axis fraction" (0=rear-most point, 1=front-
# most point, matching renderer.rib_cut_fractions()) to where that shows up
# in each view's own 2D pixel space, confirmed EMPIRICALLY (not assumed) by
# projecting the mesh's known forward-axis bounds through each view's real
# camera — see the renderer/compositor work log in STATUS.md for the actual
# numbers. "x" means a vertical line at that fraction across the width;
# "y" means a horizontal line at that fraction down the height. front/back
# look directly along the forward axis, so a cut position always projects
# to the same pixel there regardless of where it actually is along that
# axis — there is no meaningful line to draw on those two views, and they
# are simply absent from this map on purpose, not by oversight.
_RIB_MARKER_AXIS = {
    "left":   ("x", False),  # frac=0 -> left edge,  frac=1 -> right edge
    "right":  ("x", True),   # frac=0 -> right edge, frac=1 -> left edge (mirrored vs "left")
    "top":    ("y", False),  # frac=0 -> top edge,   frac=1 -> bottom edge
    "bottom": ("y", True),   # frac=0 -> bottom edge, frac=1 -> top edge (mirrored vs "top")
}

HEADER_H = 90    # px, title block height
FOOTER_H = 36    # px, watermark strip height
BORDER_MARGIN = 28  # px, gap between content and the border rectangle
BORDER_LW = 1.4


def compose_image(view_results, rib_sections, rib_ppm, output_path,
                   bg_color="#FFFFFF", line_color="#000000",
                   scale_pct=100, dpi_base=250,
                   model_name=None, show_chrome=True):
    """view_results: dict of {view_name: render_view() result}, only for
    views the user actually requested.
    rib_sections: list of rib cuts, each a list of (p0,p1) segment pairs
    (as returned by renderer.render_rib_sections) — empty list if no rib
    cuts requested. Rib cuts are laid out in a grid, wrapping at
    RIB_MAX_PER_ROW per row (rather than one ever-widening row), so a
    large cut count doesn't tank per-slice resolution on the page.
    rib_ppm: pixels-per-world-unit scale factor for rib cuts (must match
    the same scale used for the orthographic views, for true relative size).
    scale_pct: output resolution scale, 25-100 (matches UI slider).
    model_name: shown in the title block ("<model_name> — Produced in
    Ortho by 6wheel"); if None, the title line is omitted entirely rather
    than showing a blank/placeholder name.
    show_chrome: master switch for the border/title/watermark/labels —
    all drawn in line_color so they always match whatever colour scheme
    is in use. Defaults on; exists as a single off-switch in case a future
    caller wants the bare image with none of this (e.g. an internal
    diagnostic render) without threading four separate booleans through.
    """
    bboxes = {}
    sizes = {}
    for name, result in view_results.items():
        bbox = _view_used_bbox(result)
        bboxes[name] = bbox
        sizes[name] = (bbox[1] - bbox[0], bbox[3] - bbox[2])

    GAP = 40
    LABEL_H = 26 if show_chrome else 0  # extra row height reserved for the view-name label

    rib_bboxes = []
    rib_sizes = []
    has_rib = bool(rib_sections)
    if has_rib:
        for segs in rib_sections:
            bbox = _rib_used_bbox(segs, rib_ppm)
            rib_bboxes.append(bbox)
            rib_sizes.append((bbox[1] - bbox[0], bbox[3] - bbox[2]))
    rib_fracs = rib_cut_fractions(len(rib_sections)) if has_rib else []

    # rows: list of dicts describing what to draw and where. Each row is
    # either a "views" row (one or more named orthographic views side by
    # side) or a "rib" row (one chunk of up to RIB_MAX_PER_ROW rib cuts).
    rows = []  # list of dicts: {kind, height, width, ...}

    has_front = "front" in view_results
    has_back = "back" in view_results
    if has_front or has_back:
        names = [n for n in ("front", "back") if n in view_results]
        w = sum(sizes[n][0] for n in names) + GAP * (len(names) - 1)
        h = max(sizes[n][1] for n in names) + LABEL_H
        rows.append({"kind": "views", "names": names, "height": h, "width": w})

    for simple in ["left", "right"]:
        if simple in view_results:
            w, h = sizes[simple]
            rows.append({"kind": "views", "names": [simple], "height": h + LABEL_H, "width": w})

    if has_rib:
        # Chunk rib cuts into groups of at most RIB_MAX_PER_ROW, each
        # chunk becoming its own row, so e.g. 7 cuts -> a row of 4 then a
        # row of 3, instead of one row of 7 squeezed into the page width.
        for start in range(0, len(rib_sections), RIB_MAX_PER_ROW):
            chunk_idx = list(range(start, min(start + RIB_MAX_PER_ROW, len(rib_sections))))
            chunk_sizes = [rib_sizes[i] for i in chunk_idx]
            row_h = max(s[1] for s in chunk_sizes) + LABEL_H
            row_w = sum(s[0] for s in chunk_sizes) + GAP * (len(chunk_sizes) - 1)
            rows.append({"kind": "rib", "indices": chunk_idx, "height": row_h, "width": row_w})

    # Top/bottom now share one row, side by side (previously two stacked
    # full-width rows) — user-requested, since stacking wasted vertical
    # canvas space when both views together are no wider than one alone.
    top_bottom_names = [n for n in ("top", "bottom") if n in view_results]
    if top_bottom_names:
        w = sum(sizes[n][0] for n in top_bottom_names) + GAP * (len(top_bottom_names) - 1)
        h = max(sizes[n][1] for n in top_bottom_names) + LABEL_H
        rows.append({"kind": "views", "names": top_bottom_names, "height": h, "width": w})

    if not rows:
        raise ValueError("No views selected to render.")

    content_w = max(r["width"] for r in rows)
    content_h = sum(r["height"] for r in rows) + GAP * (len(rows) - 1)

    # Reserve extra canvas space around the actual content for the border,
    # title block, and watermark strip, all drawn in line_color so they
    # always match whatever colour scheme is active. When show_chrome is
    # off, all four of these collapse to 0 and canvas size exactly matches
    # the previous (pre-this-feature) behaviour.
    side_margin = BORDER_MARGIN if show_chrome else 0
    header_h = HEADER_H if (show_chrome and model_name) else (BORDER_MARGIN if show_chrome else 0)
    footer_h = FOOTER_H if show_chrome else 0

    canvas_w = content_w + side_margin * 2
    canvas_h = content_h + header_h + footer_h + side_margin

    scale = max(0.1, min(1.0, scale_pct / 100.0))
    dpi = dpi_base * scale

    fig = plt.figure(figsize=(canvas_w / 100, canvas_h / 100), dpi=dpi, facecolor=bg_color)

    def add_axes_px(x0, y0_top, wpx, hpx):
        x_frac = x0 / canvas_w
        w_frac = wpx / canvas_w
        h_frac = hpx / canvas_h
        y0_bottom = canvas_h - y0_top - hpx
        y_frac = y0_bottom / canvas_h
        ax = fig.add_axes([x_frac, y_frac, w_frac, h_frac])
        ax.set_facecolor(bg_color)
        return ax

    y_cursor = header_h
    for row in rows:
        row_h, row_w = row["height"], row["width"]
        x_cursor = side_margin + (content_w - row_w) / 2
        view_row_h = row_h - LABEL_H
        if row["kind"] == "views":
            for name in row["names"]:
                w_v, h_v = sizes[name]
                ax = add_axes_px(x_cursor, y_cursor, w_v, view_row_h)
                axis_kind, mirrored = _RIB_MARKER_AXIS.get(name, (None, False))
                fracs_for_view = [(1 - f if mirrored else f) for f in rib_fracs] if axis_kind else []
                _draw_view(
                    ax, view_results[name], bboxes[name], line_color, bg_color,
                    label=(name.upper() if show_chrome else None),
                    rib_marker_x_fracs=(fracs_for_view if axis_kind == "x" else None),
                    rib_marker_y_fracs=(fracs_for_view if axis_kind == "y" else None),
                )
                x_cursor += w_v + GAP
        elif row["kind"] == "rib":
            for n, i in enumerate(row["indices"]):
                w_r, h_r = rib_sizes[i]
                ax = add_axes_px(x_cursor, y_cursor, w_r, view_row_h)
                _draw_rib(ax, rib_sections[i], rib_ppm, rib_bboxes[i], line_color)
                if show_chrome:
                    ax.text(0.5, -0.04, f"SECTION {i + 1}", transform=ax.transAxes, ha="center", va="top",
                            fontsize=9, family="DejaVu Sans", color=line_color, alpha=0.85, clip_on=False)
                x_cursor += w_r + GAP
        y_cursor += row_h + GAP

    if show_chrome:
        # Full-canvas overlay axes (0..1 in both directions, no aspect
        # lock) for the border rectangle, title block, and watermark —
        # kept as ONE separate axes on top of everything else (zorder
        # doesn't need to be fought per-element this way) rather than
        # trying to draw these inside any single view's own axes, which
        # are all individually positioned/sized and not meant to know
        # about the canvas as a whole.
        overlay = fig.add_axes([0, 0, 1, 1])
        overlay.set_xlim(0, canvas_w)
        overlay.set_ylim(canvas_h, 0)  # top-down, matching the rest of this file's pixel convention
        overlay.axis("off")
        overlay.patch.set_alpha(0)

        # Border: a rectangle inset by roughly 40% of the side margin from
        # each edge of the canvas, so it reads as a clean frame around
        # everything (title, views, watermark) rather than just around
        # the model content.
        inset = side_margin * 0.4
        overlay.add_patch(plt.Rectangle(
            (inset, inset), canvas_w - 2 * inset, canvas_h - 2 * inset,
            fill=False, edgecolor=line_color, linewidth=BORDER_LW, zorder=5,
        ))

        if model_name:
            overlay.text(canvas_w / 2, header_h * 0.40, model_name,
                         ha="center", va="center", fontsize=20, family="DejaVu Sans",
                         color=line_color, zorder=5)
            overlay.text(canvas_w / 2, header_h * 0.72, "Produced in Ortho by 6wheel",
                         ha="center", va="center", fontsize=11, family="DejaVu Sans",
                         color=line_color, alpha=0.75, zorder=5)

        overlay.text(canvas_w - inset - 8, canvas_h - inset - 8,
                     "github.com/6wheel/Ortho",
                     ha="right", va="bottom", fontsize=8, family="DejaVu Sans",
                     color=line_color, alpha=0.55, zorder=5)

    plt.savefig(output_path, dpi=dpi, facecolor=bg_color)
    plt.close(fig)
    return output_path
