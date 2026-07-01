# Orthographic Template Generator — Setup & Usage

Turns a 3D car/vehicle model file into a clean black-and-white orthographic
line-art reference sheet (front, back, left, right, top, bottom views, plus
a width-wise cross-section). Runs entirely on your own PC — nothing is
uploaded anywhere.

## Quickest way to start (recommended)

Double-click **`Start App.bat`** in this folder. That's it.

It checks for Python, installs everything the app needs the first time
(later launches skip straight to starting up), then opens the app in your
browser automatically. A window will stay open while the app runs — leave
it open, and close it when you're done to shut the app down. If something
looks wrong, the window will explain what to do in plain language.

If you'd rather do each step yourself (or the launcher hits something it
doesn't handle), the manual steps below do exactly the same thing.

## One-time setup (manual, only needed if not using Start App.bat above)

1. **Install Python**, if you don't already have it: go to
   https://www.python.org/downloads/ , download the Windows installer, run
   it. On the **first screen** of the installer, tick the box at the
   bottom that says **"Add python.exe to PATH"** before clicking Install.
   This is the single most common thing people miss, and it's exactly what
   causes the `'pip' is not recognized` error covered below — if you've
   already installed Python and skipped that box, see the troubleshooting
   section, don't reinstall yet.

2. **Open a command window**: open the folder where these files are saved
   in File Explorer, click once in the address bar at the top (where the
   folder path is shown), type `cmd`, press Enter. A command window opens
   already pointed at the right folder — this matters for step 3.

3. **Install the required libraries** by typing this and pressing Enter:

   ```
   python -m pip install -r requirements.txt
   ```

   (`python -m pip` is used instead of plain `pip` because it's more
   reliable right after a fresh Python install — it only needs `python`
   itself to be found, not a separate `pip` command.)

   This may take a few minutes and print a lot of text. If you see red
   error text partway through, copy it and we'll sort it out — don't worry,
   it's not unusual for one package to need a small fix on a new machine.

## Troubleshooting setup

**`'pip' is not recognized as an internal or external command`**
This means Python isn't on your PATH yet (see step 1 above). Check first:
type `python --version` in the command window.
  - If THAT also says "not recognized": Python isn't installed/found at
    all. Re-run the installer from python.org, and make sure to tick "Add
    python.exe to PATH" on the very first screen this time.
  - If `python --version` DOES show a version number (e.g. `Python
    3.12.3`) but `pip` alone still fails: use `python -m pip install -r
    requirements.txt` instead of plain `pip install ...` — this works
    around it directly, no reinstall needed.

**`'python' is not recognized` too**
Same root cause, different symptom. Re-run the python.org installer,
tick "Add python.exe to PATH" on the first screen. If you've already got
a partial install you're unsure about, it's fine to just run the installer
again — it'll detect the existing install and offer to repair/modify it.

**Something else printed in red during `pip install`**
Copy the exact text (especially the last few lines, which usually say what
actually failed) — that's specific enough to diagnose properly rather than
guess at.

## Running it (every time)

1. Open a command window in the folder where these files are saved (the
   simplest way: open the folder in File Explorer, click the address bar at
   the top, type `cmd`, press Enter — a command window opens already
   pointed at the right folder).

2. Type:

   ```
   python app.py
   ```

3. You'll see a message saying it's running. Open your normal web browser
   (Chrome, Edge, Firefox — whatever you usually use) and go to:

   ```
   http://127.0.0.1:5000
   ```

4. Drop your model file onto the page. Wait for it to read the file (large
   files can take a little while). You'll see a list of all the named parts
   inside the model, all ticked on by default.

5. Pick which views you want, your colors, and click **Generate**.

6. Look at the picture. If something looks wrong — a stray shape, something
   missing, clutter where there shouldn't be any — untick the suspicious
   part(s) in the list and click Generate again. No 3D software needed;
   just compare what you see to what you'd expect a real car to look like.

7. When you're done, close the browser tab and go back to the command
   window, press `Ctrl+C` to stop the program.

## Supported file types

`.obj`, `.dae` (Collada — common for BeamNG mods), `.stl`, `.gltf` / `.glb`,
`.kn5` (Assetto Corsa — converted internally by our own from-scratch
parser).

## If something goes wrong

- **The browser says it can't connect**: make sure the command window still
  shows the program running (it should say "Running on
  http://127.0.0.1:5000") and hasn't shown an error and stopped.
- **Upload fails with an error message**: that's the program telling you
  something specific about the file — copy the exact message, that's the
  starting point for fixing it.
- **The generated image looks like a mess of clutter**: this usually means
  the model's internal mesh quality isn't great (seen this already with
  some Assetto Corsa conversions) — try the part checklist first (untick
  obviously-interior parts: seats, dashboard, roll cage, etc.) before
  assuming something's broken.
