from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.logic.calculators import _OVERCLAIM_TOLERANCE


def test_overclaim_tolerance_is_named_constant() -> None:
    """P2-C: 超额检测阈值应为命名常量。"""
    assert isinstance(_OVERCLAIM_TOLERANCE, float)
    assert _OVERCLAIM_TOLERANCE == 1.01
