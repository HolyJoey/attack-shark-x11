# Findings from the vendor software

Read from the official Windows software (`ATTACK SHARK X11 SOFT.exe`, `AttackShark.exe`
built 2025-11-07) by disassembly. Addresses are virtual addresses in that build
(image base `0x400000`). The X11 is device 4 in the vendor's UI (`res/MS/MS_4`), matched
by `VID&02045E` / `PID&0055` and USB ids `1d57:fa55` (wired) and `1d57:fa60` (2.4 GHz).

## Battery

The only battery source is the input message `03 55 40 <status> <percent>`. There is
no feature report for battery or voltage. The handler is at `0x444717`:

- every message restarts a 6000 ms timer; if it expires the mouse is treated as gone
- status `2` (full): shows 100, whatever the percent byte says
- status `3` (charging): hides the number, shows `charging_text` and animates the bar
  on a 30 ms timer
- otherwise: shows the percent unchanged (`0x423690`, formatted `%d%%`), but only
  when it is between 1 and 100

The percentage is the firmware's own voltage-based figure; the software adds no
correction or smoothing.

## Button actions

The button packet (report `0x08`, length `0x3b`) is built at `0x446230`. Each menu
entry's internal index is looked up in a table at `0x4e7368` (20-byte entries: index,
then the three slot bytes). The X11-relevant rows:

| Menu entry | Slot bytes | Shown for X11 |
|---|---|---|
| Left / Right / Middle click | `02` / `03` / `04 00 00` | yes |
| Forward / Backward | `06` / `05 00 00` | yes |
| Double click | `07 00 00` | yes |
| Fire button | `08 00 00` | yes |
| Scroll up / down | `09` / `0a 00 00` | yes |
| Scroll left / right (tilt) | `0b` / `0c 00 00` | hidden |
| DPI cycle / + / - | `0d` / `0e` / `0f 00 00` | yes |
| Easy aim | `10 00 03` | yes |
| Shortcut (keyboard) | `11 <modifiers> <usage>` | yes |
| Macro | `12 00 <button>` | yes |
| LED loop | `29 00 03` | hidden |
| Media player, prev, next, play/pause, stop, mute, vol+, vol- | `15`, `16`, `17`, `18`, `19`, `1a`, `1b`, `1c` | yes |
| Calculator, email | `1d`, `1e` | yes |
| Browser forward, back, stop, refresh, home, search | `20`, `21`, `22`, `24`, `25`, `26` | yes |
| My computer | `23` | yes |
| Button off | `01 00 00` | yes |

"Browser favorites" and the shortcut presets (cut, copy, lock PC, ...) are plain
keyboard slots, e.g. copy is `11 01 06` (Ctrl + C).

`0x40` (polling-rate cycle, from the upstream driver) is not in this table.

## Settings hidden for the X11

The X11 layout contains lift-off distance, motion sync and wake mode, but their
sections are marked `visible="false"`, so the X11 probably doesn't support them.

## Over the USB cable

The mouse's report descriptor on the cable (interface 2) declares shorter
reports: DPI `0x04` is 52 bytes, lighting `0x05` is 13, profile `0x0C` is 6. The
rest (polling `0x06` 9, buttons `0x08` 59, unlock `0xA0` 8) match the receiver.

The vendor send routine (`0x444e80`) checks a wired flag (`[obj+0x400] == 1`):

- non-macro reports: the same packet as for the receiver, header included
  (`04 38 ...`), which the driver cuts to the declared size; the checksums fall
  inside the kept part. Each write is retried up to 5 times, 200 ms apart.
- report `0x09` (macros): repacked into `09 40 ...` chunks, a different layout.

Observed on hardware: over the cable every other feature write stalls (EPIPE)
and the next one succeeds; stalled writes aren't applied. Reads with the
shorter lengths return the same values as over the receiver, and lighting, DPI
and button writes over the cable were each verified by changing a value, reading
it back, and restoring it.

## Easy aim on hardware

Mapped to a side button (`10 00 03`) it works as the official software's Easy
Aim. The mouse sends no input message when it's used, so an app can't tell when
it's active.

## Macro play modes

The vendor's select-macro dialog offers three modes and stores the choice as an
index, which goes to page byte 4 of the macro packet (upstream's `MacroType`):

| Byte 4 | Vendor label | Behaviour on the X11 |
|---|---|---|
| `0` | The Number Of Time To Play | plays the loop count (byte 8) times |
| `1` | Any Key Press To Stop Playing | loops until another *mouse* button is clicked; the mouse can't see the keyboard |
| `2` | Press And Hold, Release Stop | loops while the button is held, stops on release |

The macro checksum (footer bytes 10-11) is the sum of page 0 from byte 4 plus
page 1 from byte 4: the vendor builder sums its buffer from index 3 (`0x4460de`),
which is page byte 4. Upstream starts page 0 at byte 8, which leaves out the
mode; for mode 0 the result is the same, but modes 1 and 2 fail the checksum and
the mouse silently keeps its previous macro. With the checksum fixed, the mouse
acknowledges the write (`03 55 50 00 09`) and all three modes work on hardware.
