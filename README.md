# Mac keyboard lock

Uses the already-installed Karabiner-Elements 16.0.0 service, not X11 or an
Accessibility event tap. No new daemon, root permissions, login item, or
Input Monitoring grant. The small compiled `caps-led` helper drives the LED.

## Install

Requires Karabiner-Elements (tested with 16.0.0) and the Xcode Command Line
Tools. The selected Karabiner profile must already map Caps Lock -> Escape as
a simple modification; install refuses to touch an unfamiliar Caps setup.

```sh
./lock-keyboard install
ln -s "$PWD/lock-keyboard" ~/.local/bin/lock-keyboard  # optional
```

Re-run `install` after changing the rule or moving this directory: the
installed rule refers to `caps-led` by absolute path.

## Controls

- Physical **Esc** (alone): lock.
- Hold either **Shift**, press and release physical **Esc**: unlock.
- Physical **Caps Lock** remains ordinary Escape when unlocked.
- `~/.local/bin/lock-keyboard on|off`: lock or emergency unlock.
- `lock-keyboard check`: configuration/lint check (flags an outdated installed
  rule), not live lock state.
- `lock-keyboard uninstall`: remove only this rule and restore Caps -> Escape.

Mouse and trackpad remain usable. The **Caps Lock LED turns on while locked**,
but Caps Lock itself is never enabled: typing state is untouched, so there is
no macOS Caps Lock badge and nothing to restore on unlock. Unlock returns the
LED to the real Caps Lock state. Transient notifications are a secondary
indication.

This is a cleaning/accidental-input lock, **not a security or screen lock**.
It applies to keyboard devices Karabiner grabs in the active user session.
Touch ID/power, ignored devices, and events posted above the HID layer are not
covered. Switching profiles or stopping Karabiner removes the filtering.

## Implementation

The highest-priority complex rule uses `exo_keyboard_locked` to swallow keyboard,
consumer/media, Apple vendor keyboard, and Apple vendor top-case events.
Pointing buttons and mouse movement are not matched. Both Shift keys are
swallowed too; two variables track them explicitly so Shift+Esc still works.
Lock and unlock happen inside the rule itself (`set_variable`), with unlock on
Esc release so holding Esc cannot relock. Existing non-lock mappings are left
in place.

Each transition also runs one `shell_command`, `exec caps-led on|off osascript …`,
which only updates the LED and then becomes the notification. Karabiner keeps
one shell command at a time and SIGKILLs a still-running one when the next
starts, which is why the lock state must never depend on a command finishing
(the earlier design unlocked from a Python callback, which took ~1 s through
`karabiner_cli` and could be killed or overtaken), and why the helper `exec`s
rather than spawning children that could outlive the kill and apply a stale LED.

Karabiner mirrors the Caps LED of its virtual keyboard onto the physical
keyboards it grabs (per-device "Manipulate caps lock LED", default on), and
refreshes them every 3 s from that state, so writing physical LEDs directly does
not stick. `caps-led` instead sets the private `HIDCapsLockLED` property
(`On`, or `Auto` to follow Caps Lock) on the virtual keyboard's macOS keyboard
filter through a *passive* `IOHIDEventSystemClient`. Simple clients are refused
this property; passive clients receive no events and need no Input Monitoring.
Opening the device with `IOHIDDeviceOpen` would require Input Monitoring for
`karabiner_console_user_server`, which runs the shell commands.
While locked, Karabiner itself sees the LED as Caps Lock on; the lock rule's
`optional: caps_lock` and the unlock rule's `optional: any` account for that.
If the virtual keyboard is recreated (for example across sleep) while locked,
the LED resets to normal while the keyboard stays locked.

Karabiner simple modifications precede complex modifications. The pre-existing
Caps -> Escape simple mapping is therefore moved to the end of this complex rule:
otherwise Caps would be mistaken for physical Esc and also lock the keyboard.
Complex-rule output is not rematched by the same complex-modification stage.

Install and uninstall create timestamped backups beside
`~/.config/karabiner/karabiner.json`, detect concurrent edits, and replace the
configuration atomically. Install resets the lock to off. Rules and variables
are session-local, not copied to the system/login-screen profile.

`lock-keyboard on|off` sets the variables through `karabiner_cli` first, then
the LED; an LED failure is reported without undoing or blocking the lock change.

`install` builds `caps-led.c` with the installed Xcode/CLT compiler if needed.
Rebuild manually:

```sh
clang -O2 -Wall -Wextra caps-led.c \
  -framework IOKit -framework CoreFoundation -o caps-led
```

## Linux original

Source: [Yeyito777/yeyito-lockkeyboard](https://github.com/Yeyito777/yeyito-lockkeyboard)
(`lock-keyboard-daemon.c`, with a `lock-keyboard on|off` wrapper).

There, keyd maps Caps Lock -> Escape and physical Esc -> F13. dwm binds F13
to `lock-keyboard on`. The daemon uses a single-instance `flock`, opens the X
display, retries `XGrabKeyboard` until dwm releases its grab, discards keyboard
events, and exits on Shift+F13. It controls a real keyboard's Caps LED by writing
an `EV_LED/LED_CAPSL` event to `/dev/input/eventX`, skipping keyd's virtual device.
SIGINT/SIGTERM and `lock-keyboard off` release the X grab and clear the LED.

Those APIs are Linux/X11-specific, so the Mac version implements the same
interaction with Karabiner's HID-level filtering.

## Verification

Run `python3 test_lock_keyboard.py` for rule/configuration unit tests.
Run `lock-keyboard check` for the installed Karabiner parser's rule lint.
`caps-led status` reports whether the virtual keyboard's LED is forced on.
CGEvent-posted synthetic key presses bypass Karabiner's HID input and cannot
validate physical-key suppression. For a manual test, put focus in a scratch
document, press physical Esc, try typing and shortcuts, then Shift+Esc and type
again. Check Caps Lock still produces ordinary Escape when unlocked.
