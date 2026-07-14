# HKS Lua Editor
A local desktop tool that helps you build, inspect, and edit enemy AI combos in
Sekiro behavior (HKS `.lua`) files — without hand-editing raw Lua. You design a
combo as an ordered sequence of animation steps with optional random/state
branches, and the tool generates the correct Lua and splices it straight back
into your target file (with a backup).

It handles three combo families found in a real behavior file:
- **Act** — `Goal.ActNN`, the default-state combos picked in `Goal.Activate`.
- **Interrupt** — the `elseif interruptEffectIdentifier == <id>` reaction chain
  ("kick combos"), including the special-effect registration.
- **Kengeki** — `Goal.KengekiNN` sword-attack moves (and read-only view of the
  `Goal.Kengeki_Activate` weight selector).

## Before using this tool
### Requirements
- Python 3.10+
- Install dependencies: `pip install -r requirements.txt` (PySide6 + pytest)

### Running
- `python app.py`
- Drop an image at `assets/icon.png` (or `.ico`) if you want a custom app icon;
  it is picked up automatically.

### Reference file
- `710300_battle.lua` is a real enemy AI (HKS) file used as the ground-truth
  reference for the parsing/generation logic. You can also load your own `.lua`.

### Helpful background
- A behavior file registers a combo in three places: the `Goal.ActNN` /
  `Goal.KengekiNN` function itself, a `REGIST_FUNC` line, and (optionally) a
  `SetCoolTime` cooldown line. Special-effect–triggered combos also need an
  `AddObserveSpecialEffectAttribute` registration in `Goal.Activate`. The tool
  keeps all of these in sync for you.

## Usage
1. **Load a file** — File → *Load .lua…* (Ctrl+O). Every `Goal.ActNN`,
   `Goal.KengekiNN`, interrupt branch, and the `Kengeki_Activate` selector
   becomes a selectable combo in the dropdown. Or start fresh with *New*
   (Ctrl+N): a dialog asks for the combo's name, trigger type, and id — if that
   Act/Kengeki/special-effect already exists (in the loaded file or the open
   list) it is blocked so you can't accidentally overwrite it. The trigger
   type/id are fixed after creation (the *Name* stays editable); use *Delete
   combo* and re-create to change them.
2. **Pick / set the trigger** — choose the trigger type (`act_entry`,
   `special_effect`, or `kengeki_move`) and its id (the Act number, the
   special-effect id, or the Kengeki number).
3. **Build the combo in the tree** — each row is one `AddSubGoal` step
   (goal type, anim id, priority, distance, extra args), editable inline like a
   spreadsheet. Use *Add step* / *Add branch* / *Add elseif* to shape the flow.
   - **Move steps** with Alt+↑ / Alt+↓ — this also nests a step into, or pops it
     out of, an `if` / `elseif` / `else` body.
   - Multi-select several steps and move them together.
   - **Branches** support compound conditions with groups and mixed `and`/`or`
     (e.g. `(A or B) and C`), random rolls, state checks, ninsatsu counts, and
     `HasSpecialEffectId` checks.
4. **Import from DS Animation Studio** — File → *Import from DSAS…*, paste a
   list like:
   ```
   EnemyComboAtk 3000
   EnemyComboAtk 3001
   EnemyComboAtk 3002
   ```
   The steps are appended to the current combo (first step becomes the spin if
   the combo has none yet). *Export to DSAS…* does the reverse.
5. **Check the output** — the generated Lua updates live, with a ladder-style
   diagram of the combo so you can sanity-check the branch logic.
6. **Write it back** — File → *Write to file…* (Ctrl+S). The tool does a
   **targeted splice** (it only touches the region for this combo; the rest of
   the file stays byte-for-byte identical) and makes a non-clobbering `.bak`
   backup first.

## IMPORTANT
### Registering a new Act / Kengeki
- When you *Write to file* an Act or Kengeki that does **not** yet exist in the
  file, the tool also:
  - inserts the `REGIST_FUNC` line (`local1[N] = REGIST_FUNC(arg1, arg2,
    arg0.ActNN)` / `local2[N] = ... arg0.KengekiNN`) in numeric order, and
  - optionally inserts a cooldown line
    (`act[N]/kengeki[N] = SetCoolTime(arg1, arg2, <spin anim>, <seconds>, ...)`)
    — it prompts you for the seconds; press *Cancel* to skip the cooldown.
- The `<spin anim>` is taken from the combo's first `ComboAttackTunableSpin`
  step. Table/variable names (`local1`, `act`, …) are detected from the file so
  it also works on other enemies that name them differently.
- Replacing an Act/Kengeki that **already exists** only swaps the function body;
  it does not touch the existing REGIST / cooldown lines.

### Removing a combo
- *Delete combo* (button, or Edit → *Delete combo*) removes the current combo
  from the dropdown/memory only — the `.lua` file is not touched.
- File → *Remove from file…* deletes the currently selected combo:
  - an Act/Kengeki → its function **and** its `REGIST_FUNC` and `SetCoolTime`
    lines, and
  - an interrupt branch → the `elseif` block **and** its special-effect
    registration (both `TARGET_SELF` and `TARGET_ENE_0`).
- File → *Remove special effect…* unregisters a special-effect id on its own.
- Every remove asks for confirmation and makes a `.bak` backup first.

## After running this tool
- Loading a `.lua` you have never edited is safe — nothing is written until you
  explicitly *Write to file*.
- The tool always backs up to a **non-clobbering** `.bak` (an existing backup is
  never overwritten; a timestamped/numbered name is used instead). If something
  looks wrong, restore from the `.bak` next to your file.
- The parser is *tolerant*: constructs it doesn't model (some `local`-only
  `if` blocks, deeply nested conditions) are skipped and reported as warnings
  rather than silently dropped. This is exactly why the writer splices only the
  edited region instead of regenerating the whole file.
- Kengeki weight selector (`Goal.Kengeki_Activate`) is currently **view-only**.

## Development
- Core logic (`models.py`, `generator.py`, `parser.py`, `visualizer.py`,
  `writer.py`, `dsas.py`) is fully UI-agnostic — the PySide6 UI in `ui/` can be
  swapped without touching it.
- Run the tests: `python -m pytest tests/ -q`

## Things to add in future updates
- Building the `Kengeki_Activate` weight selector from the UI (not just viewing).
- Undo/redo coverage for every editing action.
- Drag-and-drop reordering in the tree (currently Alt+↑/↓).
- Editing on non-Windows platforms (a few helpers assume Windows).
