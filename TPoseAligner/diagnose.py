"""Diagnostic helper: dump what offset / pose APIs are available on
FBCharacter in the running MotionBuilder.

Output goes to BOTH stdout (console / Python Editor) AND a file on the
desktop so you can always read the result. A message box at the end tells
you the file path.

Run from MotionBuilder Python Editor:

    exec(open(r"C:\\Users\\jinseok.park\\Desktop\\MotionBuilder-Script\\TPoseAligner\\diagnose.py").read())
"""

from __future__ import annotations

import os
import sys
import traceback
from io import StringIO


_buffer = StringIO()


def _emit(line: str = "") -> None:
    print(line)
    _buffer.write(line + "\n")


def _section(title: str) -> None:
    _emit("")
    _emit("=" * 70)
    _emit(title)
    _emit("=" * 70)


def diagnose() -> str:
    try:
        import pyfbsdk
        from pyfbsdk import FBSystem, FBCharacter
    except Exception as exc:
        _emit(f"pyfbsdk import failed: {exc}")
        _emit(traceback.format_exc())
        return _buffer.getvalue()

    _section("pyfbsdk version / module")
    _emit(f"pyfbsdk module: {getattr(pyfbsdk, '__file__', '?')}")
    _emit(f"FBSDK_VERSION:  {getattr(pyfbsdk, 'FBSDK_VERSION', 'n/a')}")
    _emit(f"Python:         {sys.version.splitlines()[0]}")

    _section("FBCharacter methods of interest (Offset / Stance / Pose)")
    interesting = sorted(
        attr for attr in dir(FBCharacter)
        if "ffset" in attr.lower() or "stance" in attr.lower() or "pose" in attr.lower()
    )
    if not interesting:
        _emit("  (none found)")
    for attr in interesting:
        _emit(f"  {attr}")

    _section("All FBCharacter Set* methods")
    set_methods = sorted(attr for attr in dir(FBCharacter) if attr.startswith("Set"))
    if not set_methods:
        _emit("  (none found)")
    for attr in set_methods:
        _emit(f"  {attr}")

    _section("All FBCharacter Get* methods")
    get_methods = sorted(attr for attr in dir(FBCharacter) if attr.startswith("Get"))
    if not get_methods:
        _emit("  (none found)")
    for attr in get_methods:
        _emit(f"  {attr}")

    _section("Vector / matrix type symbols")
    for sym in ("FBRVector", "FBTVector", "FBVector3d", "FBVector4d",
                "FBVector3", "FBVector4", "FBMatrix", "FBRotationOrder"):
        present = hasattr(pyfbsdk, sym)
        _emit(f"  {sym:<16} {'yes' if present else 'NO'}")

    _section("Active scene character info")
    chars = list(FBSystem().Scene.Characters)
    _emit(f"Characters in scene: {len(chars)}")
    for ch in chars:
        try:
            chrz = ch.GetCharacterize()
        except Exception:
            chrz = "?"
        try:
            actin = ch.ActiveInput
        except Exception:
            actin = "?"
        _emit(f"  - {ch.LongName} | Characterized={chrz} | ActiveInput={actin}")

    if chars:
        first = chars[0]
        _section(f"Sample bone properties on '{first.LongName}'")
        sample_slots = ["Hips", "LeftUpLeg", "LeftLeg", "LeftArm", "LeftForeArm", "LeftHand"]
        suffixes = ["RotationOffset", "TranslationOffset", "Offset", "Link",
                    "OffsetT", "OffsetR", "OffsetS"]
        any_found = False
        for slot in sample_slots:
            for suffix in suffixes:
                prop_name = slot + suffix
                prop = first.PropertyList.Find(prop_name)
                if prop is None:
                    continue
                any_found = True
                cls = type(prop).__name__
                try:
                    data = prop.Data if hasattr(prop, "Data") else "(no Data)"
                except Exception as exc:
                    data = f"(err: {exc})"
                _emit(f"  {prop_name:<28} | {cls:<28} | {data}")
        if not any_found:
            _emit("  (no offset-style properties found - the character may use a different API)")

        _section(f"All animatable properties containing 'Offset' on '{first.LongName}'")
        offset_props = []
        try:
            for prop in first.PropertyList:
                name = prop.Name
                if "ffset" in name.lower():
                    offset_props.append(name)
        except Exception as exc:
            _emit(f"  (could not iterate PropertyList: {exc})")
        if not offset_props:
            _emit("  (none found)")
        for name in sorted(offset_props):
            _emit(f"  {name}")

    return _buffer.getvalue()


def write_report() -> str:
    """Run diagnose() and write the result to a file on the desktop."""
    output = diagnose()
    out_path = os.path.join(
        os.path.expanduser("~"), "Desktop", "TPoseAligner_diagnose.txt",
    )
    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(output)
        _emit("")
        _emit(f"Report written to: {out_path}")
    except Exception as exc:
        _emit(f"Could not write report file: {exc}")
        out_path = ""

    try:
        from pyfbsdk import FBMessageBox
        FBMessageBox(
            "TPoseAligner diagnose",
            f"Diagnostic complete.\n\nReport file:\n{out_path}\n\n"
            "Open the file and paste its contents back to the chat.",
            "OK",
        )
    except Exception:
        pass

    return out_path


_report_path = write_report()
