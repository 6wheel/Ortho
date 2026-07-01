# Orthographic Template Generator — Build Status

## ⚡ READ THIS FIRST — current state as of this handover (2026-06-28)

**The app works and is in active real-world use by the user on their own
Windows PC.** Every core feature is built and has been confirmed working
on real files (OBJ, KN5) by the user directly, not just in the sandbox.
The detailed history below this section documents how it got here,
several real bugs found via actual user testing, and is mostly useful as
reference/forensics — you do not need to re-read all of it to continue
work. Skim it only if you need to understand WHY something is built the
way it is.

### What's done and confirmed working by the user, on their own PC
- Loading OBJ, KN5 (via our own from-scratch parser — NOT the
  MarvinSt/kn5-obj-converter, which has no license), DAE, STL, GLTF/GLB.
- Part checklist (scrollable, filterable, select/deselect-all), with
  per-file persistence of exclusions.
- All 6 standard views + a multi-cut cross-section ("rib") slicer with an
  exact user-specified fraction spec (N cuts → N+1 equal segments).
- Manual orientation overrides (up-axis dropdown, front/back flip) for
  models that load facing the wrong way (common with BeamNG mods).
- Color pickers (background/line) with two one-click presets.
- Output resolution slider.
- GLOBAL preference persistence (colors/views/scale/AO/orientation —
  explicitly global per user request, separate from the per-file part
  selections).
- Windows compatibility (fixed a real EGL-on-Windows crash — pyrender
  defaults to EGL, which doesn't generally exist on Windows; fixed to
  only force EGL on Linux).
- Mac compatibility additions merged in from a file the user supplied
  (browser auto-open, a documented pyglet/macOS main-thread fix) — **this
  one is UNVERIFIED, no Mac available to test on.**

### ✅ RESOLVED THIS SESSION — AO slowness AND wrong-looking shading on real Liana file

**The user supplied the actual Liana KN5 file this time** (79MB, 186 parts,
156k vertices) — direct reproduction was possible instead of inferring from
logs/screenshots. Two genuinely separate bugs were found and fixed. Do not
re-open either without first re-reading this section; both were verified
by direct profiling/ray-casting, not guesswork.

**Bug A — speed (~52s → ~16s on this file, confirmed via real Flask
`/upload` → `/generate` test-client calls, not just isolated functions):**
`compositor.py` was calling `ax.plot()` once per individual line segment
(outer contours, AO contours, edge segments, rib sections). On a
fragmented 186-part file this is 9,000–21,000+ separate `Line2D` objects
per view. Profiled directly with cProfile: a single view's draw step
spent 6.5 of its 10.2 seconds in matplotlib's per-artist overhead
(`Line2D.__init__`, `set_clip_path`, `draw_path`, line-limit updates) —
NOT in any geometry math. AO ray-casting itself was independently
profiled and confirmed fast (~4s) on this file the whole time; it was
never the bottleneck. **Fix:** batch each line category into one
`matplotlib.collections.LineCollection` per axis instead of looping
`ax.plot()`. Same fix applied to `_draw_view` and `_draw_rib`. Visually
verified identical output, just far fewer matplotlib artists.

**Bug B — the real "no actual shading, just noise" complaint (this was
the important one, found by ray-casting into the mesh directly to settle
where truth diverged from rendered output):**
`pyrender`'s default scene setup (`pyrender.Mesh.from_trimesh(ao_mesh,
smooth=True)` with no explicit material, rendered via the normal PBR
pipeline) does NOT pass baked vertex colors straight through. Confirmed
by casting a ray straight down onto the car roof and reading the actual
baked AO vertex color at that exact point: **255,255,255 (fully bright,
correct)**. The corresponding rendered pixel at that same point came out
as **21,21,21** — i.e. visually black. This reproduced even on the FULLY
UNFILTERED mesh with zero parts excluded — ruling out "bad geometry" as
the cause; it was a render-config bug, full stop. Tried and ruled out:
metallic/roughness factor tuning (no effect), adding a real
`DirectionalLight` (no effect). What worked: rendering with
`flags=pyrender.RenderFlags.FLAT`, which bypasses the lighting pipeline
and outputs the (still smooth-interpolated, since `smooth=True` is kept)
vertex colors directly. Confirmed via the same straight-down ray test
location: rendered pixel now matches the true baked AO value. Applied to
`_render_ao_contours` in `renderer.py` — this is the ONLY code change for
this bug, one added kwarg on the existing `r.render(scene, ...)` call.

**Secondary, smaller finding (not fixed, not asked for — left as-is per
user instruction "not bothered if there's a bit of mess on detailed parts
or internals"):** this Liana KN5, being an Assetto Corsa-style asset, has
many near-coincident overlay layers by design (separate `_weatherstrip_`,
`_black_`, `_chrome_` sub-meshes sitting directly against the visible
panel/glass edge — invisible in-game because only one layer is shown per
material, but all geometrically present for a ray-traced AO pass). This
causes some real (not noise, not a bug) extra self-occlusion right at
window/trim seams — confirmed via floor-vertex percentage dropping from
56%→8.9%→5.8% as cockpit/interior/black/weatherstrip parts were
progressively excluded by name, and via offset-epsilon sensitivity tests
that ruled out ray-offset tuning as a factor (changing it 1000x changed
the floor% by <1 point both with and without those parts excluded — not
the driver). Bug B above was confirmed to be the dominant, independent
cause of the user-visible "scribble" complaint; this secondary effect is
real but minor and confined to trim/glass-seam edges once Bug B is fixed.
No generalizable code fix exists for this part of it — which parts count
as "decoration vs. real occluder" is asset-specific naming, not something
the renderer can know automatically. If the user wants this cleaned up
further on a per-file basis, the existing part-exclusion checklist is the
right tool; a possible future feature (proposed, NOT yet built, needs
explicit go-ahead) is a fast low-res "AO occluder preview" so the user can
see which currently-included parts are contributing dark occlusion before
committing to a full-resolution render.

**Verification standard met:** tested via the real Flask `/upload` →
`/generate` path (not just isolated function calls) on the actual user
file, with before/after timing and before/after visual inspection at
multiple zoom levels (full 4-view composite, single view, flat AO-only
render with no contour lines, raw vertex-color histogram). Both fixes are
independent of each other and were each verified in isolation as well as
together.

### ✅ THIS SESSION (after the above) — AO was STILL wrong after the
speed/FLAT-render fixes, for a reason neither of the previous fixes could
touch: **AO was being delivered as iso-brightness CONTOUR LINES, never as
an actual shaded raster.** User feedback on the previous build, verbatim
in spirit: "no gradient, looks like a Lord of the Rings map." Confirmed
by reading the code, not just the image: `_render_ao_contours()` rendered
the AO-baked mesh to a grayscale image internally (correctly, post the
RenderFlags.FLAT fix), then called `skimage.measure.find_contours()` to
extract N iso-brightness boundary lines from it, and ONLY those lines —
never the underlying grayscale pixels — were passed to `compositor.py`.
The shaded regions between contour lines were always blank/white by
construction. No amount of ray count, AO level count, or render-material
tuning could ever have produced a gradient through that pipeline; this
was a structural gap (the raster never reached the compositor), not a
quality bug. Tuning parameters further would have been pointless and is
exactly the "tune a number, ship it, it breaks differently" pattern this
file warns against elsewhere — worth remembering if AO goes wrong again:
check what data-shape is actually flowing through the pipeline before
assuming it's a tuning problem.

**Fix:** replaced contour extraction with a raster pipeline end to end:
- `renderer.py`: new `_render_ao_raster()` returns `(gray uint8 array,
  alpha bool mask)` instead of a contour list. `render_view()`'s returned
  dict now has `ao_raster` instead of `ao_contours`. The old
  `_render_ao_contours()` function is left in the file, unused, in case
  contour-style AO is wanted again later as an alternate mode — it is not
  called anywhere currently.
- `compositor.py`: `_draw_view()` now does `ax.imshow()` of the AO raster
  as an underlay BEFORE drawing the outer-contour/edge-segment line art
  on top (explicit `zorder`: raster=0, lines=2). The raster is manually
  multiply-blended against the actual background colour (`rgb = gray/255
  * bg_rgb`) rather than relying on an alpha blend or a GL-style blend
  mode, because (a) Agg, matplotlib's non-interactive backend, doesn't
  support 'multiply' blend modes directly, and (b) plain alpha-blending
  would wash out dark AO areas instead of darkening through them. Manual
  multiply reproduces a Photoshop/GIMP "Multiply" layer correctly: white
  AO leaves the background untouched, dark AO darkens it proportionally,
  black linework on top is unaffected. `_view_used_bbox()` updated to
  derive the view's used-area bbox from the raster's alpha mask when an
  outer-contour-only bbox isn't available, instead of from now-removed
  `ao_contours`.
- Visually verified working (multiply blend + line art together produce
  real smooth gradient, not lines) via direct render on the Liana, left
  view — confirmed by the user as "something" / a real step forward, not
  yet given a final pass/fail on this specific render.

**Second real test file this session: a touring-car KN5
(`atcc_touring_car_ef.kn5`, 195 parts, ~256k vertices) with a baked AO
texture supplied by the user as a ground-truth reference
(`car_paint_ao_ef.dds`, 2048x2048, UV-space).** This is a much messier,
generically-named asset than the Liana (no clean `_body_` naming
convention; exterior panels are scattered among names like `Object52`,
`object-83_body_main04_SUB07`, `doorlf_m04`). Running AO on this file
fully unfiltered showed the same disease as the Liana (streaky
self-occlusion noise on visible panels, a dark smudge through the rear
door) via the same mechanism: coincident/duplicate internal geometry.
Diagnosed by ray-casting straight through the body at door height and
reading off every hit in depth order (same technique as the Liana
session) — found, in order: the real outer skin (`Object52`, confirmed
by isolating+rendering it alone: door/roof/quarter-panel silhouette,
matches the DDS reference shape closely), then almost immediately behind
it TWO more near-duplicate full-body-shaped meshes (`object-05` and
`Object11_SUB12` — confirmed by isolating+rendering each alone, both are
clearly the same door/window silhouette as Object52, just sitting a hair
behind/in front of it), then the roll cage (`cylinder` — confirmed by
isolating it alone: classic roll-cage triangulated bar shape) plus an
interior sill piece (`interior_001_sub2_002`) and the glass
(`Object28`). Excluding these five specific parts (confirmed individually
by rendering each alone before excluding, not guessed from naming alone)
visibly removed the rear-door smudge and brought the panel shading much
closer to the DDS reference, though some streaky texture remains on the
rear quarter panel and front fender, and the wheel arches render darker/
heavier than the reference. **This was NOT packaged as an automatic
exclusion rule** — the exclusion list used for this test
(`exclude_confirmed = {'object-05', 'Object11_SUB12', 'cylinder',
'interior_001_sub2_002', 'Object28'}` plus the same kind of keyword
exclusions used on the Liana) is specific to this file's part names and
was for diagnosis only, run directly in a throwaway script, NOT wired
into `app.py`. The app's existing user-facing part-exclusion checklist is
already the correct, already-working mechanism for the user to apply
exclusions like this per file; no code changes were needed or made to
that mechanism this session.

A general technique worth recording for next time AO looks wrong on a
NEW file: (1) cast a ray straight through the body at the affected area
and print every hit in depth order with its part name — this finds
coincident/duplicate layers directly and fast, without guessing from
part names; (2) for generically-named parts, isolate each candidate by
building a filtered mesh containing ONLY that one part and rendering it
alone from the relevant view — a part's true identity (visible skin vs.
interior trim vs. structural cage vs. glass) is usually obvious by eye
once isolated, even when its name gives no clue; (3) a bounding-box IoU
screen against the confirmed main body-shell part is a reasonable way to
shortlist candidates worth checking by eye on a file with many parts (used
here, found `object-05`/`Object11_SUB12` near the top as expected), but is
NOT reliable enough to exclude by IoU score alone — it also flagged
legitimate exterior parts (a fender panel, a wing) that must stay.

**Workflow change requested by the user, effective immediately: stop
asking the user to download and run the whole app to test each AO
attempt.** Going forward, render a single test view (the user specified:
Suzuki Liana, left view) directly in the sandbox and share that one image
for a quick yes/no before iterating further. Only repackage the full app
zip once the user explicitly confirms the AO render looks right and asks
for the build.

**Known friction this session, worth fixing if it recurs:** the `view`
tool (Claude's own image-preview mechanism) did not reliably display
images to the user in this conversation — the user reported "no photo"
twice in a row for images that were confirmed to exist on disk and be
valid PNGs. Workaround that worked: copy the file to
`/mnt/user-data/outputs/` and use `present_files` to give the user a real
downloadable file link, instead of relying on `view` alone. If this
happens again, switch to `present_files` immediately rather than
re-attempting `view`.

### ✅ DONE — layout changes (rib grid wrap + top/bottom side-by-side)

Implemented in `compositor.py`: rib cuts now wrap into rows (currently
`RIB_MAX_PER_ROW = 2`, user explicitly chose 2 over the initially-tried 4
to keep a tighter rectangular layout), and top/bottom share one row side
by side instead of two stacked full-width rows. Verified via direct
function calls and the real Flask `/generate` path. One real bug caught
and fixed during this: an early test render used a hand-picked
`rib_ppm=200.0` guess instead of the actual formula `app.py` uses
(`render_res / (half_span * 2)`), so the rib cuts came out a different
scale than the orthographic views above them — user caught this visually
("rib cuts are not to scale"). Fixed by using the real formula, then
INDEPENDENTLY verified correct (not just "looks plausible") by measuring
the rendered front-view car width in pixels directly and confirming it
matches `mesh_width_world * rib_ppm` to within 0.1%. Worth remembering:
when writing throwaway test scripts that bypass `app.py`, copy its actual
constants/formulas rather than guessing a plausible-looking number — a
guessed scale constant looks fine until checked against a real reference.

### ✅ DONE — AO seam-duplicate-vertex fix ("try 1" of the suggested
AO-quality improvements; the other suggestions — low-poly glass faceting,
adaptive subdivision, bilateral raster smoothing — were NOT pursued this
round, still on the table if wanted)

Root-caused the remaining door-panel "smudgy" streaking (present even
after the raster/gradient and FLAT-render fixes) to genuine duplicate
vertices in the source mesh: measured directly on the Liana, vertices
within 2mm of each other on flat painted door skin — not a crease, not a
real feature — had normals 24-55 degrees apart and computed AO brightness
differing by up to 95/255. Found 41,605 such duplicate-position vertex
clusters across the whole mesh via cKDTree. This is a property of how the
source KN5 was authored (hard-normal seams at UV-island/smoothing-group
boundaries, invisible in the original game's lighting model, directly
visible here because AO ray direction is sampled per-vertex-normal).
Fixed in `compute_ambient_occlusion()`: merge near-coincident vertices
(tolerance `bbox_diag * 0.00005`, ~0.25mm on this car — tuned by examining
the REAL nearest-neighbor distance histogram on this mesh, not guessed:
over half of all vertices already have an exact 0.0-distance duplicate,
pair count is nearly flat 0.1-0.5mm then grows sharply past 1mm as false
merges start, 0.25mm sits safely in that flat plateau) and average their
normals into one consistent direction used only for AO ray sampling
(mesh.vertices/faces/shading normals elsewhere are untouched). Also fixed
along the way: a real but secondary tangent-basis discontinuity bug
(replaced a hardcoded-arbitrary-axis construction, which flipped
discontinuously for ~11% of vertices near a hard 0.9 threshold, with the
branchless Duff et al. orthonormal basis) — kept as a correctness fix even
though it turned out not to be the dominant visible cause. Verified on
both the Liana and the ATCC touring car (second real test file, 195
parts, also showed the same disease independently), edge cases tested (a
simple box primitive with zero duplicate vertices, to exercise the
no-merges-found code path), full Flask path still green, timing impact
small (~+1-2s). NOT yet fixed: low triangle density on some parts (e.g.
the Liana's front door glass has only 161 faces) causes separate, visibly
faceted/blocky AO on those specific parts — different root cause
(triangle density, not normals), still open if wanted.

### ✅ DONE — one-click Windows launcher (`Start App.bat`)

User wants zero command-line typing for non-technical end users. Added
`Start App.bat` in the app folder: double-click it, it finds a working
Python (tries `py` then `python`, since some PCs only have one or the
other set up), checks the version is >=3.9, installs `requirements.txt`
only when needed (a marker file + `fc /b` binary-compares the last
successfully-installed requirements.txt against the current one, so
repeat launches after the first are fast — skip straight to launch
instead of re-running pip every time), then runs `app.py`, which already
opens the browser itself. Friendly plain-language messages and explicit
next-steps (not just an error code) for the two most likely failure
modes: Python missing entirely, or Python too old — both link straight to
the python.org download page and explain the "Add python.exe to PATH"
checkbox, which is the most common real-world mistake. Explicitly `cd
/d`'s to its own folder first (`%~dp0`) since that is NOT guaranteed by
Windows when a .bat is launched via a shortcut rather than a direct
double-click, and is the single most common cause of confusing "file not
found" failures in launcher scripts generally.

**Important caveat for whoever picks this up next: this is UNTESTED on
actual Windows.** This sandbox is Linux-only; .bat files cannot be
executed here to verify them end-to-end. Each individual mechanism was
checked against documentation rather than guessed (where's exit-code
convention, fc /b's exit-code convention and the need to quote paths
re-confirmed via search, not assumed; the Python-side version-check
one-liner's exit-code behavior WAS directly tested in this sandbox since
that part is plain Python) but the assembled whole has not been run for
real. If the user reports it not working, do not assume the documented
behavior of any individual command is wrong before checking for a typo,
quoting issue, or Windows-version-specific quirk in the assembled script
first — re-verify the individual pieces only if those don't explain it.
Also added a "Quickest way to start" section at the top of `README.md`
pointing to the launcher, with the previous manual steps kept below it as
a fallback for anyone who prefers typing the commands themselves or hits
something the launcher doesn't handle.

### 🔴 REAL BUG FOUND on actual Windows, then FIXED (the launcher above
was wrong on first attempt — this section documents what actually
happened, since it's a useful lesson, not just a changelog entry)

User tested the launcher for real (the first genuine Windows test of
anything in this app's batch-file tooling) and reported, with a
screenshot: the window opened, printed the banner text up through
"Please don't close this window while the app is running", then closed
itself completely with NO error message at all — not even reaching the
"Press any key" pause in the Python-not-found branch. This happened
immediately after that point in the script, i.e. right around the
`where py` / `py --version` Python-detection block.

Root cause, reasoned through rather than guessed: double-clicking a .bat
file runs it via a plain `cmd /C`-style invocation, which closes the
window the instant the script ends for ANY reason — including an
unhandled crash partway through — not just normal completion. Every
`pause` in the previous version only protected the specific failure
paths that were anticipated in advance (Python missing, pip failing).
None of them could protect against a failure nobody predicted, because a
`pause` only runs if execution actually reaches that line. This is a
structural flaw in the whole approach, not a one-line typo — patching
the specific spot that died would not have prevented the same class of
problem from recurring somewhere else later.

FIX: rewrote the launcher to use the well-documented pattern of having
the script relaunch ITSELF inside `cmd /K` on first run (`cmd /K call
"%SELF%" run`, checking for a "run" marker argument to avoid relaunching
forever). `cmd /K` keeps the window open no matter what happens inside
the relaunched instance — including a totally unanticipated crash — which
fixes the entire CLASS of "window vanishes with no explanation" failures
at once, rather than chasing this one instance. Also simplified the
Python-detection block while rewriting (dropped the separate `where`
calls, since `py --version`/`python --version` directly answers both
"does it exist" and "does it run" in one step — fewer moving parts, less
to get wrong) and removed the now-redundant `pause` calls inside the
error branches, since the outer `cmd /K` already guarantees the window
stays open and a layman reading an error doesn't need an extra keypress
gate on top of that.

**This fix is, again, UNTESTED ON REAL WINDOWS — same caveat as before,
now doubly important since the previous version's untested status is
exactly what let this bug through.** What WAS verified this round, in
this Linux sandbox: the `cmd /K call "%SELF%" run` self-relaunch pattern
specifically (checked against a documented working example using `cmd
/K "%0 run"`, rather than trusting a first-draft nested-quote version
that looked plausible but wasn't confirmed against any source — that
first version was caught and replaced before shipping, specifically
because it was a guess); all `goto` labels resolve to a real `:label`
that exists in the file; parentheses are balanced (15 open, 15 close)
across the whole script. None of that substitutes for actually running it
on Windows. If this STILL fails, the most useful next piece of
information is exactly which line of visible output was the LAST thing
printed before the window closed (as the user correctly provided last
time via screenshot) — that pinpoints the dying line precisely and is
far more useful than re-guessing from the script text alone.

### 🔴 SECOND real bug, found from the exact error text this time (the
`cmd /K` fix above DID work as intended — the window stayed open long
enough to show a real error message instead of vanishing, which is
exactly what it was built to do; this is a different, additional bug
under it)

User got: window prints the banner correctly, then "Found Python -
checking it's a recent enough version...", then `. was unexpected at
this time.` and the cmd prompt was left sitting open (proof the cmd /K
fix is working — previously this would have closed instantly with zero
information).

Root cause, confirmed against cmd.exe's documented parsing rules (not
guessed): the Python version-check line was
`"!PYTHON_CMD!" -c "import sys; assert sys.version_info >= (3, 9)"`,
immediately followed on the next line by `if not !errorlevel! == 0 (`.
cmd.exe's bracket-balance tracking operates on raw line text and does NOT
understand that the `(3, 9)` it sees is sitting inside a quoted string
that's meaningless to it as Python syntax — confirmed via a Microsoft
Learn forum case and a still-open Ghidra GitHub issue showing the
identical symptom from the identical cause (a parenthesis inside a
quoted command-line argument, sitting near a batch block boundary).

FIX: moved the version check out of the .bat file entirely, into a new
small standalone file, `_check_python_version.py`, which the launcher now
just calls directly (no parens-in-quotes anywhere near a block boundary,
because there's no Python source text embedded in the .bat file at all
anymore for this check). This sidesteps the whole class of problem rather
than carefully tip-toeing around exactly which parenthesis adjacency is
safe.

**While re-reading the rest of the file for any other instance of this
same class of bug** (since the question "did I make this mistake
anywhere else" is exactly the right thing to ask after finding it once),
found a SECOND, not-yet-triggered instance: `echo   (3.9 or newer
required).` on the line right after, INSIDE the same `if (...)` block,
had unescaped parentheses. Confirmed via Microsoft's own `echo` docs,
quoted directly: "When inside a block terminated by parentheses, both
opening and closing parentheses must also be escaped using the caret,
immediately before each one" — stated as an unconditional rule, not a
sometimes-needed one. This one hadn't been hit yet only because the
earlier bug killed the script before reaching it. Fixed by escaping it
(`^(3.9 or newer required^).`) to match the pattern already correctly
used a few lines below it (`^(tick "..." during install^)`), which was
done right the first time — the inconsistency between those two adjacent
lines is exactly how this got missed in the first review.

**Lesson for next time, stated plainly so it isn't repeated a third time:
any `(` or `)` appearing ANYWHERE inside an `if (...)` block in this file
— whether in an echoed message or inside a quoted argument to another
program — needs scrutiny, either by escaping it with `^` (for literal
text being echoed) or by moving it out of the .bat file entirely (for
embedded code in another language, like the Python check). A "looks
balanced to me" read-through is not sufficient; check every single
paren character against this rule individually.** Re-scanned the entire
file after both fixes (`grep` for every line containing `(`) and confirmed
no further instances remain.

Still genuinely unverified end-to-end on real Windows beyond the point
the user's test reached. If another failure shows up further down the
script (the dependency-install block or the final app launch), the same
approach applies: get the exact last-printed line from the user, don't
re-guess blind.

### ✅ DONE — fixed a real crash: "up_axis and forward_axis must be
different" when picking Z as the up axis

User hit this trying to orient a BeamNG mod (BeamNG commonly uses Z-up,
unlike every KN5 file tested so far which has been Y-up) — picking "Z" in
the Up Axis dropdown crashed the render instead of working. Root cause,
found by reading the code rather than guessing: `forward_axis` has never
had a UI control of its own (only `up_axis` is exposed in
`templates/index.html`) and was hardcoded to default to `"z"` in
`app.py`'s `/generate` route, with zero relationship to whatever the user
picked for `up_axis`. Picking "Z" as up therefore always collided with
the hardcoded "Z" forward default, hitting `AxisConfig`'s (correct, not
buggy) guard against up_axis == forward_axis. "X" as up would have hit a
quieter, equally real version of the same bug — not crashing, but
silently keeping forward_axis="z" regardless, which has no guarantee of
being the right forward axis for that file either.

FIX: in `app.py`, forward_axis is now derived FROM up_axis when not
explicitly provided, instead of being a flat hardcoded constant: defaults
to "z" only when that's different from up_axis, otherwise falls back to
"y", and only to "x" as a last resort if both of those collide too. This
guarantees up_axis != forward_axis always holds without ever crashing,
for any of the three up_axis choices. Verified by testing all three
up_axis values (x/y/z) through the real Flask `/generate` route on the
Liana (200 OK for all three, previously "z" alone would 500), plus a
fuller realistic combination (up_axis=z, AO on, 4 views, 2 rib cuts —
200 OK, ~27s).

**Caveat: this was verified against the crash condition using the
Liana as a stand-in, NOT against the user's actual BeamNG file** — no
BeamNG model file was actually uploaded in the conversation where this
was reported, only a screenshot of the error text. The fix is confirmed
to stop the crash for any up_axis choice; it has NOT been confirmed to
produce a geometrically correct-looking result on a real BeamNG model
specifically. If the user provides the actual file, re-verify visually,
not just "does it 200" — `detect_front_axis` already handles all three
axis letters correctly (confirmed by reading it: `axis_i = {"x":0,
"y":1,"z":2}[forward_axis]`), so no further change should be needed
there, but a real BeamNG file may reveal something this fix didn't
anticipate (e.g. its naming convention for front/rear parts may not match
any of the keyword heuristics `detect_front_axis` looks for, in which
case the front/back orientation may still need the existing manual
"Flip front/back" checkbox).

### ✅ DONE — AO shading always rendered black/gray regardless of
line_color, on any non-default colour scheme

User reported, with real renders (blue linework on a dark navy
background): AO shading came out black/gray every time, completely
disconnected from the chosen line colour. Confirmed by reading
`_draw_view()` in `compositor.py`: the AO raster was multiply-blended as
`background_rgb * grayscale` — line_color was never referenced anywhere
in that blend at all. This happened to look correct for the
black-on-white default (darkening white lands on values that read as
"shaded black"), which is exactly why it went unnoticed through all the
earlier AO work — every test render so far used the default colours.

FIX: changed the blend from a plain multiply against bg_color to a
linear interpolation between bg_color (fully open, gray=255) and
line_color (fully occluded, gray=0): `rgb = gray_norm*bg + (1-gray_norm)
*line`. This is mathematically identical to the old behaviour exactly
when line_color is black (reduces to gray_norm*bg, i.e. the previous
formula) — confirmed via direct render comparison, default colours are
pixel-for-pixel the same. For any other line colour, shading now tints
toward that colour instead of going gray/black. Verified three ways:
direct render comparison (default colours unchanged), a blue-on-navy
direct render (shading now reads as darker blue, not gray), and the same
blue-on-navy combination through the real Flask `/generate` route with
explicit `bg_color`/`line_color` params (200 OK, visually correct).

### 📋 Still pending — NOT started (unchanged from before, still waiting
on explicit go-ahead; the AO raster/gradient work above did not touch
this)

**Canvas/layout reorganization** — current compositing wastes space:
- Rib/cross-section slices are laid out in a single row, so requesting
  many cuts tanks effective per-slice resolution. User wants: max 4 rib
  slices per row, wrapping to additional rows beyond that.
- Top and bottom views currently stack vertically (one above the other)
  when they could sit side by side, same as front/back and... wait, no —
  re-check actual current layout in `compositor.py: compose_image()`
  before assuming; user specifically flagged top/bottom as stacked when
  they could be side-by-side. Confirm current grid logic first, then fix.
- This is a `compose_image()` / `compositor.py` layout change only — no
  renderer or AO logic involved. Should be a contained, low-risk change
  once started, but has NOT been scoped or started yet as of this
  handover.



---

## Detailed historical log (reference only — you do not need to read this
to continue work; it explains how the above came to be and is preserved
for forensics / "why is it built this way" questions)

This document exists so work can resume cold, in a new conversation, without
re-deriving anything. Read this first. If anything here conflicts with a more
recent message in the conversation, the conversation wins — update this file
to match before continuing.

## What this project is

A standalone local tool: user runs one command (after a one-time Python
setup), it opens a page in their normal browser, they drop in a 3D model
file, get a checkbox list of the model's named parts, tick/untick what's
exterior-visible vs not, pick output options, click Generate, get a PNG of
orthographic line-art views (front/back/left/right/top/bottom + a width-wise
"rib" cross-section at the model's midpoint). No Blender, no 3D knowledge
required — the person judges success purely by "does the picture look right."

## Decisions locked in (do not re-ask the user about these)

1. **Delivery mechanism**: Python installed locally (one-time), then a local
   web server + browser tab. NOT a packaged .exe (too large, too fragile to
   build/test blind, ruled out explicitly).
2. **Input formats, in priority order**: OBJ (proven, primary), DAE/Collada
   (native trimesh support via pycollada, for BeamNG mods), KN5 (Assetto
   Corsa — custom parser, see below), STL, GLTF/GLB. FBX explicitly
   excluded — user doesn't want it ("that ish for nerds").
3. **KN5 support**: MUST use an original, from-scratch parser
   (`kn5_reader.py` / `kn5_to_obj.py`) — NOT the MarvinSt/kn5-obj-converter
   GitHub script, because that repo has no LICENSE file, meaning no rights
   are granted to copy/reuse it. Our own parser is already written and
   VERIFIED against a real file (see "What's already built and verified").
4. **Part inclusion/exclusion**: checkbox list, all named parts shown, all
   ticked by default. Select-all / deselect-all buttons. Must be scrollable
   (not a giant page), wide enough to read full names, with a text filter
   box above it (search/substring filter) since real models can have 150+
   parts — confirmed via UX research that a plain long checkbox list is bad
   practice past a handful of items; scrollable listbox + filter is correct.
   No nested/grouped checkboxes needed — flat list is fine, sort
   alphabetically for scannability.
5. **Selections persist between runs**: save ticked/unticked state to a small
   file keyed by the uploaded filename, reload it automatically next time the
   same filename is used. User confirmed filename-keying is sufficient (no
   need for content-hash matching or fuzzy rename detection).
6. **BeamNG wheel auto-repositioning**: explicitly a STRETCH GOAL, not part of
   the current build. Don't spend time on it unless everything else is done
   and the user asks. Manual offset controls (type/slide X/Y/Z per part) is
   the agreed fallback if it's ever tackled — full auto-detection from
   sidecar config/JSON files is unproven and out of scope for now.
7. **Color options**: two hex color pickers (background, line color) plus
   two one-click presets (black-on-white, white-on-black) that just fill the
   pickers. No other presets requested.
8. **View selection + output scale**: checkboxes for front / back / left /
   right / top / bottom / rib (the width-wise cross-section at the model's
   Z-midpoint — NOT the lengthwise centerline cut, which was a separate
   earlier experiment and is NOT one of the requested standard outputs
   unless the user asks for it again specifically). Scale slider for output
   resolution (e.g. 25%–100% of full render res).
9. **Shading/AO detail**: optional toggle, OFF by default. This is the
   Lambertian-normal-shading-contour technique already built and tested in
   conversation (not true ray-traced AO — surface-normal-angle-based, see
   below). Already known to work well on clean meshes (Probox-quality) and
   to add unhelpful noise on fragmented meshes (VY-quality). Expose as a
   toggle the user can try per-model, not as a default-on feature.
10. **Exterior/interior classification**: NO automatic name-based
    heuristics in the app (no hardcoded "exclude if name contains cockpit").
    The whole point of the checkbox UI is that the USER decides per-model by
    looking at the rendered result — the app's job is to make that loop fast
    (render → look → untick → regenerate), not to guess correctly up front.
    Default state: everything ticked (included) on first load of a new
    file.

## What's already built and verified (working code exists, in conversation's
sandbox, NOT yet assembled into the app)

- **Core render pipeline** (developed across the Probox and Holden VY work):
  - Load mesh, weld duplicate/per-face vertices (`merge_vertices`), this is
    essential — many real-world exports duplicate vertices per-face and
    silently break adjacency/edge-detection if not welded first.
  - Depth-buffer orthographic rendering via `pyrender` + EGL offscreen
    context. WORKS in this sandbox without a virtual display.
  - **Critical known bug, already solved**: pyrender applies a
    perspective-camera hyperbolic depth-remap formula to ALL cameras
    including orthographic ones, producing wrong depth values. Fixed with a
    verified inversion formula (`pyrender_depth_to_true_distance` in
    `visibility.py`) — exact to <1e-6 error against known geometry. DO NOT
    use raw pyrender depth output for anything; always run it through this
    conversion.
  - Visibility-tested hidden-line removal: sample N points along each
    candidate edge (feature edges from dihedral angle + true mesh
    boundaries), compare against the depth buffer at that pixel, keep only
    visible sub-segments. Fully vectorized with numpy (~1-2 sec for 50k
    edges × 30 samples). N_samples=30, depth_eps≈0.018 found to avoid both
    dashing artifacts (too few samples) and false bleed-through (too loose
    an epsilon).
  - Silhouette extraction via marching squares (`skimage.measure.find_contours`)
    on the depth mask — this is what draws the actual outer body outline;
    feature-edge detection alone does NOT capture smooth-surface silhouettes
    (a real gap discovered mid-build, not optional).
  - Consistent cross-view scale: one shared `xmag=ymag=half_span` (derived
    from the single largest dimension across the whole model) used for
    EVERY view's camera — do not fit each view's frame independently, this
    breaks true relative scale between panels (an early mistake, caught and
    fixed).
  - Layout/composition: pixel-exact axes placement via figure-fraction
    coordinates derived from each view's actual used bounding box (with
    small padding) — NOT matplotlib GridSpec height/width ratios, which
    fight aspect-equal scaling and produce wrong proportions across panels
    of very different aspect ratio.
  - Rib cross-section: `mesh.section(plane_origin=center, plane_normal=[0,0,1])`
    — a true geometric plane-mesh intersection (trimesh built-in), gives the
    width-wise "frame slice" outline at the Z-midpoint. Independent of
    mesh topology quality, works even on fragmented meshes.
- **KN5 parser** (`kn5_reader.py`, `kn5_to_obj.py`): original implementation,
  written from documented high-level KN5 structure (not copied from any
  existing converter — that was a hard requirement, see decision #3).
  VERIFIED against a real file (`gtsupreme_v8_supercars_holden_vy.kn5`):
  correct magic bytes, correct node/material/texture counts, vertex/face
  counts and bounding boxes match ground truth exactly, front/back bumper
  Z-positions match known-correct orientation. Also incidentally DISCOVERED
  AND FIXED a real bug in whatever tool produced the original
  donor-converted OBJ we'd been using earlier in the conversation: it had
  exactly double the real triangle count (every face duplicated verbatim) —
  our from-scratch parser produces the correct, non-duplicated geometry.
  One known practical gotcha: OBJs written by our exporter include `vt` UV
  coordinates, which makes trimesh auto-generate a `TextureVisuals` object
  on load even with no actual image — this crashes pyrender's texture
  upload. Fix: `mesh.visual = trimesh.visual.ColorVisuals(mesh)` immediately
  after loading, before any rendering. MUST carry this fix into the app.
- **Shading-contour detail pass** (the "AO toggle", decision #9): bake
  Lambertian shading (`clip(vertex_normals @ light_dir, 0, 1)`) as vertex
  colors, render with full ambient light (so the baked shading IS the only
  shading), extract grayscale, contour the shading field at multiple levels
  (`skimage.measure.find_contours` on the shading array, not the depth
  array). Light direction must be meaningfully oblique to each view's own
  camera axis or shading flattens out — different light_dir needed per view.
  Tested and works well on clean meshes; produces too much noise on
  fragmented ones (mesh-fragmentation shows up as shading noise). User
  rejected the all-views-always-on version; wants it as an optional toggle.

## What's NOT yet built (genuinely remaining, as of this update)

- **End-to-end test through a real browser tab.** Everything has been
  verified via Flask's test client (`app.test_client()`) and direct
  function calls, which exercises the real app code faithfully — but no
  one has actually opened a browser, dragged a file onto the page, clicked
  checkboxes with a mouse, and looked at the result through the actual UI.
  Worth doing once a real session can run the server persistently (see
  "known environment gotcha" below).
- **BeamNG wheel auto-repositioning** — explicit stretch goal, untouched,
  not blocking anything.
- **Testing against a real `.dae` (BeamNG) and a real `.stl`/`.gltf`/`.glb`
  file.** Only tested against synthetic single-box files for these three
  formats so far (confirmed the code path doesn't crash, but real files
  would have meaningful multi-part scene-graph names to verify against,
  the way the OBJ/KN5 tests did). Ask the user for one of each if/when
  convenient — not blocking, OBJ and KN5 (the user's actual primary
  formats) are fully verified.
- Polish items not yet considered: what happens if the user's browser tab
  sits open across multiple file uploads (does the part list / color
  pickers reset sensibly?), whether the uploads/outputs folders need any
  cleanup of old files over time (currently grow unboundedly), and whether
  large files (very high poly count) need a progress indicator beyond the
  static "Rendering..." message (currently no progress bar, just a wait).

## Known environment gotcha (sandbox-specific, may not apply to user's PC)

In THIS development sandbox, background processes started with `&` in one
`bash_tool` call do not reliably survive into the next call (each call
appears to get a fresh shell). This made testing via real `curl` HTTP
requests unreliable — solved by testing through Flask's `app.test_client()`
instead, which exercises the exact same route handlers without needing a
persistently-running server process. This is NOT expected to be an issue
on the user's actual Windows PC (they'll have one persistent command window
running `python app.py` for as long as they're using the tool) — flagging
this only so a future session doesn't waste time re-discovering the same
sandbox quirk, and so it's clear the test methodology (test client, not
live HTTP) was a deliberate workaround, not a weaker form of testing.

## What's built AND VERIFIED in `ortho_app/` specifically (this is the actual
app directory — files here are real, tested, working code, not scratch)

- **`model_loader.py`** — DONE, TESTED, WORKING:
  - `load_model(path)` → `(mesh, part_face_ranges)`. Dispatches by extension:
    - `.kn5` → converts via `kn5_to_obj.py` to a temp file, then treats as OBJ
    - `.obj` → uses a hand-rolled group parser (`_parse_obj_groups`), NOT
      trimesh's scene loader. This was a real, confirmed bug caught during
      this build: `trimesh.load(..., force='scene')` groups faces by
      MATERIAL, not by the file's real `o`/`g` names — a 41-group Probox
      OBJ collapsed to ~16 generic material-based groups under trimesh's
      default path. The manual parser fixes this and is tolerant of
      leading-space group lines and mixed line endings (real quirks hit
      with actual files, e.g. the VY model's `\r`/`\r\n` mixed terminators
      and " g name" leading-space lines — Python's text-mode universal
      newline handling resolves the line-ending issue automatically, the
      leading-space handling is explicit in the parser).
    - `.dae` / `.stl` / `.gltf` / `.glb` → trimesh scene loader, using each
      format's own real scene-graph node names (these formats don't have
      the OBJ material-grouping problem the same way).
  - `build_filtered_mesh(mesh, part_face_ranges, excluded_parts)` → returns
    a new mesh with excluded parts' faces removed. TESTED: reproduces the
    exact bounding box we derived by hand for the Probox exterior-only mesh
    earlier in the project (`[[-0.995,-0.059,-1.988],[0.998,1.669,2.277]]`).
  - Texture-visual stripping (`strip_texture_visuals`) applied universally
    on every load path, fixing the pyrender-texture-crash issue for ALL
    formats, not just KN5-sourced ones.
  - VERIFIED against: real Probox OBJ (43 parts recovered, matches hand-
    parsed count from earlier in conversation), real VY KN5 (166 parts,
    correct de-duplicated 31,099-face count, full pipeline KN5→OBJ→mesh
    works with no crash), and synthetic STL/GLB/DAE box files (load
    without crashing; real-world files of these formats would carry
    meaningful node names where a synthetic test box can't).
- **`renderer.py`** — DONE, TESTED, WORKING:
  - `get_model_scale(mesh)` → `(center, half_span, dist)`, one consistent
    scale for all views (see model_loader section above for why this must
    be shared, not per-view).
  - `detect_front_axis(mesh, part_face_ranges)` → `+1` or `-1`. Best-effort
    guess using part names containing front/bump/hood vs rear/back/trunk/
    tail, comparing average Z position of matched parts. Returns `+1` with
    no guess if no naming signal exists (caller/UI should offer a one-click
    flip, since this is a genuine per-model judgment call with no universal
    convention — confirmed Probox front=-Z, Holden VY front=+Z).
  - `render_view(mesh, view_name, center, half_span, dist, front_sign, ...)`
    → dict with outer_contours, shade_contours, edge_segments, pose/xmag/
    ymag/resolution (needed to project edge_segments to pixel space later).
    Supports all 6 standard views (front/back/left/right/top/bottom).
  - `render_rib_section(mesh, center)` → list of world-space segment pairs
    for the width-wise cross-section at the model's Z-midpoint.
  - `_render_shade_contours(...)` — the optional shading-detail pass
    (decision #9), picks a per-view oblique light direction automatically
    via `_pick_light_dir` so it never flattens out regardless of which
    view is being rendered.
  - **REAL BUG FOUND AND FIXED during this build**: initial `_view_geometry`
    placed the camera on the WRONG side for front/back/top — e.g. asking
    for 'front' on the Probox (whose front bumper is confirmed at -Z)
    rendered the BACK of the car instead. `detect_front_axis` itself was
    computing the correct sign; the bug was in how that sign was applied
    to camera eye placement (was using `+dist*front_sign` for front, needed
    `-dist*front_sign` — camera must sit on the SAME side as the
    front and look back toward the model, not the opposite side). Fixed
    and re-verified visually against all of front/back/top — all three
    now correctly match known-good orientation from earlier conversation
    work. IMPORTANT: if view orientation ever looks wrong again during
    future testing, check this sign convention first before assuming a new
    bug.
  - VERIFIED against the real Probox OBJ with the same exclusion list used
    earlier in the conversation: filtered face count matches exactly
    (119,893), front/back/top views visually confirmed correct after the
    sign fix, rib cross-section shape matches the expected "frame slice"
    silhouette, shading-detail option runs without crashing.

## Performance (measured, not estimated)

Tested via Flask's test client against the real Probox OBJ (134k faces) on
the sandbox machine used for development (pure CPU, software EGL rendering
— no GPU acceleration in play despite "EGL" in the code):
  - 4 views + rib, full resolution, no shading: ~49 seconds
  - Same request WITH shading detail on: ~47 seconds (shading didn't add
    much here; may cost more on other meshes — not extensively tested)
  - 1 view, 50% output scale, no shading: ~9 seconds
Conclusion told to user: expect roughly 10 seconds to ~1 minute per
Generate click depending on view count / resolution / mesh complexity, NOT
instant. The resolution slider is the user-facing lever for trading speed
vs detail. Real-world timing on the user's own Windows PC will differ
(unknown CPU, but same lack of GPU acceleration) — flagged as an honest
unknown, not measured on actual target hardware.

## UI clarification (user asked, answered, recorded so it isn't re-asked)

User asked whether a friendly non-command-prompt interface was being built.
Answer: yes, already done — `templates/index.html` IS that interface. The
command prompt is ONLY used for one-time setup and typing `python app.py`
to start the server; all actual interaction (file drop, part checklist,
color pickers, Generate button, viewing the result) happens in a normal
browser tab. This was apparently not clear from earlier framing — worth
remembering this is a documentation/communication gap, not a missing
feature, if it comes up again.

## Current overall state (accurate as of this update)

Everything originally planned for v1 is built and tested: `model_loader.py`,
`renderer.py`, `compositor.py`, `app.py` (Flask server with `/`, `/upload`,
`/generate`, `/outputs/<filename>` routes), `templates/index.html` (the full
browser UI — file drop zone, filterable scrollable part checklist with
select-all/deselect-all, color pickers with two presets, view checkboxes,
rib toggle, resolution slider, shading toggle, Generate button, image
display, download link), `README.md` (setup/usage instructions for the
user). Selection persistence-to-disk is wired in and confirmed working
(tested: excluded a set of parts, re-uploaded the same filename, got the
same exclusions back automatically).

This is a genuinely complete, working v1 of everything the user asked for
in their numbered-points message, EXCEPT the explicitly-deferred BeamNG
wheel auto-repositioning stretch goal. The realistic remaining work is
verification polish (real browser test, real DAE/STL/GLTF/GLB files) and
any rough edges that surface once the user actually tries it on their own
machine with their own files — NOT new features.

## Real bugs found via actual user testing on Windows (this is the genuinely
valuable feedback loop — sandbox testing alone could never have caught
this one)

1. **Missing `matplotlib` in requirements.txt / dependency self-check.**
   `compositor.py` imports matplotlib directly but it was never added to
   `requirements.txt` or the `_check_dependencies()` list. User hit a raw
   `ModuleNotFoundError` traceback when running `python app.py` after an
   otherwise-successful install. FIXED: added to both files. VERIFIED:
   re-ran the same fake-missing-import test used for the original
   dependency check, confirmed it would now report `matplotlib`
   specifically with a clean exit instead of crashing.

2. **EGL hardcoded everywhere, fails on Windows.** All three files that
   touch pyrender (`depth_render.py`, `visibility.py`, `renderer.py`) were
   force-setting `PYOPENGL_PLATFORM=egl` unconditionally (two of them) or
   via `setdefault` (one of them, which still forces it on first import).
   This works in the Linux development sandbox but EGL is fundamentally a
   Linux/NVIDIA-driver technology not generally present on Windows — user
   hit exactly this: `Unable to load EGL library ... [WinError 126]`,
   confirmed via web search to be a known, common pyrender-on-Windows
   failure mode, NOT something unique to this user's machine.
   FIXED: changed all three files to only force EGL when
   `sys.platform.startswith("linux")` AND no platform is already set in
   the environment; otherwise leaves `PYOPENGL_PLATFORM` unset, which
   (per pyrender's own source, confirmed via documentation lookup) makes
   it fall back to its default Pyglet-based offscreen platform instead —
   which doesn't require EGL.
   **NOT YET VERIFIED on an actual Windows machine** — I have no Windows
   environment to test in directly, only the Linux sandbox (confirmed: no
   regression there after the fix) plus external documentation/community
   reports supporting that this is the correct fix. This is the most
   important open risk right now: if Pyglet's fallback hits its OWN
   Windows-specific issue (some reports suggest it may want a real
   display context even in "offscreen" mode on certain systems), that
   would be the next thing to chase, with the user's actual error message
   as the next diagnostic input — don't assume this is fully resolved
   until the user confirms a render actually completes successfully.

## How to keep helping efficiently if more Windows-specific errors surface

This is now the dominant risk category (not logic bugs — those are well
tested; OS/environment friction is the unknown). When the user reports a
new error: (1) search for it verbatim plus "pyrender"/"trimesh"/"Windows"
as appropriate before guessing, (2) check ALL THREE files that touch
pyrender (`depth_render.py`, `visibility.py`, `renderer.py`) for the same
class of issue rather than patching just one, since this exact bug
(EGL hardcoded) existed in triplicate and would have been easy to fix in
only one place and miss the others, (3) re-verify no Linux regression
after any fix, since that's the only environment available for direct
testing, (4) update requirements.txt / the dependency self-check in the
same pass if the error is import-related, since the matplotlib gap shows
that list can silently drift out of sync with actual code.

User pasted the original `pip install ...` command into cmd and got
`'pip' is not recognized` — a real, common issue (Python installed without
"Add to PATH" ticked, or PATH not refreshed). Rather than wait idle, fixed
this properly instead of just re-explaining the same command:
  - Added `requirements.txt` (clean dependency list, one per line).
  - Changed the documented install command to `python -m pip install -r
    requirements.txt` instead of bare `pip install ...` — confirmed this
    only depends on `python` being found, not a separate `pip` shim, which
    is more robust right after a fresh install (verified: `python3 -m pip
    --version` works in this sandbox even where bare `pip` might not on a
    fresh Windows install).
  - Rewrote README's setup/troubleshooting section with the SPECIFIC error
    text the user hit (`'pip' is not recognized...`) and a decision-tree
    fix (check `python --version` first; if that also fails, PATH wasn't
    set during install, re-run the installer; if `python` works but `pip`
    alone doesn't, use `python -m pip` directly).
  - Added a dependency self-check at the top of `app.py`
    (`_check_dependencies()`), run BEFORE the real imports. If any required
    library is missing, prints a short plain-English message naming the
    missing package(s) and the exact fix command, then exits cleanly —
    instead of a raw Python traceback, which would be unreadable to someone
    without Python experience. TESTED: verified it passes through silently
    when everything is installed (no false positives), and verified it
    produces the correct friendly message and clean exit when a dependency
    is simulated as missing (patched `builtins.__import__` to fake a
    missing `pyrender`, confirmed correct package name reported and clean
    `sys.exit(1)`, no traceback).

This work was done speculatively (user hadn't reported the install issue
was fixed/retried yet) — if the user's actual problem turns out to be
something else not covered by this, that's the next thing to address, not
a sign this work was wasted (the self-check and requirements.txt are
worthwhile regardless).

Hand this off to the user to actually try on their own Windows PC with
real files (their own .obj files, plus the .kn5 already proven). This is
the first point where real user-side testing matters more than further
sandbox-side development — issues that show up on a real Windows machine
## Real-world testing: SUCCESS (first confirmed working run on user's actual PC)

User confirmed the app works end-to-end on their real Windows machine after
the matplotlib + EGL fixes above — "does what it says on the tin pretty
well." This is the first real confirmation outside the sandbox/test-client
testing. Three new requests came out of that real usage, now being worked:

1. **"Which way is up" manual override.** Some BeamNG models load oriented
   wrong (front/back or up-axis guessed incorrectly by `detect_front_axis`,
   which only works if part names give a naming signal — BeamNG part names
   may not). User confirmed this is fine to work around by just selecting
   all 6 views and picking the right one, but wants a proper control rather
   than relying on that workaround forever. PLAN: add (a) an up-axis
   dropdown (X/Y/Z — currently Y is hardcoded as up everywhere) and (b) a
   one-click front/back flip button, both as manual overrides sitting
   alongside the existing auto-detection, not replacing it.

2. **BeamNG wheel-at-origin issue** — user says it's fine, "just turn the
   wheels off" (untick the wheel parts in the checklist) is an acceptable
   workaround. CONFIRMED stretch goal status, deprioritized for real this
   time — do not work on this unless explicitly asked again.

3. **Full cross-section mode (multiple ribs along the model's length).**
   Currently `render_rib_section()` only cuts once, at the exact Z-midpoint.
   User wants a slider (suggested range: up to 8 cuts) producing evenly-
   spaced slices from front to back, laid out together on the output sheet
   — a natural generalization of the existing single-rib feature, same
   underlying `mesh.section()` operation repeated at different Z offsets
   along the model's length instead of just at the center.

## Precise rib cut-count spec (user clarified, exact — do not approximate)

N cuts divide the body into N+1 equal-length segments along the front-back
axis, with cuts placed at each INTERNAL boundary between segments:
  - N=1 (minimum): 1 cut, exactly at the midpoint (1/2).
  - N=2: cuts at 1/3 and 2/3.
  - N=3: cuts at 1/4, 1/2, 3/4.
  - General: cut k (1-indexed, k=1..N) sits at fraction k/(N+1) along the
    model's extent on the forward axis, measured from one end to the other
    (front to back, or whichever axis is forward per the up/front-axis
    settings — see request #1 above, these two features interact: cut
    plane orientation must follow the same forward-axis logic as
    front/back view detection, not always literally Z).
  - This is NOT "8 cuts evenly across the middle 80-90%" as speculated in
    the previous version of this doc before the user clarified — that
    speculation is WRONG, replace it with this exact spec. No margin/
    inset trimming at the ends; the fractions above are exact and the
    user was specific that they should be exact.

Implement requests #1 and #3 above. For #1: add `up_axis` parameter
threaded through `renderer.py`'s view geometry (currently every view
hardcodes `up=[0,1,0]` or similar Y-up assumptions — needs to become
configurable) plus a simple front/back-flip toggle in the UI that just
negates `front_sign` regardless of what auto-detection said. For #3:
generalize `render_rib_section(mesh, center)` to accept a cut count N and
produce N cuts at the exact fractions specified in the precise spec above
(k/(N+1) for k=1..N along the forward axis, using the model's actual
bounding-box extent on that axis as the 0..1 range — no margin/inset).
Update `compositor.py`
to lay out N rib slices in a row/grid rather than the current single
centered rib panel. Update `templates/index.html` / `app.py` to expose the
new controls. Test against the real Probox AND the real VY KN5 (the rib
slicer's behavior differed meaningfully between those two meshes earlier
in the project — clean vs fragmented topology — worth re-confirming
multi-cut mode handles both reasonably before calling this done.

## Three requested features: BUILT, TESTED, WORKING (supersedes the
"implement requests #1 and #3" paragraph immediately above this one)

1. **Manual orientation overrides.** Added `AxisConfig` class in
   `renderer.py` (up_axis, forward_axis, front_sign as explicit fields
   instead of hardcoded Y-up/Z-forward scattered through the view-geometry
   code). `_view_geometry` and `detect_front_axis` both rewritten to use
   this instead of bare Z-axis assumptions. UI: added an up-axis dropdown
   (X/Y/Z) and a "flip front/back" checkbox in `templates/index.html`,
   wired through `app.py`'s `/generate` route (`up_axis`, `forward_axis`,
   `front_flip` JSON fields). VERIFIED: tested `front_flip=True` against
   the Probox (whose correct front-facing orientation was already
   confirmed) — asking for "front" with the flip on correctly produced the
   BACK view (license plate, hatch) instead, proving the override
   genuinely inverts the sign rather than being a no-op.

2. **BeamNG wheel positioning** — confirmed by user as not worth solving,
   "just turn the wheels off" (untick in the part checklist) is accepted
   as sufficient. Stretch goal status reconfirmed, deprioritized for real.

3. **Multi-cut cross-section mode.** Exact spec from user (verified,
   not approximated): N cuts divide the model into N+1 EQUAL segments
   along the forward axis, with cuts at each internal boundary —
   fraction k/(N+1) for k=1..N. N=1 (minimum) = single cut at exact
   midpoint, matching the prior single-rib behavior exactly (confirmed:
   identical segment count, 582, between the old single-cut function and
   the new multi-cut function's n_cuts=1 case run at the midpoint
   fraction). Implemented as `render_rib_sections(mesh, axis_cfg, n_cuts)`
   in `renderer.py`, replacing the old single-cut `render_rib_section`.
   `compositor.py`'s `compose_image` updated to lay out N rib cuts side by
   side in one row (was previously a single centered panel). UI: added a
   slider (1-8) for cut count, shown alongside the existing rib checkbox.
   VERIFIED: visually confirmed a 4-cut test on the Probox shows a
   sensible front-to-back progression (headlight area → dashboard →
   rear seats → roof/hatch), and the exact fractions were checked
   numerically against the user's spec for n=2,3,4 (e.g. n=3's middle cut
   lands at exactly the same Z position and segment count as the
   old/new n=1 midpoint cut, confirming the math is consistent).

## Real bug found and fixed during this round of testing (not caught by
Probox-only testing — only surfaced on the harder/fragmented VY mesh)

`detect_front_axis` was being called in `app.py` with the FILTERED mesh
but the UNFILTERED mesh's `part_face_ranges` (whose face-index offsets
only make sense against the original, larger face array). On the Probox
this happened not to crash (the excluded parts were apparently small/
positioned such that stale offsets still landed in-bounds by luck — NOT
because the logic was correct). On the VY KN5 (more parts excluded, larger
relative change in face count after filtering) this threw a hard
`IndexError: index 31091 is out of bounds for axis 0 with size 22234`.
FIXED two ways: (1) the real fix — `app.py` now calls
`detect_front_axis(mesh, part_face_ranges, ...)` using the ORIGINAL
unfiltered mesh, which is the one part_face_ranges' offsets actually
correspond to (front/back detection doesn't need exclusions applied
anyway, it's just reading a few named reference parts). (2) a defensive
safeguard ALSO added directly inside `detect_front_axis` itself — skips
any part whose stored face-offset range falls outside the given mesh's
actual face count, rather than crashing, so a similar mismatch elsewhere
in the future degrades gracefully (falls back to the default +1 guess)
instead of throwing. VERIFIED: re-ran the exact previously-failing
request (VY KN5, left view + 3-cut cross-section, realistic interior
exclusions) — now succeeds and produces a visually sensible result.

**Lesson worth remembering**: the Probox alone was not a sufficient test
case for this bug because exclusion sets there were small/lucky. Any
future change touching `part_face_ranges` indexing should be tested
against the VY KN5 (or another model with a large exclusion set) as well
as the Probox, not just whichever file is fastest to test with.

## Suggested next concrete step

This round of feature work is complete and tested (sandbox-side, via
Flask's test client, on both the Probox OBJ and the VY KN5). Same as the
last handoff: the next genuinely informative step is the USER trying this
on their real PC with real files, since the EGL/Windows fix from last
round and these three new features have only been verified via the test
client + Linux sandbox, never through an actual browser click on Windows
with the new controls. If the user reports back a new issue or confirms
it all works, that's the next concrete input — no further blind sandbox
iteration is likely to surface more right now without that feedback.

## Three more requests handled this round: Mac compatibility, saved
preferences, and replacing fake shading with real AO

1. **Mac compatibility.** Researched rather than guessed: confirmed via
   search that pyrender's own fallback behavior (when `PYOPENGL_PLATFORM`
   is unset, it uses a Pyglet-based platform instead of EGL) already
   covers macOS the same way it covers Windows, since our existing fix
   only forces EGL on Linux specifically. The one Mac-specific addition
   needed came from a Mac-aware version of `app.py` the user supplied
   (uploaded `app.py`, NOT used wholesale — it was based on an OLDER
   version of our app.py missing all the orientation/multi-cut features
   from the previous session, so it was MERGED, not substituted).
   Merged in: (a) `webbrowser.open(...)` to auto-launch the browser tab on
   startup (OS-independent usability improvement, no downside), and (b)
   `threaded=sys.platform != "darwin"` in the final `app.run(...)` call —
   verified via search that this addresses a real, specific, documented
   pyglet/macOS crash ("NSWindow drag regions should only be invalidated
   on the Main Thread!") that can occur when GL/window operations happen
   off the main thread, which Flask's default threaded dev server would
   otherwise risk on Mac specifically. STILL UNVERIFIED ON AN ACTUAL MAC
   — no Mac available to test on directly, same honesty-about-limits
   approach as the original Windows fix.

2. **Saved preferences — confirmed GLOBAL (not per-file), per user's
   explicit answer.** Added `global_prefs.json` (separate from the
   per-file `selections/*.json` part-exclusion persistence, which stays
   per-file since that's model-specific). New `/preferences` GET route
   returns saved-or-default colors/views/scale/AO/orientation settings;
   `/generate` saves them on every request. Frontend fetches `/preferences`
   on page load and applies them to all the relevant controls via a new
   `applyGlobalPrefs()` JS function. VERIFIED: round-tripped through the
   real Flask test client — generated with custom colors/scale/AO-on/
   single-view, confirmed `/preferences` echoed back exactly those values.

3. **Replaced the old fake "shading" toggle ENTIRELY with real geometric
   ambient occlusion** (user's explicit instruction: "ditch the old
   option it sucked"). The old approach only used Lambertian
   surface-normal-vs-light-angle shading, which has no concept of nearby
   occluding geometry — explains why it produced "wiggly lines" rather
   than meaningful crease/recess darkening. New approach
   (`compute_ambient_occlusion` in `renderer.py`): casts multiple
   cosine-weighted hemisphere rays from sample points across the mesh
   surface, checks real intersection with nearby geometry via trimesh's
   ray-mesh intersector (`mesh.ray.intersects_any`), bakes the resulting
   occlusion fraction as per-vertex grayscale brightness. This is a
   GENUINE geometric computation, not a cheap approximation dressed up —
   confirmed both numerically (real variation in output, not collapsed to
   a constant) and visually (clear, correct darkening at door handles,
   window frames, wheel wells, panel gaps — see test renders in
   conversation history).

   **Performance engineering required real iteration, not just parameter
   tuning** — full per-vertex AO was tested and found to be wildly
   infeasible (~1-7 HOURS estimated for the Probox at reasonable ray
   counts, no GPU/embree acceleration available in this environment,
   confirmed: `embreex` not installed, falls back to trimesh's pure-Python
   ray-triangle intersector). Solved via spatial downsampling: one
   representative sample point per voxel cell (voxel size = bounding-box
   diagonal / `voxel_divisor`, default 80), AO computed only at those
   ~11,000 points (for the Probox) instead of all ~350,000 vertices, then
   propagated to every real vertex via nearest-neighbor lookup
   (`scipy.spatial.cKDTree`). Measured real timing: Probox (~350k verts),
   voxel_divisor=80, n_rays=4 → ~44 seconds total. VY KN5 (~22k verts,
   much smaller) → ~10 seconds. AO is computed ONCE per render request
   (not once per requested view) since it's a property of the mesh's
   geometry, not of any camera angle — reused across all views in that
   request.

   **A hard memory ceiling was found and had to be engineered around**:
   calling `mesh.ray.intersects_any(...)` with too many rays in a single
   call gets the process OS-killed (confirmed empirically: works at 4000
   rays, killed somewhere before 6000). Fixed by chunking all ray-casting
   into batches of ~2000 rays per call. This ceiling is specific to this
   environment/trimesh's pure-Python backend and might differ on the
   user's machine, but the chunking approach is safe regardless (just
   means more, smaller calls — correctness doesn't depend on hitting any
   particular ceiling).

   **A second real bug was found and fixed via testing against the VY KN5
   specifically** (the Probox alone did not surface this, same lesson as
   the earlier `detect_front_axis` bug — always test the fragmented VY
   mesh too, not just the clean Probox): ~30% of the VY mesh's vertices
   have degenerate (near-zero-length) vertex normals, almost certainly
   from the mesh's extensive fragmentation (tiny/degenerate triangle
   slivers — established earlier in the project). A zero-length normal
   breaks the hemisphere-sampling basis construction (cross product of a
   zero vector is zero → divide-by-zero → NaN coordinates → trimesh's ray
   intersector throws "Coordinates must not have minimums more than
   maximums"). FIXED: detect near-zero normals (`norm < 0.5`) and replace
   them with a fallback direction before any basis construction happens;
   added a second, cheap defensive floor on the tangent-vector norm
   immediately before its own division, as a final safety net. VERIFIED:
   re-ran the exact previously-crashing request (VY KN5, AO on, realistic
   exclusions) — now succeeds in ~10s and produces a visually sensible
   result (real wheel/window/wing detail, much cleaner than both the old
   silhouette-only AND old fake-shading versions on this same model).

   New dependency introduced: `rtree` (required by trimesh for efficient
   ray-mesh spatial queries — without it, ray casting against a real mesh
   is impractically slow even for the downsampled point count). Added to
   `requirements.txt` AND to `app.py`'s `_check_dependencies()` self-check
   — explicitly checked this time, having been burned once already by
   `matplotlib` missing from both lists in an earlier round. VERIFIED via
   the same fake-missing-import test used for every other dependency
   check: confirms `rtree` specifically is named if missing, clean exit,
   no raw traceback.

## Full regression suite re-run after this round's changes (all pass)

Re-tested via Flask's test client, covering every feature touched this
session in one combined run: Probox basic generate, front_flip override,
4-cut rib mode, Probox with AO on, preferences round-trip, and VY KN5 with
AO + 3-cut rib + a realistic exclusion set (the historically fragile
combination that has caught two separate real bugs across this project
so far). All six passed cleanly.

## Suggested next concrete step

Same pattern as before: this round's work is sandbox-verified (Flask test
client + Linux sandbox) but NOT yet confirmed by the user on their actual
machine. Specifically unverified in real use: the Mac-specific threading
fix (no Mac available to test), the new orientation controls in actual
browser use, the multi-cut slider in actual browser use, AO's real-world
timing/quality on the user's own files, and whether saved global
preferences actually feel right in practice (e.g. does loading a brand
new file with very different proportions look odd with old saved colors —
untested edge case, probably fine, not confirmed). Hand back to the user
for real-world testing; their next bug report or confirmation is the next
concrete input, same as every previous round.

## Real AO bug found via user testing on their actual laptop — fixed

User reported AO took ~5 minutes AND looked wrong — "looks nothing like
AO is completely useless." The screenshot they shared showed sharp banded
contour rings tracing panel shapes (looking like a topographic map), not
smooth shading.

**Root cause diagnosed, not guessed**: checked the actual baked vertex
brightness values directly and found only 5 distinct values in the whole
mesh (38, 63, 127, 191, 255). With the original `n_rays=4` setting,
occlusion can only take 5 possible fractions (0/4..4/4) — contour
extraction was correctly drawing iso-lines at the boundaries between these
5 discrete bands, which is mathematically exactly a topographic-map
pattern. This was the root cause of BOTH complaints at once: low ray
count was chosen specifically to keep the (pure-Python, no embree) ray
casting fast enough, and that same low ray count is what produced the
quantization/banding artifact. Tuning ray count up within the old
architecture would only have traded one problem for the other (more bands
= less banding but slower; the user's 5-minute report was already with
the SLOW setting, so there was no room to increase rays further without
the old approach).

**Real fix, not a parameter tweak**: re-attempted installing `embreex`
(a fast compiled ray-mesh intersection library) — earlier in the project
this had failed/wasn't attempted seriously, but it now installs cleanly
via a prebuilt wheel (`pip install embreex`, confirmed working). trimesh
automatically uses it for `mesh.ray` once installed — no code changes
needed for that part. Measured speedup is dramatic, not marginal: 4000
rays went from 8.77s (pure-Python fallback) to 0.23s; full-mesh AO on the
Probox (351,543 vertices × 32 rays = ~11.2 million rays) completes in
under 4 seconds. This completely removed the NEED for the original voxel-
downsampling + nearest-neighbor-propagation workaround, which is also
exactly what was causing the quantization (downsampling to ~11,000 points
with only 4 rays each was the bottleneck being worked around — fixing the
real bottleneck, embree, made the workaround unnecessary rather than
needing its own fix).

`compute_ambient_occlusion` REWRITTEN: removed voxel downsampling
entirely, computes AO at FULL per-vertex resolution directly, default
`n_rays` raised from 4 to 24 (25 distinct occlusion levels — smooth,
confirmed: 22 distinct brightness values actually observed on the real
Probox render, no more banding). Degenerate-normal handling (the earlier
VY-specific fix, ~30% of that mesh's vertices have near-zero-length
normals) was KEPT — still necessary, unrelated to the embree change.

**New defensive check added**: `_check_embree_active()` / `AOPerformanceError`
— if AO is requested but embree isn't actually active (e.g. embreex
failed to install on some future user's machine), this now fails
IMMEDIATELY with a clear, specific error message telling them to run the
pip install command, rather than silently falling back to the slow
pure-Python path and reproducing the exact bad experience the user just
reported. This was a real gap worth closing explicitly, not just noting.

New dependency: `embreex`, added to `requirements.txt`. NOTE: NOT added
to the hard-required `_check_dependencies()` list in `app.py`, since the
app should still function (silhouette-only, no AO) without it — instead
gated by the AO-specific `_check_embree_active()` check above, which only
fires if/when AO is actually requested.

**VERIFIED**: re-tested both the Probox (visual check: clean smooth
shading, no banding, real door/window/wheel/mirror detail visible) and
the VY KN5 (the historically fragile fragmented mesh — still works
correctly, ~3.3s total, degenerate-normal fix still effective, real wheel/
mirror/wing detail visible with no crash and no banding). Real timing
now: Probox AO computation alone ~5 seconds (was 44s in the previous
version, and the user's own report was ~5 MINUTES — the prior version's
documented "~44s" estimate in this file was apparently not representative
of real-world performance on the user's hardware, worth remembering that
sandbox timings don't always transfer directly).

## Updated suggested next concrete step

Same pattern as every previous round: this fix is sandbox-verified (both
the Probox and the VY KN5, via direct function calls AND through the real
Flask test client) but not yet confirmed on the user's actual machine.
Given this is the SECOND time real-world testing caught something the
sandbox alone did not (first was the VY-specific index-bounds bug, now
this AO quantization issue), it's worth the user explicitly checking: (1)
that `embreex` actually installs cleanly via `python -m pip install -r
requirements.txt` on their Windows machine (no reason to expect it
wouldn't, prebuilt wheels exist for common platforms, but unverified
firsthand), and (2) that the AO output now looks like real shading rather
than contour rings, and (3) real timing on their hardware now that embree
is in use (expect seconds, not minutes — if it's still slow, that's a
new, different problem to investigate, not the same one).
