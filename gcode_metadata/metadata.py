"""Gcode-metadata tool for g-code files. Extracts preview pictures as well.
"""
from time import time

import base64
import json
import re
import os
import zipfile
from typing import Dict, Any, Type, Callable, List
from logging import getLogger

__version__ = "0.1.0"
__date__ = "14 Mar 2022"  # version date
__copyright__ = "(c) 2021 Prusa 3D"
__author_name__ = "Michal Zoubek"
__author_email__ = "link@prusa3d.com"
__author__ = f"{__author_name__} <{__author_email__}>"
__description__ = "Python library for extraction of metadata from g-code files"

__credits__ = "Ondřej Tůma, Martin Užák, Michal Zoubek, Tomáš Jozífek"
__url__ = "https://github.com/ondratu/gcode-metadata"

GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
CHARS_TO_REMOVE = ["/", "\\", "\"", "(", ")", "[", "]", "'"]

log = getLogger("connect-printer")

RE_ESTIMATED = re.compile(r"((?P<days>[0-9]+)d\s*)?"
                          r"((?P<hours>[0-9]+)h\s*)?"
                          r"((?P<minutes>[0-9]+)m\s*)?"
                          r"((?P<seconds>[0-9]+)s)?")


class UnknownGcodeFileType(ValueError):
    # pylint: disable=missing-class-docstring
    ...


def get_mmu_name(name):
    """Returns a name for the new list value item"""
    return f"{name} per tool"


def check_gcode_completion(path):
    """Check g-code integrity"""
    log.debug(path)


def thumbnail_from_bytes(data_input):
    """Parse thumbnail from bytes to string format because
    of JSON serialization requirements"""
    converted_data = {}
    for key, value in data_input.items():
        if isinstance(value, bytes):
            converted_data[key] = str(value, 'utf-8')
    return converted_data


def thumbnail_to_bytes(data_input):
    """Parse thumbnail from string to original bytes format"""
    converted_data = {}
    for key, value in data_input.items():
        converted_data[key] = bytes(value, 'utf-8')
    return converted_data


def estimated_to_seconds(value: str):
    """Convert string value to seconds.

    >>> estimated_to_seconds("2s")
    2
    >>> estimated_to_seconds("2m 2s")
    122
    >>> estimated_to_seconds("2M")
    120
    >>> estimated_to_seconds("2h 2m 2s")
    7322
    >>> estimated_to_seconds("2d 2h 2m 2s")
    180122
    >>> estimated_to_seconds("bad value")
    """
    match = RE_ESTIMATED.match(value.lower())
    if not match:
        return None
    values = match.groupdict()
    retval = int(values['days'] or 0) * 60 * 60 * 24
    retval += int(values['hours'] or 0) * 60 * 60
    retval += int(values['minutes'] or 0) * 60
    retval += int(values['seconds'] or 0)

    return retval or None


def same_or_nothing(value_list):
    """Returns a value only if all the values in a list are the same"""
    if any(x != value_list[0] for x in value_list):
        raise ValueError("The values were not the same")
    return value_list[0]


class MetaData:
    """Base MetaData class"""

    path: str
    thumbnails: Dict[str, bytes]  # dimensions: base64(data)
    data: Dict[str, str]  # key: value

    Attrs: Dict[str, Any] = {}  # metadata (name, convert_fct)

    def __init__(self, path: str):
        self.path = path
        self.thumbnails = {}
        self.data = {}

    @property
    def cache_name(self):
        """Create cache name in format .<filename>.cache

        >>> MetaData("/test/a.gcode").cache_name
        '/test/.a.gcode.cache'

        >>> MetaData("/test/a.txt").cache_name
        '/test/.a.txt.cache'

        >>> MetaData("x").cache_name
        '/.x.cache'

        """
        path_ = os.path.split(self.path)
        new_path = path_[0] + "/." + path_[1] + ".cache"
        return new_path

    def is_cache_fresh(self):
        """If cache is fresher than file, returns True"""
        try:
            file_time_created = os.path.getctime(self.path)
            cache_time_created = os.path.getctime(self.cache_name)
            return file_time_created < cache_time_created
        except FileNotFoundError:
            return False

    def save_cache(self):
        """Take metadata from source file and save them as JSON to
        <file_name>.cache file"""
        try:
            if self.thumbnails or self.data:
                dict_data = {
                    "thumbnails": thumbnail_from_bytes(self.thumbnails),
                    "data": self.data
                }
                with open(self.cache_name, "w", encoding='utf-8') as file:
                    json.dump(dict_data, file, indent=2)
        except PermissionError:
            log.warning("You don't have permission to save file here")

    def load_cache(self):
        """Load metadata values from <file_name>.cache file"""
        try:
            with open(self.cache_name, "r", encoding='utf-8') as file:
                cache_data = json.load(file)
            self.thumbnails = thumbnail_to_bytes(cache_data["thumbnails"])
            self.data = cache_data["data"]
        except (json.decoder.JSONDecodeError, FileNotFoundError, KeyError)\
                as err:
            raise ValueError(
                "JSON data not found or in incorrect format") from err

    def load(self, save_cache=True):
        """Extract and set metadata from `self.path`. Any metadata
        obtained from the path will be overwritten by metadata from
        the file if the metadata is contained there as well"""
        if self.is_cache_fresh():
            self.load_cache()
        else:
            self.load_from_path(self.path)
            self.load_from_file(self.path)
            if save_cache:
                self.save_cache()

    def load_from_file(self, path: str):
        """Load metadata and thumbnails from given `path`"""
        # pylint: disable=unused-argument

    def load_from_path(self, path: str):
        """Load metadata from given path (path, not its content),
        if possible.
        """
        # pylint: disable=unused-argument

    def set_data(self, data: Dict):
        """Helper function to save all items from `data` that
        match `self.Attr` in `self.data`.
        """
        for attr, conv in self.Attrs.items():
            val = data.get(attr)
            if not val:
                continue
            try:
                self.data[attr] = conv(val)
            except ValueError:
                log.warning("Could not convert using %s: %s", conv, val)

    def set_attr(self, name, value):
        """A helper function that saves attributes to `self.data`"""
        if name not in self.Attrs:
            return
        conv = self.Attrs[name]
        try:
            self.data[name] = conv(value)
        except ValueError:
            log.warning("Could not convert using %s: %s", conv, value)

    def __repr__(self):
        return f"Metadata: {self.path}, {len(self.data)} items, " \
               f"{len(self.thumbnails)} thumbnails"

    __str__ = __repr__


class MMUAttribute:
    """A class describing how to parse an attribute that can have
    multiple values for an mmu print

    conversion: a function that takes a list of values and returns a single
                one. ValueError is raised if that cannot be done"""

    # pylint: disable=too-few-public-methods

    def __init__(self,
                 separator: str=", ",
                 value_type: Type=float,
                 conversion: Callable[[List[Any]], Any] = same_or_nothing):
        self.separator: str = separator
        self.value_type = value_type
        self.conversion: Callable[[List[Any]], Any] = conversion

    def from_string(self, raw_value):
        """Parses the value from string, returns the list value as well as a
        value for a single tool info compatibility"""
        parsed = []
        for value in raw_value.split(self.separator):
            parsed.append(self.value_type(value))
        single_value = self.conversion(parsed)
        return parsed, single_value


class FDMMetaData(MetaData):
    """Class for extracting Metadata for FDM gcodes"""

    def set_attr(self, name, value):
        """Set an attribute, but add support for mmu list attributes"""
        if name in self.MMUAttrs:
            value_list, single_value = self.MMUAttrs[name].from_string(value)
            mmu_name = get_mmu_name(name)
            super().set_attr(mmu_name, value_list)
            super().set_attr(name, single_value)
        else:
            super().set_attr(name, value)

    # Metadata we are looking for and respective conversion functions

    MMUAttrs: Dict[str, MMUAttribute] = {
        "filament used [cm3]": MMUAttribute(
            separator=", ", value_type=float, conversion=sum
        ),
        "filament used [mm]": MMUAttribute(
            separator=", ", value_type=float, conversion=sum
        ),
        "filament used [g]": MMUAttribute(
            separator=", ", value_type=float, conversion=sum
        ),
        "filament cost": MMUAttribute(
            separator=", ", value_type=float, conversion=sum
        ),
        "filament_type": MMUAttribute(
            separator=";", value_type=str, conversion=same_or_nothing
        ),
        "temperature": MMUAttribute(
            separator=",", value_type=int, conversion=same_or_nothing
        ),
        "bed_temperature": MMUAttribute(
            separator=",", value_type=int, conversion=same_or_nothing
        ),
        "nozzle_diameter": MMUAttribute(
            separator=",", value_type=float, conversion=same_or_nothing
        )
    }

    # These keys are primary defined by PrusaSlicer
    # Keys ending in "per tool" mean there is a list inside
    Attrs = {
        "estimated printing time (normal mode)": str,
        "printer_model": str,
        "layer_height": float,
        "fill_density": str,
        "brim_width": int,
        "support_material": int,
        "ironing": int,
        "quiet_percent_present": bool,
        "quiet_left_present": bool,
        "quiet_change_in_present": bool,
        "normal_percent_present": bool,
        "normal_left_present": bool,
        "normal_change_in_present": bool,
        "layer_info_present": bool
    }

    # Add attributes that have multiple values in MMU print gcodes
    # pylint: disable=no-value-for-parameter
    for name, mmu_attribute in MMUAttrs.items():
        mmu_name = get_mmu_name(name)
        Attrs[name] = mmu_attribute.value_type
        Attrs[mmu_name] = list

    KEY_VAL_PAT = re.compile("; (?P<key>.*?) = (?P<value>.*)$")

    THUMBNAIL_BEGIN_PAT = re.compile(
        r"; thumbnail begin\s+(?P<dim>\w+) (?P<size>\d+)")
    THUMBNAIL_END_PAT = re.compile("; thumbnail end")

    M73_PAT = re.compile(r"^[^;]*M73 ?"
                         r"(?:Q(?P<quiet_percent>\d+))? ?"
                         r"(?:S(?P<quiet_left>\d+))? ?"
                         r"(?:C(?P<quiet_change_in>\d+))? ?"
                         r"(?:P(?P<normal_percent>\d+))? ?"
                         r"(?:R(?P<normal_left>\d+))? ?"
                         r"(?:D(?P<normal_change_in>\d+))? ?.*"
                         r"$")

    LAYER_CHANGE_PAT = re.compile(r"^;Z:\d+\.\d+$")

    # M73 info group and attribute names
    M73_ATTRS = {"quiet_percent": "quiet_percent_present",
                 "quiet_left": "quiet_left_present",
                 "quiet_change_in": "quiet_change_in_present",
                 "normal_percent": "normal_percent_present",
                 "normal_left": "normal_left_present",
                 "normal_change_in": "normal_change_in_present"}

    FDM_FILENAME_PAT = re.compile(
        r"^(?P<name>.*?)_(?P<height>[0-9.]+)mm_"
        r"(?P<material>\w+)_(?P<printer>\w+)_(?P<time>.*)\.")

    METADATA_START_OFFSET = 400000  # Read 400KB from the start
    METADATA_END_OFFSET = 40000  # Read 40KB at the end of the file
    # Number of times the search for M73 is going to repeat if info
    # is incomplete
    MAX_M73_SEARCH_BYTES = 100000

    TOLERATED_COUNT = 2

    def __init__(self, path: str):
        super().__init__(path)
        self.last_filename = None

        # When in the process of parsing an image, these won't be None
        # Parsed as in currently being parsed
        self.parsed_image_dimensions = None
        self.parsed_image_size = None
        self.parsed_image = None

        self.m73_searched_bytes = 0

    def load_from_path(self, path):
        """Try to obtain any usable metadata from the path itself"""
        filename = os.path.basename(path)
        match = self.FDM_FILENAME_PAT.match(filename)
        if match:
            data = {
                "name": match.group("name"),
                "layer_height": match.group("height"),
                "filament_type": match.group("material"),
                "printer_model": match.group("printer"),
                "estimated printing time (normal mode)": match.group("time"),
            }
            self.set_data(data)

    def from_comment_line(self, line):
        """Parses data from a line in the comments"""
        # thumbnail handling
        match = self.THUMBNAIL_BEGIN_PAT.match(line)
        if match:
            self.parsed_image_dimensions = match.group("dim")
            self.parsed_image_size = int(match.group("size"))
            self.parsed_image = []
            return

        match = self.THUMBNAIL_END_PAT.match(line)
        if match:
            image_data = "".join(self.parsed_image)
            self.thumbnails[self.parsed_image_dimensions] = image_data.encode()
            assert len(image_data) == self.parsed_image_size, len(image_data)

            self.parsed_image_dimensions = None
            self.parsed_image_size = None
            self.parsed_image = None
            return

        # We store the image data only during parsing. If actively parsing:
        if self.parsed_image is not None:
            line = line[2:].strip()
            self.parsed_image.append(line)

        # For the bulk of metadata comments
        match = self.KEY_VAL_PAT.match(line)
        if match:
            key, val = match.groups()
            self.set_attr(key, val)

        match = self.LAYER_CHANGE_PAT.match(line)
        if match:
            self.set_attr("layer_info_present", True)

    def from_gcode_line(self, line):
        """Parses data from a line in the gcode section"""
        match = self.M73_PAT.match(line)
        if match:
            for group_name, attribute_name in self.M73_ATTRS.items():
                if match.group(group_name) is not None:
                    self.set_attr(attribute_name, True)

    def load_from_file(self, path):
        """Load metadata from file
        Tries to use the quick_parse function. If it keeps failing,
        tries the old technique of parsing.

        :path: Path to the file to load the metadata from
        """
        # pylint: disable=redefined-outer-name
        # pylint: disable=invalid-name
        started_at = time()

        with open(path, "rb") as file_descriptor:
            self.quick_parse(file_descriptor)
            # parsing_new_file = self.last_filename != file_descriptor.name
            # self.evaluate_quick_parse(data, to_log=parsing_new_file):

        # self.last_filename = file_descriptor.name
        # self.set_data(data.meta)
        log.debug("Caching took %s", time() - started_at)

    def evaluate_quick_parse(self, to_log=False):
        """Evaluates if the parsed data is sufficient
        Can log the result
        Returns True if the data is sufficient"""
        wanted = set(self.Attrs.keys())
        got = set(self.data.keys())
        missed = wanted - got
        log.debug("Wanted: %s", wanted)
        log.debug("Parsed: %s", got)

        log.debug(
            "By not reading the whole file, "
            "we have managed to miss %s", list(missed))

        # --- Was parsing successful? ---

        if len(self.data) < 10:
            log.warning("Not enough info found, file not uploaded yet?")
            return False

        if missed and to_log:
            if len(missed) == len(wanted):
                log.warning("No metadata parsed!")
            else:
                log.warning("Metadata missing %s", missed)
            if len(missed) <= self.TOLERATED_COUNT:
                log.warning("Missing meta tolerated, missing count < %s",
                            self.TOLERATED_COUNT)

        if len(missed) > self.TOLERATED_COUNT:
            return False

        return True

    def quick_parse(self, file_descriptor):
        """Parse metadata on the start and end of the file"""
        position = 0
        size = file_descriptor.seek(0, os.SEEK_END)
        file_descriptor.seek(0)
        while position != size:
            close_to_start = position < self.METADATA_START_OFFSET
            close_to_end = position > size - self.METADATA_END_OFFSET
            if not close_to_start and not close_to_end:
                # Skip the middle part of the file
                position = size - self.METADATA_END_OFFSET
                file_descriptor.seek(position)
            line = file_descriptor.readline()
            position += len(line)
            if line.startswith(b";"):
                self.from_comment_line(line.decode("UTF-8"))
            else:
                if self.percent_of_m73_data() == 100:
                    continue
                if self.m73_searched_bytes > self.MAX_M73_SEARCH_BYTES:
                    continue

                self.from_gcode_line(line.decode("UTF-8"))
                self.m73_searched_bytes += len(line)

    def percent_of_m73_data(self):
        """Report what percentage of M73 attributes has been found"""
        count = len(self.M73_ATTRS)
        present = 0
        for attribute in self.M73_ATTRS.values():
            if attribute in self.data:
                present += 1
        return (present/count) * 100


class SLMetaData(MetaData):
    """Class that can extract available metadata and thumbnails from
    ziparchives used by SL1 slicers"""

    # Thanks to Bruno Carvalho for sharing code to extract metadata and
    # thumbnails!

    Attrs = {
        "printer_model": str,
        "printTime": int,
        "faded_layers": int,
        "exposure_time": float,
        "initial_exposure_time": float,
        "max_initial_exposure_time": float,
        "max_exposure_time": float,
        "min_initial_exposure_time": float,
        "min_exposure_time": float,
        "layer_height": float,
        "materialName": str,
        "fileCreationTimestamp": str,
    }

    THUMBNAIL_NAME_PAT = re.compile(r"(?P<dim>\d+x\d+)")

    def load(self, save_cache=True):
        """Load metadata"""
        try:
            super().load(save_cache)
        except zipfile.BadZipFile:
            # NOTE can't import `log` from __init__.py because of
            #  circular dependencies
            print("%s is not a valid SL1 archive", self.path)

    def load_from_file(self, path: str):
        """Load SL1 metadata

        :path: path to the file to load the metadata from
        """
        data = self.extract_metadata(path)
        self.set_data(data)

        self.thumbnails = self.extract_thumbnails(path)

    @staticmethod
    def extract_metadata(path: str) -> Dict[str, str]:
        """Extract metadata from `path`.

        :param path: zip file
        :returns Dictionary with metadata name as key and its
            value as value
        """
        # pylint: disable=invalid-name
        data = {}
        with zipfile.ZipFile(path, "r") as zip_file:
            for fn in ("config.ini", "prusaslicer.ini"):
                config_file = zip_file.read(fn).decode("utf-8")
                for line in config_file.splitlines():
                    key, value = line.split(" = ")
                    try:
                        data[key] = json.loads(value)
                    except json.decoder.JSONDecodeError:
                        data[key] = value
        return data

    @staticmethod
    def extract_thumbnails(path: str) -> Dict[str, bytes]:
        """Extract thumbnails from `path`.

        :param path: zip file
        :returns Dictionary with thumbnail dimensions as key and base64
            encoded image as value.
        """
        thumbnails: Dict[str, bytes] = {}
        with zipfile.ZipFile(path, "r") as zip_file:
            for info in zip_file.infolist():
                if info.filename.startswith("thumbnail/"):
                    data = zip_file.read(info.filename)
                    data = base64.b64encode(data)
                    dim = SLMetaData.THUMBNAIL_NAME_PAT.findall(
                        info.filename)[-1]
                    thumbnails[dim] = data
        return thumbnails


def get_metadata(path: str, save_cache=True, filename=None):
    """Returns the Metadata for given `path`

    :param path: Gcode file
    :param save_cache: Boolean if cache should be saved
    :filename: Filename in case of temp file
    """
    # pylint: disable=redefined-outer-name
    if filename:
        fnl = filename.lower()
    else:
        fnl = path.lower()

    metadata: MetaData
    if fnl.lower().endswith(GCODE_EXTENSIONS):
        metadata = FDMMetaData(path)
    elif fnl.lower().endswith(".sl1"):
        metadata = SLMetaData(path)
    else:
        raise UnknownGcodeFileType(path)

    metadata.load(save_cache)
    return metadata


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")
