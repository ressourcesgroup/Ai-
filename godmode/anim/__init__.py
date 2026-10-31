"""Procedural R15 animation engine (see rig.py for joints/limits, styles.py for the styles).

The styles were designed from the reference video titles the user supplied; we
cannot watch video, so none of this is motion capture.
"""

from .engine import PROVENANCE, generate, list_styles, match_style, validate
from .player import player_script
from .rig import HIERARCHY, JOINTS, POS_LIMITS, POSED_PARTS, ROT_LIMITS, limits_table_markdown
from .styles import STYLES

__all__ = ["PROVENANCE", "generate", "list_styles", "match_style", "validate", "player_script", "HIERARCHY", "JOINTS",
           "POS_LIMITS", "POSED_PARTS", "ROT_LIMITS", "limits_table_markdown", "STYLES"]
