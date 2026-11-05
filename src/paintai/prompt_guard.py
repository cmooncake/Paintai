"""Protect explicit portrait attributes from free-form learned suggestions."""

import re

COLORS = (
    "black",
    "white",
    "brown",
    "chestnut",
    "blonde",
    "red",
    "orange",
    "yellow",
    "amber",
    "gold",
    "green",
    "teal",
    "blue",
    "purple",
    "pink",
    "gray",
    "grey",
    "silver",
    "ivory",
    "cream",
    "beige",
)
COLOR = r"(?:" + "|".join(COLORS) + r")"


def portrait_locks(text):
    """Conservative extraction: preserve original spans; never invent missing attributes."""
    patterns = {
        "hair": rf"\b(?:chestnut\s+)?{COLOR}(?:\s+(?:short|long|bob|curly|straight)){{0,3}}\s+hair\b",
        "eyes": rf"\b{COLOR}\s+eyes\b",
        "clothing": rf"\b(?:(?:simple|plain)\s+)?{COLOR}\s+(?:blouse|shirt|dress|jacket|sweater)\b",
        "background": rf"\b(?:(?:plain|solid|muted|pale|dark|light)\s+){{0,3}}{COLOR}\s+background\b",
    }
    locks = {}
    for key, pattern in patterns.items():
        matches = list(re.finditer(pattern, text, re.I))
        if (
            key == "clothing"
            and matches
            and all(
                re.search(r"\b(?:ivory|white|cream)\s+blouse\b", m.group(0), re.I) for m in matches
            )
        ):
            matches = matches[:1]  # The compiler repeats ivory blouse as white blouse.
        if len(matches) != 1:
            continue  # Multiple characters/colors need a richer constraint parser.
        match = matches[0]
        if re.search(rf"{COLOR}\s*(?:and|/)\s*$", text[: match.start()], re.I):
            continue  # "black and white hair" is not monochrome white hair.
        if key == "clothing" and re.search(r"\b(?:high[- ]collar|turtleneck)\b", text, re.I):
            continue  # Do not introduce a high-collar exclusion against an explicit request.
        locks[key] = match.group(0)
    return locks


def negative_constraints(locks):
    negative = []
    for key, noun in (("hair", "hair"), ("eyes", "eyes"), ("background", "background")):
        if key not in locks:
            continue
        wanted = set(re.findall(COLOR, locks[key].lower()))
        equivalents = (
            {"brown", "chestnut"},
            {"amber", "gold", "yellow"},
            {"ivory", "white", "cream"},
            {"gray", "grey", "silver"},
        )
        for group in equivalents:
            if wanted & group:
                wanted |= group
        if key == "background" and "teal" in wanted:
            wanted |= {"green", "blue"}
        # Keep exclusions short and avoid suppressing white garments via white-background tokens.
        candidates = {
            "hair": ("blue", "green", "pink", "purple"),
            "eyes": ("blue", "green", "pink", "purple"),
            "background": ("yellow", "beige", "pink", "orange"),
        }
        for color in candidates[key]:
            if color not in wanted:
                negative.append(f"{color} {noun}")
    if "hair" in locks:
        negative += ["multicolored hair", "dyed hair tips"]
    clothing = locks.get("clothing", "").lower()
    if "blouse" in clothing and ("simple" in clothing or "plain" in clothing):
        negative += [
            "turtleneck",
            "high collar",
            "uniform",
            "necktie",
            "dark collar",
            "colored blouse",
        ]
    if "background" in locks and re.search(r"\b(plain|solid)\b", locks["background"], re.I):
        negative += ["patterned background", "decorative background"]
    return ", ".join(negative)
