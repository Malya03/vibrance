#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Test double for kscreen-doctor.

Prints the 'kscreen-doctor -o' format of libkscreen (Plasma 6.0 to 6.8) and
understands the output.<name>.<setting>.<value> options vibrance uses. The
"displays" live in a JSON file, so no real display is ever touched.

  FAKE_KSCREEN_STATE   JSON file: {"plasma": [major, minor], "outputs": [...]}
  FAKE_KSCREEN_LOG     every invocation is appended here as one JSON line
  FAKE_KSCREEN_REJECT  when set, KWin "rejects" every ICC profile
"""
import json
import os
import sys

GREEN, RED, YELLOW, BLUE, RESET = "\x1b[01;32m", "\x1b[01;31m", "\x1b[01;33m", "\x1b[01;34m", "\x1b[0;0m"
SOURCES = ("sRGB", "ICC", "EDID")
TRADEOFFS = {"preferEfficiency": "prefer efficiency and performance", "preferAccuracy": "prefer accuracy"}


def show(config):
    plasma = tuple(config["plasma"])
    for number, output in enumerate(config["outputs"], start=1):
        head = "%d %s" % (number, output["name"])
        if plasma >= (6, 4):
            head += " " + output["uuid"]
        print(GREEN + "Output: " + RESET + head)
        print("\t" + (GREEN + "enabled" if output["enabled"] else RED + "disabled") + RESET)
        print("\t" + GREEN + "connected" + RESET)
        print("\t" + GREEN + "priority %d" % (number if output["enabled"] else 0) + RESET)
        print("\t" + YELLOW + "Panel" + RESET)
        print("\t" + BLUE + "Modes: " + RESET + " 1:" + GREEN + "2560x1440@165.00*" + RESET + "!  2:1920x1080@60.00 ")
        print("\t" + YELLOW + "Geometry: " + RESET + "0,0 1707x960")
        print("\t" + YELLOW + "Vrr: " + RESET + "Automatic")
        print(YELLOW + "\tHDR: " + RESET + output["hdr"])
        if output["hdr"] == "enabled":
            print(YELLOW + "\t\tSDR brightness: " + RESET + "200 nits")
            if plasma >= (6, 7):  # lines that must not be mistaken for the SDR settings
                print(YELLOW + "\t\tHDR color profile source: " + RESET + "EDID")
                print(YELLOW + "\t\tHDR ICC profile: " + RESET + "none")
        print(YELLOW + "\tWide Color Gamut: " + RESET + output["wcg"])
        capable = output.get("capable", True)
        print(YELLOW + "\tICC profile: " + RESET + ((output["icc"] or "none") if capable else "incapable"))
        if plasma >= (6, 1):
            line = YELLOW + "\tColor profile source: " + RESET + (output["source"] if capable else "incapable")
            if plasma >= (6, 3) and capable:
                line += "\n" + YELLOW + "\tColor power preference: " + RESET + TRADEOFFS[output["tradeoff"]]
            print(line)
        print(YELLOW + "\tBrightness control: " + RESET + "supported, set to 100% and dimming to 100%")


def configure(config, args):
    plasma = tuple(config["plasma"])
    for arg in args:
        ops = arg.split(".")
        output = None
        if len(ops) >= 4 and ops[0] == "output":
            output = next((o for o in config["outputs"] if ops[1] in (o["name"], o["uuid"])), None)
        if output is None:
            sys.stderr.write("Output with name or uuid %s not found.\n" % (ops[1] if len(ops) > 1 else arg))
            return 3
        setting, value = ops[2], ".".join(ops[3:])
        if setting == "iccprofile":
            if value and (os.environ.get("FAKE_KSCREEN_REJECT") or not os.path.isfile(value)):
                if plasma >= (6, 3):  # older versions fail without a word
                    print('applying config failed! ICC profile "%s" is broken, it has no whitepoint' % value)
                return 0
            output["icc"] = value
        elif setting == "colorProfileSource" and plasma >= (6, 1) and value in SOURCES:
            output["source"] = value
        elif setting == "colorPowerTradeoff" and plasma >= (6, 3) and value in TRADEOFFS:
            output["tradeoff"] = value
        else:
            sys.stderr.write("Unable to parse arguments: %s\n" % arg)
            return 2
    path = os.environ["FAKE_KSCREEN_STATE"]
    with open(path + ".tmp", "w") as f:
        json.dump(config, f)
    os.replace(path + ".tmp", path)  # tests read this file while a command is still running
    return 0


def main(args):
    with open(os.environ["FAKE_KSCREEN_LOG"], "a") as log:
        log.write(json.dumps({"args": args, "env": {name: os.environ.get(name) for name in
                                                     ("LD_LIBRARY_PATH", "LD_PRELOAD", "QT_QPA_PLATFORM")}}) + "\n")
    with open(os.environ["FAKE_KSCREEN_STATE"]) as f:
        config = json.load(f)
    if args == ["-o"]:
        show(config)
        return 0
    return configure(config, args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
