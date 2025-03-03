"""Gcode-metadata tool for g-code files. Extracts preview pictures as well.
"""
from time import time

import base64
import json
import re
import os
import zipfile
from typing import Dict, Any, Type, Callable, List, Optional
from logging import getLogger
from importlib.metadata import version

# pylint: disable=too-many-lines

GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
SLA_EXTENSIONS = ("sl1", "sl1s", "m1")
CHARS_TO_REMOVE = ["/", "\\", "\"", "(", ")", "[", "]", "'"]

log = getLogger("connect-printer")

RE_ESTIMATED = re.compile(r"((?P<days>[0-9]+)d\s*)?"
                          r"((?P<hours>[0-9]+)h\s*)?"
                          r"((?P<minutes>[0-9]+)m\s*)?"
                          r"((?P<seconds>[0-9]+)s)?")
PRINTERS = [
    'COREONE',
    'HT90',
    'MK2.5',
    'MK2.5S',
    'MK2.5MMU2',
    'MK2.5SMMU2S',
    'MK3',
    'MK3S',
    'MK3MMU2',
    'MK3SMMU2S',
    'MK3MMU3',
    'MK3SMMU3',
    'MK3.9',  # no IS in name as it shipped with IS FW
    'MK3.9MMU3',
    'MK3.9S',
    'MK3.9SMMU3',
    'MK3.5',
    'MK3.5MMU3',
    'MK3.5S',
    'MK3.5SMMU3',
    'MK4',
    # 'MK4MMU3',  # MMU3 for MK4 only for MK4IS
    'MK4ISMMU3',
    'MK4IS',
    'MK4S',
    'MK4SMMU3',  # no IS in name as it shipped with IS FW
    'MINI',
    'MINIIS',
    'XL',
    'XL2',
    'XL5',
    'XLIS',
    'XL2IS',
    'XL5IS',
    'SL1',
    'SL1S',
    'M1',
    'iX',
]

PRINTERS.sort(key=len, reverse=True)

MATERIALS = [
    'PLA', 'PETG', 'ABS', 'ASA', 'FLEX', 'HIPS', 'EDGE', 'NGEN', 'PA', 'PVA',
    'PCTG', 'PP', 'PC', 'PEBA', 'CPE', 'PVB', 'PET', 'PLA Tough', 'METAL',
    'TPU', 'NYLON', "IGLIDUR"
]

IMAGE_FORMATS = ['PNG', 'JPG']


class UnknownGcodeFileType(ValueError):
    # pylint: disable=missing-class-docstring
    ...


class ImageInfo:
    """A class to hold image info for thumbnail selection purposes"""

    def __init__(self, width, height, format_):
        self.width = width
        self.height = height
        self.format = format_

    @property
    def ratio(self):
        """Gets the image ratio"""
        return self.width / self.height

    @staticmethod
    def dimension_badness(width, target_width):
        """Returns a badness score for the size difference between the image
        and the target dimension. The score is higher when the dimension is
        smaller than the target dimension"""
        size_difference = width - target_width
        if size_difference < 0:
            return (abs(size_difference) + 2)**2
        return size_difference

    def badness(self, target: "ImageInfo", aspect_ratio_weight=1):
        """Returns a badness score for this image compared to the target"""
        # This gives a value between 1 and infinity
        ar_badness = (max(self.ratio, target.ratio) /
                      min(self.ratio, target.ratio))

        width_badness = self.dimension_badness(self.width, target.width)
        height_badness = self.dimension_badness(self.height, target.height)
        size_badness = width_badness + height_badness

        # The aspect ratio weight of 2 would square aspect ratio badness
        # while using a square root on the size badness
        # As the aspect ratio minimum is 1, let's make the lowest value
        # 0.01 by subtracting 0.99
        weighted_ar_badness = ar_badness**aspect_ratio_weight - 0.99
        weighted_size_badness = size_badness**(1 / aspect_ratio_weight)

        return weighted_ar_badness * weighted_size_badness

    @staticmethod
    def from_thumbnail_info(info: str):
        """Parses thumbnail info from string thumbnail key format"""
        string_resolution, format_ = info.split("_")
        width, height = tuple(map(int, string_resolution.split('x')))
        return ImageInfo(width, height, format_)

    def to_thumbnail_info(self):
        """Returns thumbnail info in string thumbnail key format"""
        return f"{self.width}x{self.height}_{self.format}"

    def __str__(self):
        return self.to_thumbnail_info()

    def __repr__(self):
        return f'ImageInfo("{str(self)}")'


def get_mmu_name(name):
    """Returns a name for the new list value item"""
    return f"{name} per tool"


def check_gcode_completion(path):
    """Check g-code integrity"""
    log.debug(path)


def from_bytes(data) -> str:
    """Convert data in bytes to string"""
    return str(data, 'utf-8')


def to_bytes(data) -> bytes:
    """Convert string to data in bytes"""
    return bytes(data, 'utf-8')


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


def extract_data(input_string):
    """Extracts metadata from the filename
        >>> extract_data("HP_PLA,PLA_MK3SMMU3_3h22m.gcode") #doctest: +ELLIPSIS
        {... 'material': 'PLA', 'printer': 'MK3SMMU3', 'time': '3h22m'}
        >>> extract_data("sh_bn_0.6n_0.32mm_PETG_MK4_8h55m.gcode")['material']
        'PETG'
        >>> extract_data("PLA_0.6n 0.32mm_MK3S_1d1h42m")['printer']
        'MK3S'
        >>> extract_data("42.gcode")['printer'] is None
        True
        >>> extract_data("Tisk tohoto souboru bude trvat 1d18h15m")['time']
        '1d18h15m'
        >>> extract_data("+ěščřžýáíé / -.:<>.gcode") #doctest: +ELLIPSIS
        {'name': None, ..., 'material': None, 'printer': None, 'time': None}
        >>> extract_data("Tohle je PLA, nebo PETG, nevim.gcode")['material']
        'PLA'
        >>> extract_data("ßüäö")['printer'] is None
        True
    """

    # mat_pat = material pattern, prt_pat = printer pattern
    patterns = [
        (r"(.*?)(?=[0-9.]+n|mm|{mat_pat}|{prt_pat}|\d+[dhm]+)", 'name'),
        (r"([0-9.]+)n", 'nozzle'), (r"([0-9.]+)mm", 'height'),
        (r"(?:" + "|".join(MATERIALS) + r")", 'material'),
        (r"(?:" + "|".join(PRINTERS) + r")", 'printer'),
        (r"(\d+[dhm]+(?:\d*[dhm]+)*)(?!\w)", 'time')
    ]

    data = {}
    for pattern, key in patterns:
        pattern = pattern.format(mat_pat="|".join(MATERIALS),
                                 prt_pat="|".join(PRINTERS))
        match = re.search(pattern, input_string)
        if match:
            if key in ('nozzle', 'height'):
                data[key] = float(match.group(1))
            else:
                data[key] = match.group()
        else:
            data[key] = None

    return data


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
        # buffer which keeps last unfinished line from previous chunk
        self.chunk_buffer = b''
        self.position = 0  # current position in chunked read

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
        """Checks if we can use the current cache file"""
        return self.is_cache_recent() and self.is_cache_correct_version()

    def is_cache_recent(self):
        """Checks if the cache file is newer than the source file"""
        try:
            file_time_created = os.path.getctime(self.path)
            cache_time_created = os.path.getctime(self.cache_name)
        except FileNotFoundError:
            return False

        return file_time_created < cache_time_created

    def is_cache_correct_version(self):
        """Checks if the cache file was created with the same version
        of gcode-metadata"""

        def isallowed(char):
            """Filters out disallowed characters, used with str.filter"""
            return char not in "\",\n} "

        # This expects the first item in the json file to be the
        # gcode-metadata version, with which the cache was created.
        # If it's not there, or the version is different, the cache is deleted
        with open(self.cache_name, "r", encoding="utf-8") as file:
            for line in file:
                if text := "".join(filter(str.isalpha, line)):
                    if text.startswith("version"):
                        break
                    return False
            else:  # didn't reach break
                return False
            first_pair = line.split(",", 1)[0]
            _, version_part = first_pair.split(":", 1)
            file_version = "".join(filter(isallowed, version_part))
            if file_version == version('py-gcode-metadata'):
                return True

        return False

    def save_cache(self):
        """Take metadata from source file and save them as JSON to
        <file_name>.cache file.
        Parse thumbnail from bytes to string format because of JSON
        serialization requirements"""

        def get_cache_data(info):
            width, height = info.width, info.height

            return {
                "resolution": f"{width}x{height}",
                "data": from_bytes(self.thumbnails[info.to_thumbnail_info()]),
                "format": info.format
            }

        try:
            if self.data:
                cache = {
                    "version": version('py-gcode-metadata'),
                    "metadata": self.data,
                }

                if self.thumbnails:

                    if preview := get_preview(self.thumbnails):
                        cache["preview"] = get_cache_data(preview)

                    if icon := get_icon(self.thumbnails):
                        cache["icon"] = get_cache_data(icon)

                with open(self.cache_name, "w", encoding='utf-8') as file:
                    json.dump(cache, file, indent=2)
        except PermissionError:
            log.warning("You don't have permission to save file here")

    def load_cache(self):
        """Load metadata values from <file_name>.cache file"""
        try:
            with open(self.cache_name, "r", encoding='utf-8') as file:
                cache_data = json.load(file)
                preview = cache_data.get("preview")
                icon = cache_data.get("icon")

            self.thumbnails = {}

            if preview:
                key = f"{preview['resolution']}_{preview['format']}"
                self.thumbnails[key] = to_bytes(preview["data"])
            if icon:
                key = f"{icon['resolution']}_{icon['format']}"
                self.thumbnails[key] = to_bytes(icon["data"])

            self.data = cache_data["metadata"]
        except (json.decoder.JSONDecodeError, FileNotFoundError, KeyError)\
                as err:
            raise ValueError(
                "JSON data not found or in incorrect format") from err

    def load(self, save_cache=True):
        """Extract and set metadata from `self.path`. Any metadata
        obtained from the path will be overwritten by metadata from
        the file if the metadata is contained there as well"""
        cache_loaded = False
        if self.is_cache_fresh():
            try:
                self.load_cache()
                cache_loaded = True
            except Exception:  # pylint: disable=broad-except
                log.warning("Failed loading cache for: %s", self.path)
        if not cache_loaded:
            try:
                self.load_from_path(self.path)
                self.load_from_file(self.path)
            except Exception:  # pylint: disable=broad-except
                log.exception("Failed loading metadata from: %s", self.path)
            else:
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

    def load_from_chunk(self, data: bytes, size: int):
        """Process given chunk array of data.
        :data: data of a chunk.
        :size: size of the file."""
        # pylint: disable=unused-argument

    def set_data(self, data: Dict):
        """Helper function to save all items from `data` that
        match `self.Attr` in `self.data`.
        """
        for key, value in data.items():
            self.set_attr(key, value)

    def set_attr(self, name, value):
        """A helper function that saves attributes to `self.data`"""
        if name not in self.Attrs:
            return
        if value is None:
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
                 separator: str = ", ",
                 value_type: Type = float,
                 conversion: Callable[[List[Any]], Any] = same_or_nothing):
        self.separator: str = separator
        self.value_type = value_type
        self.conversion: Callable[[List[Any]], Any] = conversion

    def parse_tools(self, raw_value: Any) -> tuple[list[Any], Any]:
        """Parses the value from raw_value, returns the list value as well as a
        value for a single tool info compatibility
        """
        try:
            values = raw_value.split(self.separator)
        except AttributeError:
            values = [raw_value]

        parsed: list[Any] = []
        for value in values:
            try:
                parsed.append(self.value_type(value))
            except ValueError:
                return [], None
        try:
            single_value = self.conversion(parsed)
        except ValueError:
            single_value = None
        return parsed, single_value


class FDMMetaData(MetaData):
    """Class for extracting Metadata for FDM gcodes"""

    # pylint: disable=too-many-instance-attributes

    def set_attr(self, name, value):
        """Set an attribute, but add support for mmu list attributes"""
        if value == '""':  # e.g. when no extruder_colour
            return
        if value is None:
            return
        if name in self.MMUAttrs:
            value_list, single_value = self.MMUAttrs[name].parse_tools(value)
            mmu_name = get_mmu_name(name)
            if len(value_list) > 1:
                super().set_attr(mmu_name, value_list)
            if single_value is not None:
                super().set_attr(name, single_value)
        else:
            super().set_attr(name, value)

    # Metadata we are looking for and respective conversion functions

    MMUAttrs: Dict[str,
                   MMUAttribute] = {
                       "filament used [cm3]":
                       MMUAttribute(separator=", ",
                                    value_type=float,
                                    conversion=sum),
                       "filament used [mm]":
                       MMUAttribute(separator=", ",
                                    value_type=float,
                                    conversion=sum),
                       "filament used [g]":
                       MMUAttribute(separator=", ",
                                    value_type=float,
                                    conversion=sum),
                       "filament cost":
                       MMUAttribute(separator=", ",
                                    value_type=float,
                                    conversion=sum),
                       "filament_type":
                       MMUAttribute(separator=";",
                                    value_type=str,
                                    conversion=same_or_nothing),
                       "temperature":
                       MMUAttribute(separator=",",
                                    value_type=int,
                                    conversion=same_or_nothing),
                       "bed_temperature":
                       MMUAttribute(separator=",",
                                    value_type=int,
                                    conversion=same_or_nothing),
                       "nozzle_diameter":
                       MMUAttribute(separator=",",
                                    value_type=float,
                                    conversion=same_or_nothing),
                       "extruder_colour":
                       MMUAttribute(separator=";",
                                    value_type=str,
                                    conversion=same_or_nothing),
                       "nozzle_high_flow":
                       MMUAttribute(separator=",",
                                    value_type=int,
                                    conversion=same_or_nothing),
                       "filament_abrasive":
                       MMUAttribute(separator=",",
                                    value_type=int,
                                    conversion=same_or_nothing),
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
        "layer_info_present": bool,
        "max_layer_z": float,
        "objects_info": json.loads,
    }

    # Add attributes that have multiple values in MMU print gcodes
    # pylint: disable=no-value-for-parameter
    for name, mmu_attribute in MMUAttrs.items():
        mmu_name = get_mmu_name(name)
        Attrs[name] = mmu_attribute.value_type
        Attrs[mmu_name] = list

    KEY_VAL_PAT = re.compile("; (?P<key>.*?) = (?P<value>.*)$")

    THUMBNAIL_BEGIN_PAT = re.compile(
        r"; thumbnail_?(?P<format>QOI|JPG|) begin (?P<dim>[\w ]+) "
        r"(?P<size>\d+)")
    THUMBNAIL_END_PAT = re.compile("; thumbnail_?(QOI|JPG)? end")

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
    M73_ATTRS = {
        "quiet_percent": "quiet_percent_present",
        "quiet_left": "quiet_left_present",
        "quiet_change_in": "quiet_change_in_present",
        "normal_percent": "normal_percent_present",
        "normal_left": "normal_left_present",
        "normal_change_in": "normal_change_in_present"
    }

    METADATA_START_OFFSET = 800000  # Read 800KB from the start
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
        self.img_format = None
        self.img_dimensions = None
        self.img_size = None
        self.img = None

        self.m73_searched_bytes = 0

    def load_from_path(self, path):
        """Try to obtain any usable metadata from the path itself"""
        filename = os.path.basename(path)
        data = extract_data(filename)

        result = {
            "name": data["name"],
            "nozzle_diameter": data["nozzle"],
            "layer_height": data["height"],
            "filament_type": data["material"],
            "printer_model": data["printer"],
            "estimated printing time (normal mode)": data["time"]
        }

        self.set_data(result)

    def from_comment_line(self, line):
        """Parses data from a line in the comments"""
        # thumbnail handling
        match = self.THUMBNAIL_BEGIN_PAT.match(line)
        if match:
            img_format = match.group("format")

            # PNG is not explicitly described in thumbnails header
            self.img_format = "PNG" if img_format == "" else img_format
            self.img_dimensions = match.group("dim")
            self.img_size = int(match.group("size"))
            self.img = []
            return

        match = self.THUMBNAIL_END_PAT.match(line)
        if match:
            img_data = "".join(self.img)
            key = f"{self.img_dimensions}_{self.img_format}"
            self.thumbnails[key] = img_data.encode()
            assert len(img_data) == self.img_size, len(img_data)

            self.img_format = None
            self.img_dimensions = None
            self.img_size = None
            self.img = None
            return

        # We store the image data only during parsing. If actively parsing:
        if self.img is not None:
            self.img.append(line[2:].strip())

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

    def process_line(self, line: bytes):
        """Try to read info from given byteaaray line"""
        if line.startswith(b";"):
            self.from_comment_line(line.decode("UTF-8"))
        else:
            if self.percent_of_m73_data() == 100:
                return
            if self.m73_searched_bytes > self.MAX_M73_SEARCH_BYTES:
                return
            self.from_gcode_line(line.decode("UTF-8"))
            self.m73_searched_bytes += len(line)

    def metadata_area(self, position: int, size: int) -> bool:
        """Finds out if the current position contains metadata"""
        close_to_start = position < self.METADATA_START_OFFSET
        close_to_end = position > size - self.METADATA_END_OFFSET
        if not close_to_start and not close_to_end:
            return False
        return True

    def quick_parse(self, file_descriptor):
        """Parse metadata on the start and end of the file"""
        position = 0
        size = file_descriptor.seek(0, os.SEEK_END)
        file_descriptor.seek(0)
        while position != size:
            if not self.metadata_area(position, size):
                # Skip the middle part of the file
                position = size - self.METADATA_END_OFFSET
                file_descriptor.seek(position)
            line = file_descriptor.readline()
            position += len(line)
            self.process_line(line)

    def load_from_chunk(self, data: bytes, size: int):
        """Process given chunk array of data.
        :data: data of a chunk.
        :size: size of the file."""
        metadata_area = self.metadata_area(self.position, size)
        self.position += len(data)
        if not metadata_area:
            metadata_end_offset_position = size - self.METADATA_END_OFFSET
            # end offset not in chunk data
            if metadata_end_offset_position > self.position:
                self.chunk_buffer = b''
                return
            data = data[:metadata_end_offset_position]
        data = self.chunk_buffer + data
        lines = data.split(b"\n")
        # last line was cut in middle, save it to buffer to process it
        # with next chunk of data
        if not data.endswith(b"\n"):
            self.chunk_buffer = lines.pop()
        else:
            self.chunk_buffer = b''

        for line in lines:
            self.process_line(line)

    def percent_of_m73_data(self):
        """Report what percentage of M73 attributes has been found"""
        count = len(self.M73_ATTRS)
        present = 0
        for attribute in self.M73_ATTRS.values():
            if attribute in self.data:
                present += 1
        return (present / count) * 100


class SLKeys:
    """Class that maps keys from config.json to
    more readable names"""

    def __init__(self, key_to_parse: str):
        self.key_to_parse = key_to_parse

    KeyMapping = {
        "printTime": "estimated_print_time",
        "expTime": "exposure_time",
        "expTimeFirst": "exposure_time_first",
        "layerHeight": "layer_height",
        "materialName": "material",
        "printerModel": "printer_model",
        "usedMaterial": "resin_used_ml",
    }

    @staticmethod
    def keys():
        """Returns all keys"""
        return list(SLKeys.KeyMapping.keys()) + list(
            SLKeys.KeyMapping.values())

    @property
    def key(self):
        """Returns correct key"""
        return self.KeyMapping.get(self.key_to_parse, self.key_to_parse)


class SLMetaData(MetaData):
    """Class that can extract available metadata and thumbnails from
    ziparchives used by SL1 slicers"""

    # Thanks to Bruno Carvalho for sharing code to extract metadata and
    # thumbnails!

    Attrs = {
        # to unify sl float with fdm int value
        "estimated_print_time": lambda x: int(float(x)),
        "layer_height": float,  # mm
        "material": str,
        "exposure_time": float,  # s
        "exposure_time_first": float,  # s
        "total_layers": int,
        "total_height": float,  # mm
        # to unify with filament used [mm] rounded to 2 decimal places
        "resin_used_ml": lambda x: round(float(x), 2),
        "printer_model": str,
    }

    THUMBNAIL_NAME_PAT = re.compile(
        r".*?(?P<dim>\d+x\d+)\.(?P<format>qoi|jpg|png)")

    def set_attr(self, name, value):
        """A helper function that saves attributes to `self.data`"""
        if value is None:
            return
        correct_name = SLKeys(name).key
        if correct_name not in self.Attrs:
            return
        conv = self.Attrs[correct_name]
        try:
            self.data[correct_name] = conv(value)
        except ValueError:
            log.warning("Could not convert using %s: %s", conv, value)

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
        data: dict = {}
        file_name = "config.json"
        with zipfile.ZipFile(path, "r") as zip_file:
            if file_name not in zip_file.namelist():
                return data
            data = json.loads(zip_file.read(file_name))
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
                    match = SLMetaData.THUMBNAIL_NAME_PAT.match(info.filename)
                    if match:
                        img_format = match.group("format").upper()
                        img_dim = match.group("dim")
                        data = zip_file.read(info.filename)
                        data = base64.b64encode(data)
                        thumbnails[f"{img_dim}_{img_format}"] = data
        return thumbnails


def get_metadata(path: str, save_cache=True, filename=None):
    """Returns the Metadata for given `path`

    :param path: Gcode file
    :param save_cache: Boolean if cache should be saved
    :param filename: Filename in case of temp file as the path do differs from
    the name of temp file and the meta class is decided based on extension.
    """
    metadata = get_meta_class(path, filename)
    metadata.load(save_cache)
    return metadata


def get_meta_class(path: str, filename: Optional[str] = None):
    """Returns the Metadata class based on given filename or path.

    :param path: Gcode file
    :param filename: Filename in case of temp file as the path do differs from
    the name of temp file and the meta class is decided based on extension.
    """
    if filename:
        fnl = filename.lower()
    else:
        fnl = path.lower()

    meta_class: MetaData
    if fnl.lower().endswith(GCODE_EXTENSIONS):
        meta_class = FDMMetaData(path)
    elif fnl.lower().endswith(SLA_EXTENSIONS):
        meta_class = SLMetaData(path)
    else:
        raise UnknownGcodeFileType(path)
    return meta_class


def get_closest_image(thumbnails: Dict[str, bytes],
                      target: ImageInfo,
                      aspect_ratio_weight: float = 1.0) -> Optional[ImageInfo]:
    """Get the image with the closest resolution
    and aspect ratio to the target
    The weight of aspect ratio to resolution proximity can be tweaked"""
    valid_thumbnails: List[ImageInfo] = []
    for thumbnail in thumbnails.keys():
        info: ImageInfo = ImageInfo.from_thumbnail_info(thumbnail)
        if info.format in IMAGE_FORMATS:
            if info.width >= 50 and info.height >= 50:
                valid_thumbnails.append(info)

    if not valid_thumbnails:
        return None

    sorted_thumbnails: List[ImageInfo] = sorted(
        valid_thumbnails, key=lambda x: x.badness(target, aspect_ratio_weight))

    return sorted_thumbnails[0]


def get_preview(thumbnails: Dict[str, bytes]) -> Optional[ImageInfo]:
    """Get the preview with the biggest resolution from the list of
    thumbnails

    >>> get_preview(
    ... {'8000x20_PNG': b'', '600x400_PNG': b'', '800x600_PNG': b''})
    ImageInfo("800x600_PNG")
    >>> get_preview(
    ... {'600x1_PNG': b'', '320x240_PNG': b'', '800x9000_PNG': b''})
    ImageInfo("320x240_PNG")
    >>> get_preview(
    ... {'500x100_PNG': b'', '50x50_PNG': b'', '900x400_PNG': b''})
    ImageInfo("900x400_PNG")
    >>> get_preview({'500x200_PNG': b''})
    ImageInfo("500x200_PNG")
    """

    return get_closest_image(thumbnails,
                             ImageInfo(640, 480, "PNG"),
                             aspect_ratio_weight=1)


def get_icon(thumbnails: Dict[str, bytes]) -> Optional[ImageInfo]:
    """Get the icon which suits best according given parameters

    >>> get_icon({'8000x20_PNG': b'', '600x400_PNG': b'', '800x600_PNG': b''})
    ImageInfo("600x400_PNG")
    >>> get_icon({'600x1_PNG': b'', '320x240_PNG': b'', '800x9000_PNG': b''})
    ImageInfo("320x240_PNG")
    >>> get_icon({'500x100_PNG': b'', '50x50_PNG': b'', '120x110_PNG': b''})
    ImageInfo("120x110_PNG")
    >>> get_icon({'50x20_PNG': b''}) is None
    True
    """
    return get_closest_image(thumbnails,
                             ImageInfo(100, 100, "PNG"),
                             aspect_ratio_weight=1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")
