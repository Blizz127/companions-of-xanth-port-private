#!/usr/bin/env python3
"""Build dev-menu warp checkpoints from the player's own game data.

Each checkpoint replays part of the published route under tool_vmboot
(optionally resuming from an earlier checkpoint through the game's own
startup Restore prompt), then saves with the game's own Save dialog under a
typeable label, exactly as a player would: F5, the label, Enter. The save
the game writes is copied to the checkpoint directory as <id>.SAV together
with a reference screenshot, and listed in checkpoints.txt as unverified.
tools/xanth_dev_warp_sweep.py then warps to each one through the dev menu
and marks it verified only if the game accepts it.

Everything is written to a user-only directory (default
~/.local/share/xanth-port/dev-checkpoints, or XANTH_DEV_CHECKPOINTS); it is
derived from the player's game and must never be committed or shipped.

Later route segments resume from the walkthrough's recorded anchor saves
(build/anchor_*, written by the game's own Save dialog and pinned by
SHA-256 in tests/test_walkthrough_*.py). A checkpoint based on one is built
only when the anchor matches its pin; pass --anchors DIR if they live
elsewhere. Such a checkpoint's warp spot is a chain start (step name "*"):
the next step starts there, but progress between the previous step's end
and the anchor is not on a recorded route, so no step is offered for it.

  xanth_dev_checkpoints.py --vmboot build/tool_vmboot [--data DIR] [--out DIR]
                           [--anchors DIR] [--only ID ...]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACES = ROOT / "tests" / "traces"

# Recorded walkthrough anchors: the game's own saves, pinned by the tests.
ANCHORS = {
    "anchor_isthmus": "8d975715f9a2cb9f6ba00d671fd178399d56829224b6bb8969b8c5e1b8e03157",
    "anchor_unmask": "29dd6466242c877278c6d0e8f851ea70dfe95988d3b0db6a879988394d59812b",
    # Segment 14's own output (the 275-point Ironwood Tree save), which
    # segments 15-19 resume from; pinned by tests/test_walkthrough_fire.py.
    "agent_dungeon_test": "901998237900d1665a78bfcc42cb0f067045c49806edc336efbed7bd062647df",
}
CHAIN_START = "*"

# Several segments end by reading the score panel, and the game will not open
# its Save dialog over it. Dismiss it with its checkmark, as the next segment
# does (walkthrough_09_pail.xit, measured on wt8_score.bmp).
DISMISS_SCORE = ["run 20", "move 165 123", "run 5", "click left", "run 30", "move 305 196", "run 15"]
# Segments that stop inside a conversation: advance it with Enter, as the next
# segment does (walkthrough_15/16/18). A restored save must be field-idle.
DISMISS_DIALOGUE = ["key 13 28", "run 60", "move 305 196", "run 15"]

# id, label, region, area, spot, base (None: cold boot; a checkpoint id; or
# "anchor:<name>"), route lines, step (order, name) or None. Labels use only
# a-z/0-9/space: the warp types them into the Restore dialog. A step
# checkpoint is the game's state at the END of that step of the published
# route; step names are our own short descriptions.
CHECKPOINTS = [
    ("mundania-bedroom", "dw bedroom", "Mundania", "Your house", "Bedroom (start of game)",
     None, ["include lib/boot.xit"], None),
    ("mundania-step1", "dw step one", "Mundania", "Your house", "Kitchen (first visit)",
     None, ["include walkthrough_01_mundania.xit"], (1, "Find the kitchen")),
    ("mundania-step2", "dw step two", "Mundania", "Your house", "After the phone call",
     None, ["include walkthrough_02_phone.xit",
            # The route ends with the conversation open; dismiss it as
            # walkthrough_03 does (checkmark at 125,134) before saving.
            "run 60", "move 125 134", "run 5", "click left", "run 40", "move 305 196", "run 15"],
     (2, "Take the phone call")),
    ("mundania-step3", "dw step three", "Mundania", "Your house", "Kitchen supplies taken",
     None, ["include walkthrough_03_kitchen.xit"], (3, "Gather kitchen supplies")),
    ("mundania-step4", "dw step four", "Mundania", "Your house", "Package received",
     None, ["include walkthrough_04_courier.xit"], (4, "Meet the courier")),
    ("mundania-opening-done", "dw opening", "Mundania", "Your house", "Bedroom (opening finished)",
     None, ["include walkthrough_05_cdrom.xit"], (5, "Start the game CD")),
    ("xanth-cavern", "dw cavern", "Xanth", "Cavern", "Cavern (with Nada)",
     "mundania-opening-done", ["include walkthrough_06_xanth.xit"], (6, "Arrive in Xanth")),
    ("xanth-cavern-doors", "dw doors", "Xanth", "Cavern", "Cavern (second door open)",
     "xanth-cavern", ["include walkthrough_07_cavern.xit"], (7, "Search the cavern with Nada")),
    ("xanth-village", "dw village", "Xanth", "Isthmus", "Isthmus Village",
     "xanth-cavern", ["include make_anchor_isthmus_village.xit"], (8, "Reach Isthmus Village")),
    ("xanth-meadow", "dw meadow", "Xanth", "Isthmus", "Meadow (bucket trapped)",
     "xanth-village", ["include walkthrough_08_from_village.xit"] + DISMISS_SCORE, (9, "Clear Isthmus Village")),
    ("xanth-recipe", "dw recipe", "Xanth", "Isthmus", "Booth (recipe read)",
     "xanth-village", ["include walkthrough_09_pail.xit"] + DISMISS_SCORE, (10, "Fairy Nuff's recipe")),
    ("xanth-void", "dw void", "Xanth", "The Void", "The Void (shimmering door)",
     "anchor:anchor_isthmus", ["include lib/resume_isthmus.xit"], (11, CHAIN_START)),
    ("earth-outskirts", "dw earth", "Earth", "Outskirts", "Outskirts of Earth",
     "anchor:anchor_isthmus", ["include walkthrough_10_void.xit"], (12, "Solidify the shimmering door")),
    ("earth-barrow", "dw barrow", "Earth", "Barrow", "Inside the barrow",
     "earth-outskirts", ["include walkthrough_11_barrow.xit"] + DISMISS_SCORE, (13, "Find the barrow")),
    ("earth-spring", "dw spring", "Earth", "Barrow", "Desert spring (Metria unmasked)",
     "anchor:anchor_unmask", ["include lib/resume_unmask.xit"], (14, CHAIN_START)),
    ("earth-metria", "dw metria", "Earth", "Barrow", "Barrow (Metria defeated)",
     "anchor:anchor_unmask", ["include walkthrough_12_metria.xit"] + DISMISS_SCORE, (15, "Get past Metria")),
    ("earth-dungeon", "dw dungeon", "Earth", "Barrow", "Dungeon (staircase)",
     "earth-metria", ["include walkthrough_13_dungeon.xit"] + DISMISS_SCORE, None),
    ("earth-gap", "dw gap", "Earth", "Barrow", "Ironwood Tree (to the Region of Fire)",
     "earth-metria", ["include walkthrough_14_dungeon_items.xit"] + DISMISS_DIALOGUE, (16, "Search the dungeon")),
    ("earth-wings", "dw wings", "Earth", "Barrow", "Flapping wings",
     "anchor:agent_dungeon_test", ["include walkthrough_15_barrow_wings.xit"], None),
    ("fire-lake", "dw lake", "Fire", "Lava lake", "Lava lake",
     "anchor:agent_dungeon_test", ["include walkthrough_16_fire_lake_entry.xit"], (17, "Enter the Region of Fire")),
    ("fire-mack", "dw mack", "Fire", "Lava lake", "Fireman (Mack's story)",
     "anchor:agent_dungeon_test", ["include walkthrough_17_fireman_story.xit"] + DISMISS_DIALOGUE, (18, "Hear Mack's story")),
    ("fire-wall", "dw firewall", "Fire", "Firewall", "Firewall",
     "anchor:agent_dungeon_test", ["include walkthrough_18_firewall.xit"], (19, "Reach the firewall")),
    ("fire-charcoal", "dw charcoal", "Fire", "Firewall", "Firewall (charcoal taken)",
     "anchor:agent_dungeon_test", ["include walkthrough_19_charcoal.xit"], (20, "Take the charcoal")),
]

SCAN = {c: s for c, s in zip("1234567890", range(0x02, 0x0C))}
SCAN.update({c: s for c, s in zip("qwertyuiop", range(0x10, 0x1A))})
SCAN.update({c: s for c, s in zip("asdfghjkl", range(0x1E, 0x27))})
SCAN.update({c: s for c, s in zip("zxcvbnm", range(0x2C, 0x33))})
SCAN[" "] = 0x39


def default_out() -> Path:
    if os.environ.get("XANTH_DEV_CHECKPOINTS"):
        return Path(os.environ["XANTH_DEV_CHECKPOINTS"])
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "xanth-port" / "dev-checkpoints"


def default_data() -> Path:
    for c in (os.environ.get("XANTH_DATA"), str(Path.home() / "Games/companions-of-xanth/data"),
              str(ROOT / "game_cd/XANTH")):
        if c and (Path(c) / "XANTH.EXE").is_file():
            return Path(c)
    raise SystemExit("retail data not found; pass --data")


def save_lines(label: str, ref: Path) -> list[str]:
    lines = ["run 20", "key 0 63", "run 20"]
    lines += [f"key {ord(c)} {SCAN[c]}" for c in label]
    lines += ["key 13 28", "run 40", "move 160 100", "run 20", f"shot {ref}",
              "dump 3961:0256 16",      # progress fingerprint: room DS:0256, score DS:0264
              "dump 3961:69FC 2"]       # and the inventory count DS:69FC
    return lines


def read_list(out: Path) -> dict[str, list[str]]:
    rows = {}
    path = out / "checkpoints.txt"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#"):
                parts = line.split("\t")
                if len(parts) >= 7:
                    rows[parts[0]] = parts
    return rows


def write_list(out: Path, rows: dict[str, list[str]]) -> None:
    order = [c[0] for c in CHECKPOINTS] + sorted(set(rows) - {c[0] for c in CHECKPOINTS})
    text = "# id\tlabel\tregion\tarea\tspot\tverified\tsha256\troom\tscore\tstep\tstep name\titems\n"
    text += "".join("\t".join(rows[i]) + "\n" for i in order if i in rows)
    tmp = out / "checkpoints.txt.tmp"
    tmp.write_text(text)
    os.chmod(tmp, 0o600)
    tmp.replace(out / "checkpoints.txt")


def build(cp, vmboot: Path, data: Path, out: Path, budget: int, anchors: Path) -> list[str]:
    cid, label, region, area, spot, base, route, step = cp
    with tempfile.TemporaryDirectory(prefix=f"xanth-ck-{cid}-") as tmp:
        work = Path(tmp)
        saves = work / "saves"
        saves.mkdir()
        if base and base.startswith("anchor:"):
            name = base.split(":", 1)[1]
            src = anchors / name
            slot = src / "XANTH000.SAV"
            if not slot.is_file() or hashlib.sha256(slot.read_bytes()).hexdigest() != ANCHORS[name]:
                raise SystemExit(f"{cid}: {slot} is missing or does not match its pin")
            for f in ("XANTH000.SAV", "LEGEND.INI", "RESTART.DAT"):
                if (src / f).is_file():
                    shutil.copy(src / f, saves / f)
        elif base:
            src = out / f"{base}.SAV"
            if not src.is_file():
                raise SystemExit(f"{cid}: build {base} first")
            shutil.copy(src, saves / "XANTH000.SAV")
        shutil.copytree(TRACES / "lib", work / "lib")
        (work / "build" / "frames").mkdir(parents=True)
        for name in os.listdir(TRACES):
            if name.endswith(".xit"):
                shutil.copy(TRACES / name, work / name)
        ref = out / f"{cid}.ref.bmp"
        script = work / "build.xit"
        script.write_text("\n".join(route + save_lines(label, ref)) + "\n")
        before = set(saves.glob("XANTH*.SAV"))
        proc = subprocess.run(
            [str(vmboot), "--exe", str(data / "XANTH.EXE"), "--data", str(data),
             "--saves", str(saves), "--script", str(script), "--insns", str(budget)],
            cwd=work, env={**{k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")}, "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        log = proc.stdout + proc.stderr
        (out / f"{cid}.build.log").write_text(log[-20000:])
        os.chmod(out / f"{cid}.build.log", 0o600)
        if proc.returncode != 0 or "fault                 : ok" not in log:
            raise SystemExit(f"{cid}: route failed (see {cid}.build.log)")
        made = [p for p in saves.glob("XANTH*.SAV")
                if p.read_bytes()[:len(label)] == label.encode() and p not in before]
        if len(made) != 1:
            raise SystemExit(f"{cid}: expected one new save labelled {label!r}, found {len(made)}")
        dst = out / f"{cid}.SAV"
        shutil.copy(made[0], dst)
        os.chmod(dst, 0o600)
        if ref.exists():
            os.chmod(ref, 0o600)
        digest = hashlib.sha256(dst.read_bytes()).hexdigest()
        fp = (work / "build" / "frames" / "dump_3961_0256.bin").read_bytes()
        room, score = int.from_bytes(fp[0:2], "little"), int.from_bytes(fp[14:16], "little")
        items = int.from_bytes((work / "build" / "frames" / "dump_3961_69FC.bin").read_bytes()[:2], "little")
        print(f"[checkpoints] built {cid} ({spot}) room {room} score {score} items {items} sha256 {digest[:16]}")
        return [cid, label, region, area, spot, "0", digest, str(room), str(score),
                str(step[0]) if step else "0", step[1] if step else "-", str(items)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--vmboot", required=True)
    ap.add_argument("--data")
    ap.add_argument("--out")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--insns", type=int, default=30_000_000_000)
    ap.add_argument("--anchors", help="directory with the walkthrough anchors (default: build/)")
    args = ap.parse_args(argv)
    data = (Path(args.data) if args.data else default_data()).resolve()
    out = (Path(args.out) if args.out else default_out()).resolve()
    vmboot = Path(args.vmboot).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    anchors = (Path(args.anchors) if args.anchors else ROOT / "build").resolve()
    rows = read_list(out)
    failed = []
    for cp in CHECKPOINTS:
        if args.only and cp[0] not in args.only:
            continue
        try:
            rows[cp[0]] = build(cp, vmboot, data, out, args.insns, anchors)
        except SystemExit as e:            # keep going; later entries may not depend on it
            print(f"[checkpoints] FAILED {e}")
            failed.append(cp[0])
            continue
        write_list(out, rows)
    if failed:
        print(f"[checkpoints] failed: {' '.join(failed)}")
    print(f"[checkpoints] {len(rows)} listed in {out / 'checkpoints.txt'} (unverified until swept)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
