"""Privacy risk levels for metadata fields: the ONE place they are defined.

Parsers only report what they find; how risky each finding is gets decided
here, so the rules stay consistent across formats and easy to review.

Levels:
- critical: reveals where you were, hidden extra images or files, or
  identifies your exact device or you by name (GPS, thumbnails, MPF images,
  data after the end of the image, serial numbers, owner name)
- high: identifies device model, dates, authors, software, edit history and
  document IDs, download origin, comments
- medium: camera settings and ICC device info (fingerprinting)
- low: everything else
"""

from __future__ import annotations

from dataclasses import dataclass

from metaex.core.report import format_value
from metaex.core.result import Section

CRITICAL, HIGH, MEDIUM, LOW = "critical", "high", "medium", "low"
LEVELS = (CRITICAL, HIGH, MEDIUM, LOW)

# Sections that describe the file itself rather than embedded metadata.
NOT_METADATA_SECTIONS = {"file_type", "file_system", "hashes", "structure"}

# -- critical --------------------------------------------------------------------------
CRITICAL_SECTIONS = {"gps", "mpf", "trailing"}
CRITICAL_FIELDS = {
    ("thumbnail", "embedded_thumbnail"),
    ("jfif", "thumbnail"),
    ("jfif", "jfxx_thumbnail"),
    ("photoshop", "0x040c"),
    ("photoshop", "0x0409"),
}
CRITICAL_NAMES = {
    "BodySerialNumber", "LensSerialNumber", "SerialNumber", "CameraSerialNumber",
    "InternalSerialNumber", "CameraOwnerName", "OwnerName",
}

# -- high ------------------------------------------------------------------------------
HIGH_SECTIONS = {"mark_of_the_web", "comments"}
HIGH_NAMES = {
    # device
    "Make", "Model", "UniqueCameraModel", "MakerNote", "ImageUniqueID", "CameraFirmware",
    # people and descriptions
    "Artist", "Copyright", "ImageDescription", "UserComment", "Photographer", "ImageEditor",
    "ImageTitle", "DocumentName", "creator", "rights", "contributor", "publisher",
    "description", "title", "CaptionWriter", "AuthorsPosition", "CreatorContactInfo",
    "PersonInImage", "Regions", "Name",
    # software
    "Software", "HostComputer", "ProcessingSoftware", "CreatorTool", "softwareAgent",
    "OriginatingProgram", "ProgramVersion",
    # edit history and document identity
    "History", "DocumentID", "OriginalDocumentID", "InstanceID", "DerivedFrom", "Ingredients",
    "PreservedFileName",
    # places and people (IPTC / XMP)
    "City", "State", "Country", "CountryCode", "Location", "Sublocation", "LocationCreated",
    "LocationShown", "Sub-location", "Province-State", "Country-PrimaryLocationName",
    "Country-PrimaryLocationCode", "ContentLocationName", "ContentLocationCode",
    "By-line", "By-lineTitle", "Contact", "Writer-Editor", "Credit", "Source",
    "CopyrightNotice", "Caption-Abstract", "Headline", "ObjectName", "SpecialInstructions",
    "OriginalTransmissionReference", "LocalCaption",
}

# -- medium ----------------------------------------------------------------------------
MEDIUM_SECTIONS = {"other_app"}  # contents unknown: could be anything
MEDIUM_FIELDS = {
    ("icc", "device_manufacturer"),
    ("icc", "device_model"),
    ("icc", "device_manufacturer_description"),
    ("icc", "device_model_description"),
}
MEDIUM_NAMES = {
    "ExposureTime", "FNumber", "ISOSpeedRatings", "ISOSpeed", "PhotographicSensitivity",
    "SensitivityType", "StandardOutputSensitivity", "RecommendedExposureIndex",
    "ExposureProgram", "ExposureMode", "ExposureBiasValue", "ExposureIndex",
    "MeteringMode", "WhiteBalance", "LightSource", "Flash", "FlashEnergy",
    "FocalLength", "FocalLengthIn35mmFilm", "ShutterSpeedValue", "ApertureValue",
    "MaxApertureValue", "BrightnessValue", "SubjectDistance", "SubjectDistanceRange",
    "SubjectArea", "SubjectLocation", "LensMake", "LensModel", "LensSpecification", "Lens",
    "LensID", "LensInfo", "SceneCaptureType", "SceneType", "DigitalZoomRatio", "Contrast",
    "Saturation", "Sharpness", "SensingMethod", "CustomRendered", "GainControl",
    "FocalPlaneXResolution", "FocalPlaneYResolution", "FocalPlaneResolutionUnit",
    "CompositeImage", "Temperature", "Humidity", "Pressure", "WaterDepth", "Acceleration",
    "CameraElevationAngle",
}


@dataclass(frozen=True)
class RiskFinding:
    level: str
    section: str
    """Section title, e.g. "GPS"."""
    label: str
    value: str
    section_key: str
    field_key: str


def _names(section_key: str, field_key: str, label: str) -> list[str]:
    """Local names to match against the rules (no prefixes, IFD names or [n])."""
    if section_key == "xmp":
        path = field_key[len("(extended) "):] if field_key.startswith("(extended) ") else field_key
        names = []
        for part in path.split("/"):
            part = part.split("[", 1)[0]
            names.append(part.split(":", 1)[-1])
        return names
    return [field_key.rsplit(".", 1)[-1], label]


def _is_date(name: str) -> bool:
    return ("Date" in name or name.startswith(("OffsetTime", "SubSecTime", "TimeCreated",
                                                 "TimeSent", "ReleaseTime", "ExpirationTime",
                                                 "DigitalCreationTime"))
            or name == "when")


def classify(section_key: str, field_key: str, label: str = "") -> str:
    """Risk level of one field."""
    if section_key == "icc":
        # ICC fields share names with personal fields (Description, Copyright) but
        # describe a color profile; only the device fields can fingerprint.
        return MEDIUM if (section_key, field_key) in MEDIUM_FIELDS else LOW
    names = _names(section_key, field_key, label)
    if (section_key in CRITICAL_SECTIONS or (section_key, field_key) in CRITICAL_FIELDS
            or any(n in CRITICAL_NAMES or n.startswith("GPS") for n in names)):
        return CRITICAL
    if (section_key in HIGH_SECTIONS
            or any(n in HIGH_NAMES or n.startswith("XP") or _is_date(n) for n in names)):
        return HIGH
    if (section_key in MEDIUM_SECTIONS or (section_key, field_key) in MEDIUM_FIELDS
            or any(n in MEDIUM_NAMES for n in names)):
        return MEDIUM
    return LOW


EXTENDED_XMP_PREFIX = "(extended) "


def _dedup_key(finding: RiskFinding) -> tuple[str, str, str] | None:
    """Findings with the same key describe the same property.

    Standard and Extended XMP often repeat the same properties; each is
    counted once. None means the finding is never merged.
    """
    if finding.section_key != "xmp":
        return None
    path = finding.field_key
    if path.startswith(EXTENDED_XMP_PREFIX):
        path = path[len(EXTENDED_XMP_PREFIX):]
    return (finding.section_key, path, finding.value)


def assess(sections: list[Section]) -> list[RiskFinding]:
    """Classify every metadata field, most severe first (stable within a level).

    A property found in both standard and Extended XMP with the same value
    is listed once, at the higher of the two levels.
    """
    findings: list[RiskFinding] = []
    merged: dict[tuple[str, str, str], int] = {}
    for section in sections:
        if section.key in NOT_METADATA_SECTIONS:
            continue
        for f in section.fields:
            finding = RiskFinding(classify(section.key, f.key, f.label), section.title,
                                  f.label, format_value(f.value), section.key, f.key)
            key = _dedup_key(finding)
            if key is not None and key in merged:
                i = merged[key]
                if LEVELS.index(finding.level) < LEVELS.index(findings[i].level):
                    findings[i] = finding
                continue
            if key is not None:
                merged[key] = len(findings)
            findings.append(finding)
    findings.sort(key=lambda finding: LEVELS.index(finding.level))
    return findings


def counts(findings: list[RiskFinding]) -> dict[str, int]:
    return {level: sum(1 for f in findings if f.level == level) for level in LEVELS}
