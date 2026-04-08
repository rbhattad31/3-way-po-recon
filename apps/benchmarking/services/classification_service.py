"""
Line item classification service.
Classifies a line item description into one of the benchmark categories using
keyword matching rules. Falls back to UNCATEGORIZED.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword map: category -> list of regex keywords (case-insensitive)
# Order matters: first match wins.
# ---------------------------------------------------------------------------
CATEGORY_RULES = [
    ("EQUIPMENT", [
        r"\bvrf\b", r"\bvrv\b", r"\bchiller\b", r"\bahu\b", r"\bfcu\b",
        r"\bfan.coil\b", r"\bpackaged\b", r"\bdx\b", r"\bsplit.ac\b",
        r"\bsplit unit\b", r"\bcassette\b", r"\bcondenser\b", r"\bcompressor\b",
        r"\bevaporator\b", r"\boutdoor unit\b", r"\bindoor unit\b",
        r"\bair handling\b", r"\brtul?\b", r"\bcooling tower\b",
        r"\bpumps?\b", r"\bpump set\b",
    ]),
    ("CONTROLS", [
        r"\bbms\b", r"\bbas\b", r"\bcontrol panel\b", r"\bprogramming\b",
        r"\bscada\b", r"\bddc\b", r"\bplc\b", r"\bthermostat\b",
        r"\bautomation\b", r"\bmodbus\b", r"\bbacnet\b", r"\blonworks\b",
        r"\bcommissioning controller\b", r"\bcontrol wiring\b",
    ]),
    ("DUCTING", [
        r"\bduct(ing|work)?\b", r"\bplenum\b", r"\bflex duct\b",
        r"\bspiro\b", r"\bgi duct\b", r"\bsheet metal\b", r"\boval duct\b",
        r"\bround duct\b", r"\bair diffuser\b", r"\bgrille\b", r"\bregister\b",
        r"\bvolume control\b", r"\bdamper\b",
    ]),
    ("INSULATION", [
        r"\binsulat\b", r"\barmaflex\b", r"\bkaiflex\b", r"\bnitrile\b",
        r"\bglass wool\b", r"\brock wool\b", r"\bfoam\b",
        r"\bvapour barrier\b", r"\bthermal wrap\b",
    ]),
    ("ACCESSORIES", [
        r"\bhangers?\b", r"\bsupports?\b", r"\bvibration isolator\b",
        r"\bflexible connector\b", r"\bvalves?\b", r"\bstrainer\b",
        r"\bpressure gauge\b", r"\bthermometer\b", r"\bflow switch\b",
        r"\bball valve\b", r"\bbutterfly valve\b",
    ]),
    ("INSTALLATION", [
        r"\binstallation\b", r"\binstall\b", r"\bfixing\b",
        r"\bmounting\b", r"\berection\b", r"\blabour\b", r"\bmanpower\b",
        r"\bcivil works?\b", r"\bcore cutting\b", r"\bgrouting\b",
        r"\bcabling\b", r"\belectrical connection\b",
    ]),
    ("TC", [
        r"\btest(ing)?\b", r"\bcommission(ing)?\b", r"\bt\s*[&+]\s*c\b",
        r"\bfat\b", r"\bsat\b", r"\bsnagging\b", r"\bbalancing\b",
        r"\bhandover\b", r"\bhvac testing\b",
    ]),
]

_COMPILED_RULES = [
    (cat, [re.compile(kw, re.IGNORECASE) for kw in keywords])
    for cat, keywords in CATEGORY_RULES
]


class ClassificationService:
    """Classify line item descriptions into benchmark categories."""

    @classmethod
    def classify(cls, description: str) -> dict:
        """
        Returns {"category": str, "confidence": float, "matched_keyword": str}
        """
        if not description:
            return {"category": "UNCATEGORIZED", "confidence": 0.0, "matched_keyword": ""}

        desc = description.strip()

        for category, patterns in _COMPILED_RULES:
            for pattern in patterns:
                if pattern.search(desc):
                    return {
                        "category": category,
                        "confidence": 0.90,
                        "matched_keyword": pattern.pattern,
                    }

        # Partial fallback: check if any token is >= 4 chars and is a partial domain word
        lower = desc.lower()
        if any(kw in lower for kw in ["supply", "procure", "purchase"]):
            return {"category": "EQUIPMENT", "confidence": 0.40, "matched_keyword": "supply"}

        return {"category": "UNCATEGORIZED", "confidence": 0.0, "matched_keyword": ""}

    @classmethod
    def classify_batch(cls, descriptions: list) -> list:
        """Classify a list of description strings. Returns list of result dicts."""
        return [cls.classify(d) for d in descriptions]
