from pydantic import BaseModel

_KNOWN_CATEGORIES = frozenset({"gameplay", "tutorial", "podcast"})


class IngestResult(BaseModel):
    drive_file_id: str
    name: str
    # Normalised category derived from the Drive folder name
    category: str
    # True when the folder name was not in the known set and was defaulted to generic
    category_was_unknown: bool = False


def classify_from_folder(drive_file_id: str, name: str, folder_name: str) -> IngestResult:
    """Derive content category from the Drive parent folder name."""
    if folder_name in _KNOWN_CATEGORIES:
        return IngestResult(
            drive_file_id=drive_file_id,
            name=name,
            category=folder_name,
        )
    return IngestResult(
        drive_file_id=drive_file_id,
        name=name,
        category="generic",
        category_was_unknown=True,
    )
