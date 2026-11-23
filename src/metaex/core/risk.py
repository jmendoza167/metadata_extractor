"""Privacy risk levels for metadata fields: the ONE place they are defined.

Parsers only report what they find; how risky each finding is gets decided
here, so the rules stay consistent across formats and easy to review.

Levels:
- critical: reveals where you were, hidden extra images or files, or
  identifies your exact device or you by name (GPS, thumbnails, MPF images,
  data after the end of the image or hidden inside the image data, serial
  numbers, owner name, local file paths in SVG, which reveal user names)
- high: identifies device model, dates, authors, software, edit history and
  document IDs, download origin, comments, PNG text chunks, AI generation
  data (prompts, settings), PNG modification time, Content Credentials (C2PA),
  GIF plain text and unknown application extensions (contents unknown),
  SVG metadata, processing instructions and editor data
- medium: camera settings, ICC device info and profile names, HDR mastering
  display info, unknown chunks or segments (fingerprinting, unknown contents),
  SVG <title>/<desc> text
- low: everything else (including zero padding after the end of the image,
  which formats report in a separate "padding" section)

Office documents (DOCX, XLSX, PPTX, OpenDocument) have their own table,
OFFICE_SECTIONS and OFFICE_FIELDS:
- critical: author and last editor, people's user IDs (account e-mail
  addresses), file paths that name a user, deleted text kept by tracked
  changes, unused parts and data hidden in the ZIP container, the authors
  of embedded Office files (and embedded images with GPS, by the image rules)
- high: company, manager, template, dates, application and version, comment
  and revision authors, people's names, sensitivity labels, custom
  properties and custom XML, document variables, external links and data
  connections, macros, embedded objects, hidden text, ZIP comments and
  timestamps
- medium: total editing time, revision number, printer settings, revision
  IDs (rsids), thumbnail, hidden sheets and slides, speaker notes
- low: counts and everything else
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
CRITICAL_SECTIONS = {"gps", "mpf", "trailing",
                     "hidden_image_data",  # PNG: extra bytes inside the image data
                     "local_paths"}  # SVG: absolute paths reveal user names
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
HIGH_SECTIONS = {"mark_of_the_web", "comments",
                 # PNG: text chunks, AI prompts/settings, tIME, C2PA manifests
                 "text", "ai", "time", "content_credentials",
                 # GIF: Plain Text extensions, unknown application extensions
                 "plain_text", "app_extensions",
                 # SVG: <metadata> (RDF / Dublin Core), processing instructions,
                 # editor namespaces and attributes
                 "svg_metadata", "processing_instructions", "editor"}
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
MEDIUM_SECTIONS = {"other_app", "unknown",  # contents unknown: could be anything
                   "title_desc"}  # SVG <title>/<desc>: accessibility text, often a name
MEDIUM_FIELDS = {
    ("icc", "device_manufacturer"),
    ("icc", "device_model"),
    ("icc", "device_manufacturer_description"),
    ("icc", "device_model_description"),
    ("icc", "profile_name"),  # PNG iCCP: free text, sometimes names a display
    ("color", "mDCv"),  # HDR mastering display
    ("color", "cLLI"),
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


# -- Office documents ------------------------------------------------------------------
OFFICE_SECTIONS = {
    # section key -> level of its fields, unless OFFICE_FIELDS says otherwise
    "office_core": LOW, "office_app": LOW, "office_comments": LOW, "office_people": HIGH,
    "tracked_changes": LOW, "hidden_content": MEDIUM,
    "office_custom": HIGH, "office_labels": HIGH, "office_custom_xml": HIGH,
    "docx_variables": HIGH, "office_external": HIGH, "xlsx_external_data": HIGH,
    "xlsx_workbook": MEDIUM, "macros": HIGH, "office_embedded": HIGH,
    "office_thumbnail": MEDIUM, "printer_settings": MEDIUM, "revision_ids": MEDIUM,
    "office_hidden_parts": CRITICAL, "embedded_images": LOW,
    "zip_hidden": CRITICAL, "zip_comment": HIGH, "zip_extra": HIGH, "zip_entries": LOW,
    "odf_meta": LOW, "odf_user_defined": HIGH, "odf_settings": LOW,
}
OFFICE_FIELDS = {
    # (section key, field key or its last part after "." or ":") -> level
    ("office_core", "creator"): CRITICAL, ("office_core", "lastModifiedBy"): CRITICAL,
    ("office_core", "created"): HIGH, ("office_core", "modified"): HIGH,
    ("office_core", "lastPrinted"): HIGH, ("office_core", "revision"): MEDIUM,
    ("office_app", "Company"): HIGH, ("office_app", "Manager"): HIGH,
    ("office_app", "Template"): HIGH, ("office_app", "Application"): HIGH,
    ("office_app", "AppVersion"): HIGH, ("office_app", "HyperlinkBase"): HIGH,
    ("office_app", "HLinks"): HIGH, ("office_app", "TotalTime"): MEDIUM,
    ("office_comments", "authors"): HIGH, ("office_comments", "initials"): HIGH,
    ("office_comments", "dates"): HIGH, ("office_comments", "dateUtc"): HIGH,
    ("office_people", "userId"): CRITICAL, ("office_people", "providerId"): MEDIUM,
    ("tracked_changes", "deleted_text"): CRITICAL,
    ("tracked_changes", "deleted_characters"): CRITICAL,
    ("tracked_changes", "authors"): HIGH, ("tracked_changes", "dates"): HIGH,
    ("hidden_content", "hidden_text"): HIGH, ("hidden_content", "hidden_text_runs"): HIGH,
    ("hidden_content", "glossary"): HIGH, ("hidden_content", "hidden_section"): HIGH,
    ("xlsx_workbook", "absPath"): HIGH, ("xlsx_workbook", "fileVersion"): HIGH,
    ("xlsx_workbook", "documentId"): HIGH, ("xlsx_workbook", "uidLastSave"): HIGH,
    ("office_embedded", "creator"): CRITICAL, ("office_embedded", "lastModifiedBy"): CRITICAL,
    ("zip_entries", "entry_times"): HIGH,
    # OpenDocument (meta.xml, settings.xml)
    ("odf_meta", "initial-creator"): CRITICAL, ("odf_meta", "creator"): CRITICAL,
    ("odf_meta", "creation-date"): HIGH, ("odf_meta", "date"): HIGH,
    ("odf_meta", "print-date"): HIGH, ("odf_meta", "printed-by"): CRITICAL,
    ("odf_meta", "generator"): HIGH, ("odf_meta", "template"): HIGH,
    ("odf_meta", "template-title"): HIGH, ("odf_meta", "template-date"): HIGH,
    ("odf_meta", "editing-duration"): MEDIUM, ("odf_meta", "editing-cycles"): MEDIUM,
    ("odf_settings", "PrinterName"): MEDIUM, ("odf_settings", "PrinterSetup"): MEDIUM,
    ("odf_settings", "PrinterPaperFromSetup"): MEDIUM,
}


def _office_level(section_key: str, field_key: str, value: str) -> str:
    """Level of a field in an Office section (see OFFICE_SECTIONS)."""
    from metaex.core.paths import local_paths, user_name

    if any(user_name(p) for p in local_paths(value)):
        return CRITICAL  # a file path that names a user
    for key in (field_key, field_key.rsplit(".", 1)[-1], field_key.split(".", 1)[0],
                field_key.rsplit(":", 1)[-1]):
        level = OFFICE_FIELDS.get((section_key, key))
        if level is not None:
            return level
    return OFFICE_SECTIONS[section_key]


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
    if section_key in ("xmp", "svg_metadata"):  # RDF property paths
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


def classify(section_key: str, field_key: str, label: str = "", value: str = "") -> str:
    """Risk level of one field (``value`` is its displayed value; only Office
    sections look at it, for file paths that name a user)."""
    if section_key in OFFICE_SECTIONS:
        return _office_level(section_key, field_key, value)
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
            value = format_value(f.value)
            finding = RiskFinding(classify(section.key, f.key, f.label, value), section.title,
                                  f.label, value, section.key, f.key)
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
