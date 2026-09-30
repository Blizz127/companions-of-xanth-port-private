# Native dev menu work

Releases up to alpha.3 do not contain a dev menu; alpha.4 and later enable it by default for this preview. The shared implementation
contract is `~/release-staging/port-dev-menu/DEV_MENU_SPEC.md` (v1,
2026-09-30). The owner additionally requires Xanth checkpoints to use the
game's own save/restore path, with no host memory pokes.

Xanth uses F12 or Back/View + Start on the same controller to open the menu.
F1-F10 and Backspace remain game inputs while the menu is closed. The menu
is a host overlay: it leaves guest VRAM untouched and keeps simulation running.
Warp and Finish Area use verified user-local saves through the game's own
Restore dialog. No cheats or direct guest-memory writes are included.

## Menu shell

The host-side shell is in `port/src/dev_menu.c`. It is on by default in this
preview. `--no-dev-menu`, `XANTH_DEV_MENU=0` or `dev_menu=0` in the port
config turns it off. XANTH_DEV_MENU overrides CLI choices, which override config;
`--dev-menu` enables it again. `XANTH_CHEATS=0`
hard-disables it, so no filter, overlay or controller handle is subscribed.

- **Opening:** F12, or Back+Start on the same pad: held together in either
  order, or the second press within 300 ms of the first. Both buttons are
  withheld from the game while a chord is still possible; a lone press
  reaches the game as a tap once the window has passed (a lone hold
  delivers one tap). Under Steam Input or InputPlumber the same physical chord
  arrives once per device that mirrors the pad; a chord on another pad
  within 300 ms of the first is that same chord and toggles nothing more
  (before alpha.6 it re-toggled, so the menu opened and closed at once).
- **Navigation:** Up/Down, the D-pad or the left stick (threshold 12000, one
  row per deflection) move the cursor. Enter/A selects, and Esc/B goes back,
  closing the menu at the root. There is no auto-repeat.
- **Pages:** the root is Warp, Finish Area, Cheats, Options. Warp and Finish
  Area list only live-verified checkpoints (below); without local checkpoints
  they show one explanatory line and no selectable rows. Cheats has no
  verified entries, so it shows only that line.
- **Options:** fast-forward on/off (also L3), hold R3 to fast-forward, speed
  2x/4x/8x/max, screenshot (the presented frame with the overlay), fullscreen,
  CRT scanlines and a controls help page. With the menu enabled, the
  `--hotkeys` F10/F11 shortcuts are off and these rows replace them.
- **Input capture:** while the menu is open it consumes keys, mouse buttons
  and pad buttons. A release is consumed exactly when its press was, so input
  the game already saw never sticks. After closing, anything still held from
  the menu must be released before gameplay input passes. A pad that connects
  with buttons held is ignored until it goes neutral.
- **Presentation:** the overlay is drawn on the presented RGBA copy after
  palette expansion. Guest VRAM, the DAC and the game's draw lists are never
  touched, and the simulation keeps running while the menu is open.
- **Harness:** `XANTH_DEV_KEYS="frame:NAME,..."` scripts menu input for tests
  (OPEN, UP, DOWN, ENTER, ESC, HELP, FF, FF_SPEED, SHOT, R3_DOWN, ...),
  and `XANTH_DEV_SHOT_DIR` redirects screenshots. `XANTH_STATE_HASH=1` prints
  an FNV-1a hash of guest RAM and the DAC at exit.

Xanth exception to the shared key map: retail Xanth reads function keys as
game commands (F1 waits), so the menu takes no key the game can receive.
The port forwards F1-F10 to the guest and never forwards F11 or F12, so F12
opens the menu and F1-F10 always reach the game, menu enabled or not. Help,
fast-forward speed, screenshots and (once verified) quick save/load are
Options rows instead of F-keys. Backspace stays with the parser for typing,
so fast-forward is L3 (toggle) and R3 (hold). While the menu is open it
consumes all keyboard input, F-keys included.

Gates on the branch:
- `DevMenuTests` (asset-free, SDL virtual devices) covers default and explicit enable/disable resolution,
  the all-off identity, navigation and empty groups, keyboard/mouse/pad
  capture and drain, the same-pad combo and replays, fast-forward, refusals,
  screenshots, the key script, and held-at-connect/removal hotplug.
- `DevMenuRetailIdentityTest` (asset-gated) runs the real game for 1500
  frames three ways: off, opted in with a scripted walk through every page,
  and hard-disabled. Guest RAM plus DAC hash, cycle count and final frame
  must be identical in all three.

The menu uses the shared hooks:
- `hal_input_set_event_filter` sees every SDL event before the game and the
  host hotkeys do.
- `hal_input_set_pad_button_mask` keeps the game from seeing the pad: the
  whole pad, analog pointer included, while the menu is open or buttons from
  it are still held, and the reserved Back/Start bits while a combo is armed.
- `hal_video_set_overlay` draws the menu layer into the renderer viewport.
  Screenshots composite the same layer onto the presented frame.

## Save/restore recovery evidence

The read-only source audit identified these guest pathways:

| Function | Source | OVL payload offset |
|---|---|---|
| Save/restore dialog | `src/ovl_18949.asm` | `0x4A05` |
| Dialog wrapper | `src/ovl_20559.c` | `0x504F` |
| Save writer, opens `wb` | `src/ovl_15682.asm` | `0x3D42` |
| Restore reader, opens `rb` | `src/ovl_16185.asm` | `0x3F39` |
| Startup restore prompt | `src/ovl_14576.asm` | `0x38F0` |

The dialog selects Save Game for argument zero and Restore Game for a nonzero
argument. Its EXE thunk is at code offset `0x2126B`. Calling these routines
requires their RTLink residency and retail calling context; identifying the
routines does not justify a direct host far-call.

`DS:18F6` counts save-list entries, `DS:189A` gates the startup restore
prompt, and `DS:18A2`/`DS:18A3` describe the undo buffer/result. None is a
proven field or modal-state predicate. Likewise, `vm.waiting_for_input` can
be true on the title screen and in dialogues.

Existing local anchors are candidates for a restore sweep. Startup restore
traces do not prove that a live field-menu restore is safe for every anchor.
The existing save/load round-trip artifacts are both 17,972 bytes and differ
only at label offsets 0–3. That proves a specific round trip, not every
checkpoint. Startup restore and mid-field restore use different UI paths.

## Safe-state observer and restore path (M2/M3 findings)

`guest_state.c` classifies the game's own input state (see
`docs/GUEST_UI_SEMANTICS.md` section 6). Warps and quick load will act only
on `field-idle`, which means the main loop's verified `get_event` call just
returned an idle event, the synthetic queue is empty, and no guard is set.

Live runs from the end-of-opening save show that the retail Restore dialog
can be driven with ordinary input, the same input a player types:
- **R** (or F6) opens "Restore Game" while the game is field-idle. It lists
  the saves in the save directory and has Ok, Delete and Cancel buttons.
  The observer reads `busy-modal` at 317D:0764 while it is open.
- **Down** moves the list highlight, and the name box mirrors the selected
  entry.
- **Enter** restores the selected entry, and the observer returns to
  `field-idle`.
- **Esc** cancels, also back to `field-idle`.
- **F5 or S** opens "Save Game". A typed label plus Enter makes the game
  write the next free slot file itself (XANTH001.SAV in the probe).

## Warp and Finish Area (M3/M4)

Warps use the game's own Restore dialog and make no memory writes:
1. Each checkpoint is a save the game's own Save dialog wrote at a point on
   the published route. `tools/xanth_dev_checkpoints.py` builds them locally
   from the player's data with `tool_vmboot`, into
   `$XDG_DATA_HOME/xanth-port/dev-checkpoints` (or
   `~/.local/share/xanth-port/dev-checkpoints`; directory 0700, files 0600).
   They are never committed or packaged. `checkpoints.txt` records each
   save's SHA-256 and its fingerprint (room `DS:0256`, score `DS:0264`,
   inventory count `DS:69FC`).
2. The menu copies the save into the lowest free slot (`XANTH000`-`XANTH099`).
3. On `field-idle` it types R. Only while the observer reports the Restore
   dialog (`busy-modal`) does it type the entry's label, which selects it,
   then Enter.
4. It waits for `field-idle` again, removes the temporary slot and reports
   "Warped to <spot>". A timeout, or the dialog closing early, stops the
   warp with a "Warp refused"/"Warp failed" toast and removes the slot; the
   menu presses no further keys.

The recorded SHA-256 must match both the local checkpoint and its temporary
copy; changed saves are refused. The temporary slot is created exclusively,
so an existing player save is never overwritten. Player inputs are captured
throughout the Restore macro. Shutdown removes an unfinished temporary slot.


`tools/xanth_dev_warp_sweep.py` verifies every checkpoint live in
`xanth_port`'s own real-time loop from a fresh boot, driving the menu with
its harness keys. A target passes when the warp reports arrival, never a
refusal, the guest is `field-idle` at exit, no temporary slot is left, the
room picture and inventory match the checkpoint's reference frame (at most
3% of pixels differ, for animation), and room and score match. Passing
entries get `verified=1`; only those are listed in the menu.

Finish Area > Finish Current Step reads the live fingerprint, finds the
route step it lies in (start score <= score < end score, or the exact start
point for steps that score nothing), and warps to that step's end. Steps
whose fingerprints cannot be told apart are not offered. `--steps` in the
sweep verifies it: warp to step N-1's end, then Finish Current Step must
land exactly on step N's fingerprint.

A route point reached from one of the game's own recorded anchor saves
(step name `*`, a chain start) is a warp spot and the start of the next
step, never a step itself: Finish Current Step skips over it, Finish Area
ignores it, and no step is offered for the unrecorded play between the
previous step's end and the anchor. Steps missing from the list (not built,
or not verified) are skipped; the steps after them are still found.

Results on 2026-09-30 (headless, local checkpoints): warp sweep VERIFIED 8/8
(bedroom at start, kitchen steps 1-3, package received, opening finished,
cavern with Nada, second cavern door), and Finish Current Step VERIFIED 7/7.
The alpha.5 builder lists 23 warp spots and 18 finish-step entries through
Isthmus, the Void, Earth and the Region of Fire; every entry past the cavern
is offered only after your own local sweep verifies it.

After any warp or step finish, the first new game save shows the toast "Dev
menu was used: this save may not match a normal playthrough" and appends a
line to `xanth-dev-menu.log` in the save directory.

## Remaining gates

Verified checkpoints cover the opening and the first Xanth cavern. The route
builders now reach the Region of Fire; each of those entries is listed only
after a passing local sweep.
Cheats and quick save/load are not offered: neither has a verified
game-state path yet.

A joystick SDL has no controller mapping for, but that is shaped like an
Xbox pad (eight or more buttons), is given the standard
Linux xpad layout at connect time (raw-joystick fallback; logged as
`[DEV_MENU] raw joystick fallback`). Its buttons 6 and 7 are Back and
Start. Smaller devices are ignored and logged once.

Steam's virtual X-Box 360 pad is only visible to SDL 2.28+ when
`SDL_GAMECONTROLLER_ALLOW_STEAM_VIRTUAL_GAMEPAD=1` is set before SDL starts;
Steam sets it only for titles it launches itself. The port sets it (and
logs the previous value) so a launcher-started run under gamescope Game
Mode, where the physical pad's device node is root-only, still has a pad.

The controller backend now has virtual-device regression coverage for Steam
hint sanitation, active-pad selection, removal and neutral reconnects. The
same-pad menu combo, menu input drain, menu navigation and all-off identity
have automated coverage. Physical handheld testing is separate and
has not been performed.

HD presentation fixes and font verification drafts remain queued behind the
dev-menu phase. They do not supply a higher-resolution art or font pack.

## Building your local checkpoint list

The Linux package includes Python tools, authored route traces and
`tools/tool_vmboot`. It contains no checkpoint saves or game data. From the
extracted package directory, with Python 3 installed, run:

```sh
python3 tools/xanth_dev_checkpoints.py --vmboot tools/tool_vmboot --data /path/to/your/data
python3 tools/xanth_dev_warp_sweep.py --port ./xanth_port --data /path/to/your/data
python3 tools/xanth_dev_warp_sweep.py --port ./xanth_port --data /path/to/your/data --steps
./launch.sh /path/to/your/data --dev-menu
```

Building replays the verified route and can take substantial time. The sweep
runs headlessly. Up to 23 warp spots and 18 Finish Current Step entries become
available after successful local verification. Before that, those menu pages
explain that checkpoints are missing. Saves, reference frames and verification
logs remain in your local data directory and must not be redistributed.
