"""
V-H's binding (research/PREREGISTRATION_4.md): the bound text passes, any other text is refused.
Run: PYTHONPATH=. python tests/test_vh.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from trading.fno.vh import REGISTRATION, RegistrationMismatch, check_registration


def test_the_bound_registration_passes_and_a_changed_one_is_refused():
    assert check_registration() == check_registration(REGISTRATION)
    changed = Path(tempfile.mkdtemp()) / "PREREGISTRATION_4.md"
    changed.write_bytes(REGISTRATION.read_bytes().replace(b"h(x) = max(0.25, 0.01", b"h(x) = max(0.20, 0.01", 1))
    try:
        check_registration(changed)
        raise AssertionError("a changed registration was accepted")
    except RegistrationMismatch:
        pass
    assert "DRAFT" not in REGISTRATION.read_text()


if __name__ == "__main__":
    import sys
    import traceback

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
