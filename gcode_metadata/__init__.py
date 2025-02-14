"""Init file describing every importable object"""

from .metadata import MetaData, FDMMetaData, SLMetaData, get_metadata, \
    UnknownGcodeFileType, estimated_to_seconds, get_preview, get_icon, \
    get_meta_class, PRINTERS

__version__ = "0.2.1"
__date__ = "14 Feb 2025"  # version date
__copyright__ = "(c) 2025 Prusa 3D"
__author_name__ = "Prusa Connect Developers"
__author_email__ = "link@prusa3d.cz"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python library for extraction of metadata from g-code files"

__credits__ = "Ondřej Tůma, Michal Zoubek, Tomáš Jozífek, Šárka Faloutová"
__url__ = "https://github.com/prusa3d/gcode-metadata"

__all__ = [
    "MetaData", "FDMMetaData", "SLMetaData", "get_metadata",
    "UnknownGcodeFileType", "estimated_to_seconds", "get_preview", "get_icon",
    "get_meta_class", "PRINTERS"
]
