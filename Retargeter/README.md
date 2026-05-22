# Retargeter

MotionBuilder HumanIK-based retargeting hub for moving animation between
3ds Max, Maya, and Unreal Engine 5 through FBX. MotionBuilder hosts the
source and target HumanIK characters; the script imports source FBX
animations, plots them onto the target rig, and exports one FBX per take
ready for the destination DCC.

## Requirements

- MotionBuilder 2020 or newer
  - MotionBuilder 2025 and 2026 ship PySide6 / Python 3.11
  - MotionBuilder 2020 - 2024 ship PySide2 / Python 3.7+
  - The UI auto-detects which Qt binding is available, no manual configuration
    is needed
- HumanIK Characters are *already characterized* for both the Source and the
  Target inside a "setting" FBX file (see "Setting file" below)

## Folder layout

```
Retargeter/
  retargeter.py               # entry point you exec from the Python Editor
  Retargeter_Start.py         # one-shot launcher (forwards to retargeter.py)
  __init__.py
  core/
    scene_utils.py            # HumanIK discovery + validation + slot/finger helpers
    fbx_io.py                 # animation-only import, take-by-take export, metadata
    take_manager.py           # take creation, naming, conflict handling
    retarget_engine.py        # Character Input + Plot to Skeleton + apply_hik_options
    root_motion.py            # Keep / Strip / Extract per take
    pipeline.py               # orchestration: hooks, dry-run, logging, isolation
    logger.py                 # tee logger (console + file + UI)
    skeleton_features.py      # source/target shape measurements (height, widths, fingers, ...)
    option_advisor.py         # rule-based recommender for PlotConfig + HIK options
    feedback_log.py           # append-only JSONL of per-take options/features/labels
    quality_metrics.py        # opt-in foot/wrist/hips/knee/shoulder quality scores
  ui/
    main_panel.py             # PySide2/6 main panel (split layout, toolbar, action bar)
    options_dialog.py         # modeless options dialog + Auto recommend / reasons
    take_table.py             # per-take checkboxes + root motion + Quality (Good/Bad) + status
    file_list.py              # drag&drop FBX list with context menu
    log_view.py               # colored severity-aware log viewer
    _qt_helpers.py
  config/
    default_settings.json     # default plot rate, fbx version, naming, hik options, etc.
    hooks.py                  # user-extensible pre/post hook slots
```

External (separate venv, optional - stage 2):

```
tools/
  retargeter_ml/              # future ML trainer for the option recommender
    README.md                 # placeholder, see "Auto option recommender" below
```

## Setup the "setting" FBX file (per case)

The script assumes you have created one MotionBuilder scene per
(source engine -> target engine) case, for example:

- `Max_to_UE5.fbx` -- a 3ds Max biped is Characterized as the Source,
  the UE5 SK_Mannequin skeleton as the Target.
- `Maya_to_UE5.fbx` -- a Maya HumanIK skeleton as Source, UE5 SK_Mannequin
  as Target.
- `UE5_to_Maya.fbx` -- inverse direction.

Procedure for one setting file (do this once per case):

1. Open MotionBuilder.
2. Drag the **Source engine's reference T-pose FBX** into the viewport.
   This brings in the engine's skeleton in T-pose.
3. From the Asset Browser, drag a `Character` onto the source skeleton's hips
   and characterize it (HumanIK -> Characterize -> Biped). Verify all the
   slots map correctly in the HumanIK panel. Rename the character to
   something descriptive like `SRC_Max_Biped`.
4. Repeat with the **Target engine's reference T-pose FBX**. Characterize
   and name it like `TGT_UE5_Mannequin`.
5. Verify both characters are valid (green padlock icon in HumanIK).
6. Save the scene as the setting file, e.g. `Max_to_UE5.fbx`.

When the script runs against this setting file, the Source HumanIK character
is already wired to the Source skeleton bones. Every imported source FBX
must have bone names matching those Source bones so the animation curves
bind during merge.

### Namespaces (required when source and target share bone names)

UE4 and UE5 mannequins, UE5 Manny and Quinn, and many Maya/Max biped pairs
share a number of bone short names (`hand_l`, `foot_l`, `pelvis`, ...). When
both rigs sit in the same setting file with bare names, MotionBuilder cannot
tell them apart and a single FileMerge can collapse one rig onto the other.
The retargeter handles this by relying on namespaces:

1. After characterizing both rigs in the setting file, give **each rig** its
   own namespace via the Navigator: right-click the rig's root in the Scene
   Browser and choose **Add Namespace** (e.g. `UE4`, `UE5`).
2. Verify that HumanIK characters still validate after the rename
   (the green padlock should remain).
3. Save the setting file.

You do **not** need to namespace the source FBX files you drop on the
panel - they ship as bare `Hips`, `pelvis`, etc. The script reads the
source character's namespace at run time and asks `FBFbxOptions.NamespaceList`
to prefix every loaded object with it, so a bare `pelvis` arrives in the
scene as `UE4:pelvis` and binds straight onto the source rig that was
already there.

Two safety nets back this up:

- **Target rig protection** (`protect_target_transforms`, on by default)
  snapshots every target-character bone's T/R/S immediately before each
  merge and restores any bone the merge mutated. This catches the failure
  mode where an incoming FBX bone short name still collides with a
  *target* bone (the symptom looks like target bones jumping to (0,0,0)
  with the rig visible in the Navigator but invisible in the viewport).
- **Duplicate cleanup** (`cleanup_duplicate_bones`, on by default) catches
  the symmetric failure mode where the FBX hierarchy differs from the
  source character's hierarchy enough that MotionBuilder still appends
  `" <N>"` to clashing bones; the cleanup pass transfers their animation
  back onto the source rig and deletes the leftovers.

If you ever see target bones snapping to (0,0,0) right after a merge even
with both safety nets on, try unticking **Import base model transforms with
animation** in **Option > Settings... > Retargeting** (this flips
`FBFbxOptions.BaseModelsAnimation = False` for the merge so only animation
curves arrive, never base T-pose values).

#### Why you may briefly see `UE6:`, `UE7:` namespaces in the Navigator

`FBFbxOptions.NamespaceList = "UE5"` does **not** mean "merge into the
existing `UE5:` namespace"; MotionBuilder's documented behaviour is to
*increment* the namespace if one with that name already exists, isolating
the incoming objects into a fresh `UE6:`, then `UE7:`, and so on for each
subsequent import. The retargeter expects this: every merge is followed by
a cleanup pass that walks the new bones, transfers their animation curves
onto the matching source-character bone, and deletes the temporary
`UE6:` / `UE7:` subtree. When the cleanup finishes the Navigator only
shows the original `UE5:*` rig again, now carrying the imported animation.

The pipeline log surfaces this clearly:

```
namespace remap APPLIED: incoming objects prefixed with 'UE5:'
namespace was incremented to 'UE6:' ('UE5' already exists in scene); cleanup will reconcile incoming bones onto the source rig.
cleanup: transferred animation on 88 bone(s), deleted 89 model(s), skipped 0.
cleanup: transferred from {UE6: 88} into source rig.
```

If you ever see a residual `UE6:`/`UE7:` namespace *after* a successful
Import & Plot, that means the cleanup pass skipped or failed - inspect
the log for `cleanup: ...skipped N` or any `cleanup: ... failed` lines.

## Running

There are three ways to launch the panel from inside MotionBuilder. Pick
whichever fits your workflow.

### Option A: Menu auto-install (recommended)

Use the repo-wide aggregate installer at the project root - it
registers every shipped tool (Retargeter, TPoseAligner, ...) under a
single shared **Tools** menu in MotionBuilder. See the root
[`../README.md`](../README.md) "Quick install" section for the
PowerShell symlink one-liner.

After install, launch via **Tools -> Retargeter -> Open Retargeter
Panel...**.

### Option B: One-shot launcher (good for development)

From MotionBuilder's Python Editor:

```python
exec(open(r"C:\path\to\MotionBuilder-Script\Retargeter\Retargeter_Start.py").read())
```

`Retargeter_Start.py` forwards to `retargeter.py` next to it; no absolute
paths inside the script - move the project freely.

### Option C: Direct exec (no launcher, no menu)

```python
exec(open(r"C:\path\to\MotionBuilder-Script\Retargeter\retargeter.py").read())
```

Or, if the project's parent folder is already on `sys.path`:

```python
from Retargeter import show_panel
show_panel()
```

The panel can be left open while you swap setting files between runs.

## Panel anatomy

```
Menu (File / Option / Run / Help)
Toolbar (Engine preset, Settings, Open output, Refresh, ready dot)
+-------------------------+-----------------------------------+
| Setup (left)            | Run & Review (right)              |
|  HumanIK Characters     |   Takes table (filter / sort /    |
|   src / arrow / tgt     |     status colors / context menu) |
|  Source FBX files       |   Combined action row:            |
|   (drag & drop list)    |    [Check][Uncheck][Apply..]      |
|  Output folder          |    ... [Dry-run][Import & Plot]   |
|                         |    [Export][Run All*][Cancel]     |
|                         |   Progress bar (full width)       |
|                         |   Log panel (Clear button)        |
+-------------------------+-----------------------------------+
```

`*` = primary action; **Cancel** only appears while a run is in progress.
Current take name is rendered inside the progress bar text.

## Panel workflow

1. Open the setting FBX for the case you want.
2. Run the script -- the panel appears.
3. Click **Refresh** (toolbar or inline arrow next to the Source combo)
   and pick the Source / Target HumanIK characters. The little arrow
   between them swaps the two.
4. Populate the Source FBX list. Three options:
   - drag FBX files **or** folders straight onto the list from the OS
     file manager;
   - **Add files...** / **Add folder...** buttons (or **File** menu);
   - paste paths via custom hooks if you have a pipeline.
5. (Optional) **Option > Settings...** to tweak per-run options
   (plot rate, root motion, naming, FBX version, ...). The dialog is
   modeless: leave it open while you run.
6. Set the **Output folder** in the left column.
7. **Dry-run** to preview which takes / output paths would be produced
   without touching the scene.
8. **Run All** to do import, plot, and export back-to-back.
   Or split it: **Import & Plot** first, review the take table, then
   **Export Selected**.
9. While running: the progress bar shows the current take name; the log
   panel colours error/warn lines; **Cancel** stops at the next safe
   take boundary (best responsiveness during Export).
10. On success a confirmation box offers **Open output folder**.

## Take table tips

- Right-click a row for **Reveal source in file explorer**,
  **Copy source path**, **Copy take name**, **Remove row**.
- The **Filter** combo at the top of the table shows only rows with a
  given status (e.g. just the `failed` ones after a noisy run).
- Failed rows are tinted red, OK rows tinted green, so you can scan a
  long table at a glance.

## Keyboard shortcuts

- `Ctrl+O` -- Add FBX files...
- `Ctrl+Shift+O` -- Add folder...
- `Delete` -- Remove selected files
- `Ctrl+L` -- Clear file list
- `Ctrl+,` -- Open the Options dialog
- `Ctrl+R` -- Run All
- `Ctrl+P` -- Import & Plot only
- `Ctrl+E` -- Export selected takes
- `Ctrl+D` -- Dry-run
- `Esc` -- Cancel running
- `F5` -- Refresh HumanIK character list
- `Ctrl+W` -- Close panel

## Presets

`Option > Settings... > Presets` saves the entire option set under a
name. Presets are stored per-user in `QSettings` (Windows registry under
``HKCU\\Software\\Retargeter`` or the platform equivalent), not in the
repo, so each operator keeps their own list. Use **Save Current As...**
to capture the current values, then **Load** to switch between
``UE5_to_Maya`` / ``Max_to_UE5`` / experimental tweaks instantly.

## State persistence

The panel restores the following on next launch (per user, via
`QSettings`):

- window geometry and splitter ratios
- output folder
- engine preset
- last opened "Add files..." and "Add folder..." directories

## Options reference

- **Plot rate (fps)** -- frame rate used for `FBPlotOptions.PlotPeriod`.
  30 fps for cinematics, 60+ for combat.
- **Plot translation** -- bake Hips XYZ into the skeleton. Disable only if
  you have a downstream system that drives the Hips translation separately.
- **Constant key reduction** -- removes keys that hold identical values;
  smaller FBX, but can mask sub-frame snaps.
- **HumanIK Match Source** -- when on, MoBu compensates for differences
  between the source and target reference poses. Recommended.
- **Remove existing takes before import** -- "clean slate" mode; useful
  when batching repeatedly.
- **Inject metadata into exported FBX** -- writes a custom `RetargetInfo`
  user property on the Hips bone of the exported FBX. Readable in Maya,
  Max, and UE5. Contains `source_path`, `source_take`, `target_character`,
  `plot_rate`, `root_motion_mode`, `tool_version`, `author`, `timestamp`.
- **Default root motion** -- `keep`, `strip`, or `extract`. See the next
  section.
- **Take prefix / suffix** -- string decoration applied to the filename's
  stem when naming the new take. Example: prefix `UE5_` and a source file
  `idle_loop.fbx` produces take `UE5_idle_loop`.
- **Filename template** -- output filename pattern, e.g. `{take}.fbx`. The
  `{take}` placeholder receives the (prefixed/suffixed) take name.
- **On file conflict** -- `increment` appends `_01`, `_02`, etc.;
  `overwrite` clobbers; `skip` leaves the file alone.
- **FBX version** -- target FBX SDK version of the exported file.
- **ASCII FBX** -- text FBX instead of the default binary.
- **Engine preset** (toolbar) -- informational tag stored in the metadata;
  no geometric transform applied yet (reserved for future engine-specific
  axis / unit handling). Lives on the top toolbar rather than in the
  Options dialog because it is the single most frequently changed control.

## Root motion modes

- **Keep** -- output Hips XYZ exactly as the plot produced them. Use when
  the destination engine drives movement from root motion or when the
  authoring intent is "go where the animation says".
- **Strip** -- collapse Hips X and Z translation to the first-frame value
  for every take, producing in-place animation. Hips Y is preserved so
  jumps / crouches still read. Use when the engine's animation blueprint
  handles movement and the animation must not push the actor around.
- **Extract** -- transfer Hips horizontal motion onto the HumanIK
  `Reference` bone (or the Hips parent) and zero out Hips horizontal
  translation. This is the canonical UE5 root motion convention: the
  `root` bone moves through the world while the pelvis stays above it.
  If no carrier bone exists the script falls back to **Strip** and logs
  the substitution.

The default mode is applied to every newly created take; override on
specific rows in the take table for mixed batches (idles use Strip, runs
use Keep, etc.).

## User hooks

Edit `Retargeter/config/hooks.py` to plug in custom behaviour:

- `pre_import(fbx_path)`
- `post_import(fbx_path, take)`
- `pre_plot(target_character, source_character, take)`
- `post_plot(target_character, take)`
- `pre_export(take, target_character, out_path, metadata)`
- `post_export(take, target_character, out_path, metadata)`

Hooks are best-effort: an exception in a hook is logged and the pipeline
continues with the next take.

## Known issues / future work

- **Engine presets are informational only.** The metadata is tagged but no
  axis / unit transform is applied at export. Max scenes authored in Z-up
  will still arrive Z-up. Use MotionBuilder's File > Options or your
  engine's FBX import settings to handle axis remap for now.
- **Extract root motion** assumes the target character has a HumanIK
  `Reference` slot or a parent bone above Hips. UE5 SK_Mannequin has
  `root` as the parent of `pelvis` so the fall-back to "parent of hips"
  works there.
- **Multiple takes per FBX**: if a source FBX already contains several
  takes they are all imported and renamed `<stem>`, `<stem>_p01`,
  `<stem>_p02`, ... The pipeline plots each one independently.
- **FBX version selection**: the combo box's value is stored in the
  metadata but the actual SDK version written is whatever the running
  MotionBuilder defaults to. To force a specific version, use the
  `FBFbxManager` API in a `pre_export` hook.

## Auto option recommender (rule-based, with a path to ML)

Picking the right combination of `match_source`, `HIKForceActorSpace`,
`HIKScaleCompensation`, `HIKTopSpineCorrection`, `HIKFingerPropagation` and
PlotConfig is mostly a function of how the source and target rigs differ in
shape (height, shoulder width, spine subdivision, finger count, T-pose vs
A-pose, ...). The Retargeter now ships a deterministic rule-based advisor
that reads those shape features off the live HumanIK characters and proposes
option values, with a documented reason for every change.

How to use it:

1. Set Source and Target on the panel.
2. Open `Settings...` and go to the `Retargeting` page.
3. Click `Auto recommend from characters`. Any widget the advisor touched
   gets a yellow background and the `Recommendation reasons` panel lists why.
4. Disagree with one? Just click the widget; the yellow tint clears for that
   one. Or click `Undo Auto` to revert everything back to your pre-Auto
   snapshot.

The advisor lives in `Retargeter/core/option_advisor.py`. Rules are a single
declarative list (`RULES`); adding a new heuristic is one function entry. The
HIK 4-options surface (`apply_hik_options` in `retarget_engine.py`) resolves
each option through (a) direct character attribute, (b) PropertyList alias
table, (c) normalised PropertyList scan, (d) skip with a log warning.

### Feedback loop

Every plotted take appends one JSON line capturing:

- the shape features the advisor saw,
- the option values actually used + whether each HIK option was applied,
- (optional) quality metrics if `Compute post-plot quality metrics` is on in
  the Plot page,
- the operator's Good/Bad label later, written by the panel's `Save feedback`
  action (only takes with a `good` or `bad` quality entry are saved).

The same line is written to **two** places so the data is both auditable
per project and accumulated across projects for training:

- **Project log** -- `{out_dir}/_retarget_feedback.jsonl`. Written only when
  an output folder is set. Use this to ask "what options were used for this
  specific batch?". Travels with the project folder; safe to delete.
- **Central log** -- `~/.retargeter/feedback.jsonl` by default (or whatever
  `$RETARGETER_FEEDBACK_PATH` points to). Append-forever, never written
  outside this single file. This is the file stage-2 training reads. Point
  the env var at a team-shared drive to pool labels across operators:

  ```
  setx RETARGETER_FEEDBACK_PATH "\\share\team\retargeter\feedback.jsonl"
  ```

Both writes are best-effort: a temporarily unreachable network share or a
read-only project folder is silently skipped so the plot loop never stops
because of feedback I/O. At least one of the two sinks usually succeeds.

### Replacing the rules with a learned model

The `Recommender` protocol is in place so a future `ModelBackedRecommender`
can drop in without UI changes. Training will live in
`tools/retargeter_ml/` (its own Python venv, separate from MotionBuilder):

1. Read JSONL records into pandas.
2. Use the operator's `good`/`bad` labels as the primary supervision; fall
   back to a weighted combination of `quality_metrics` for unlabelled takes.
3. Train a multi-output classifier for the boolean options and a regressor
   for `plot_rate` / `plot_translation` (XGBoost or a small MLP).
4. Save `Retargeter/data/option_recommender.joblib`.
5. The MoBu-side `ModelBackedRecommender` calls the model via a `subprocess`
   that runs inside the trainer venv; failures (model missing, timeout,
   incompatible features) fall back to the rule-based advisor automatically
   and surface that fact in the reason panel.

## Programmatic API

If you'd rather drive the pipeline from a script (e.g. CI / batch job),
skip the UI entirely:

```python
from Retargeter.core.pipeline import RunConfig, run
from Retargeter.core.retarget_engine import PlotConfig
from Retargeter.core.fbx_io import ExportConfig

config = RunConfig(
    source_character_name="SRC_Max_Biped",
    target_character_name="TGT_UE5_Mannequin",
    fbx_files=[r"D:\anims\walk.fbx", r"D:\anims\run.fbx"],
    out_dir=r"D:\out",
    plot=PlotConfig(plot_rate=30),
    export=ExportConfig(fbx_version="FBX201800"),
    default_root_motion="strip",
)
report = run(config)
print("OK" if report.ok else "FAILED", "->", len(report.results), "takes")
```
