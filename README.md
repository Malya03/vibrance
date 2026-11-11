# vibrance

Digital vibrance for KDE Plasma on Wayland: more saturated colors on the whole
display, like NVIDIA's "Digital Vibrance" or `vibrant-cli` on X11. Useful for
games (CS2, Overwatch, ...) and for dull laptop panels and budget monitors.

```bash
vibrance on 150
```

One Python file, no dependencies beyond what Plasma already ships, no root, and
nothing is injected into the game.

## How it works

On Wayland only the compositor can change the colors that reach the display,
and KWin has no saturation setting. It does have color management: every window
is converted from its own color space (sRGB unless the application says
otherwise) into the color space described by the display's ICC profile.

`vibrance` writes a synthetic ICC profile that describes a display with sRGB
primaries pulled towards the white point, and selects it for the display. KWin
now believes the panel is less saturated than it is and compensates. The
correction KWin derives is exactly a saturation matrix: applied in linear
light, with Rec. 709 luma weights, leaving white, grays and brightness alone.

The profile is selected through `kscreen-doctor`, the same way as picking an ICC
profile in Plasma's display settings, so KWin remembers it per display across
reboots.

## What it costs

`vibrance` is not a post-processing layer such as vkBasalt or ReShade. No
shader pass is added for it: the matrix becomes part of the color conversion
KWin does anyway while it composites a window, and the tone curve of the
profile is handed to the display hardware (checked on an AMD GPU with Plasma
6.8 by reading back the CRTC gamma table).

What you do give up is direct scanout. While a display has an ICC profile, KWin
composites fullscreen windows itself instead of passing the game's buffer
straight to the display: one more full-screen copy per frame. A display that
already uses a color profile ("Built-in" or an ICC file) is in that state
anyway, and nothing changes for it. The cost is small, but it is not zero:
measure it if every frame matters to you.

Two things make KWin add a second full-screen pass on top:

- "Color accuracy" set to "Prefer color accuracy". `vibrance` switches the
  display to "Prefer efficiency" while it is on and puts your choice back
  afterwards (Plasma 6.3 and newer).
- Night Light being active. This comes from reading KWin 6.8's source and has
  not been measured.

KWin can also hand such a matrix to the display hardware (kernel plane color
pipelines), which could bring direct scanout back, but Plasma 6.8 ships with
that path switched off (KDE bug 525839).

## Requirements

- KDE Plasma 6.1 or newer, Wayland session. Developed and tested on Plasma 6.8.
  Plasma 6.1 to 6.7 are handled according to the KWin and libkscreen sources of
  those releases but have not been run by the author; on 6.1 and 6.2 KWin
  always uses the slower path for ICC profiles.
- `kscreen-doctor` (part of Plasma's libkscreen).
- Python 3.8 or newer, standard library only.
- HDR and wide color gamut switched off for the display: KWin ignores the ICC
  profile in those modes. Plasma's own "sRGB color intensity" slider is the
  tool for that case.

## Install

```bash
git clone https://github.com/Malya03/vibrance.git
install -Dm755 vibrance/vibrance ~/.local/bin/vibrance
```

Any directory in your `PATH` will do. The script is a single file, so you can
also download just [`vibrance`](vibrance) and make it executable.

To update, run `git pull` in the clone and repeat the `install` command.

## Usage

```
vibrance on [PERCENT] [-o DISPLAY[=PERCENT]]...      enable
vibrance off [-o DISPLAY]...                         restore the previous color settings
vibrance toggle [PERCENT] [-o DISPLAY[=PERCENT]]...  switch between the two
vibrance run [PERCENT] [-o ...] -- COMMAND           enable only while COMMAND is running
vibrance status                                      list the displays and their state
vibrance make PERCENT [FILE]                         only write the ICC profile
```

`PERCENT` is the saturation: 100 is neutral, 150 is a good start, 272 is the
most KWin accepts (it rejects profiles with a smaller gamut).

### One display or several

Without `-o` every enabled display is used. Each display has a level of its
own, and `vibrance` remembers the last one:

```bash
vibrance status                         # names of the displays, and what is on
vibrance on 160                         # all displays at 160%
vibrance on 125 -o HDMI-A-1             # only this display
vibrance on -o eDP-1=160 -o DP-2=125    # a level for each
vibrance off -o DP-2                    # one display back to normal
vibrance on                             # everything on again, each at its own level
```

```
$ vibrance status
eDP-1     on, 160%
HDMI-A-1  off (last level 125%)
```

The level is stored for the monitor, not for the port (Plasma 6.4 and newer):
another monitor plugged into the same port starts with a level of its own.

### Keyboard shortcut

Bind `vibrance toggle` to a global shortcut. It turns everything off if any
display is on, otherwise everything on at the remembered levels.

### Only while a game runs

```bash
vibrance run -- COMMAND                 # remembered levels
vibrance run 170 -o DP-2 -- COMMAND
```

Steam launch options:

```
vibrance run -- %command%
```

When the command ends, the displays return to what they had before, including
a different vibrance level. If vibrance cannot be enabled, the command still
starts and a warning is printed.

Launchers that exit right after starting the actual game end the wrapper early;
use `vibrance on` or a shortcut for those. Inside a Flatpak sandbox (Flatpak
Steam) there is no `kscreen-doctor`, so `run` cannot do anything there: enable
vibrance from outside instead.

## Good to know

- The level is relative to a plain sRGB display. If the display used its
  "Built-in" profile or your own ICC profile, that profile is not in effect
  while vibrance is on.
- Plasma's "sRGB color intensity" slider, which the display settings show
  while a color profile is in use, works against vibrance: at 100% it cancels
  the effect completely. Leave it at 0%.
- Colors that are already very saturated clip, as with any vibrance control.
- The five darkest brightness levels above black (1 to 5 of 255) come out up
  to one and a half levels darker, because KWin converts the profile's tone
  curve with limited precision. Black stays exactly black.
- KWin stores the selected profile itself, so vibrance stays on after a reboot
  until you run `vibrance off`.

## What it touches, and how to undo it

`vibrance on` changes three settings of a display: the ICC profile, the color
profile source (to "ICC profile") and, on Plasma 6.3 and newer, color accuracy
(to "Prefer efficiency"). `vibrance off` restores what was there before.

| Path | Content |
| --- | --- |
| `~/.local/share/vibrance/` | generated profiles, a few kilobytes each |
| `~/.local/state/vibrance/displays.json` | last level and previous settings of each display |

To remove everything: run `vibrance off`, then delete the script and these two
paths. Without the script, set the color profile of the display back in
Plasma's display settings, or run
`kscreen-doctor output.NAME.colorProfileSource.sRGB`.

## Alternatives

| Tool | Why it did not fit |
| --- | --- |
| `vibrant-cli` / libvibrant | X11 only |
| vkBasalt, ReShade | post-processing inside the game: an extra shader pass every frame, set up per game, does not cover the desktop |
| gamescope | a nested compositor with its own composition step |
| "sRGB color intensity" in Plasma | stops at the panel's native gamut, so it does next to nothing on an sRGB-class panel |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests run the script against a fake `kscreen-doctor` that imitates Plasma
6.0 to 6.8. They never touch a real display.

## Authors

- [Malya03](https://github.com/Malya03) had the idea, set the requirements and
  tested the result on real hardware.
- Claude, an AI model made by Anthropic, did the research into KWin and wrote
  the code, the tests and this README.

## License

GNU Affero General Public License, version 3 or (at your option) any later
version. See [LICENSE](LICENSE). The ICC profiles the tool generates are not
covered by it; they are in the public domain.
