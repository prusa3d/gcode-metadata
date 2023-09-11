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

GCODE_EXTENSIONS = (".gcode", ".gc", ".g", ".gco")
CHARS_TO_REMOVE = ["/", "\\", "\"", "(", ")", "[", "]", "'"]

log = getLogger("connect-printer")

RE_ESTIMATED = re.compile(r"((?P<days>[0-9]+)d\s*)?"
                          r"((?P<hours>[0-9]+)h\s*)?"
                          r"((?P<minutes>[0-9]+)m\s*)?"
                          r"((?P<seconds>[0-9]+)s)?")

PRINTERS = [
    'MK4IS', 'MK4MMU3', 'MK4', 'MK3SMMU3', 'MK3MMU3', 'MK3SMMU2S', 'MK3MMU2',
    'MK3S', 'MK3', 'MK2.5SMMU2S', 'MK2.5MMU2', 'MK2.5S', 'MK2.5', 'MINI',
    'XL5', 'XL4', 'XL3', 'XL2', 'XL', 'iX', 'SL1', 'SHELF', 'EXTRACTOR',
    'HARVESTER'
]

PRINTERS.sort(key=len, reverse=True)

MATERIALS = [
    'PLA', 'PETG', 'ABS', 'ASA', 'FLEX', 'HIPS', 'EDGE', 'NGEN', 'PA', 'PVA',
    'PCTG', 'PP', 'PC', 'TPU', 'PEBA', 'CPE', 'PVB', 'PET'
]


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
        >>> extract_data("+ěščřžýáíé \\/ -.:<>.gcode") #doctest: +ELLIPSIS
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
                 separator: str = ", ",
                 value_type: Type = float,
                 conversion: Callable[[List[Any]], Any] = same_or_nothing):
        self.separator: str = separator
        self.value_type = value_type
        self.conversion: Callable[[List[Any]], Any] = conversion

    def from_string(self, raw_value):
        """Parses the value from string, returns the list value as well as a
        value for a single tool info compatibility"""
        parsed = []
        for value in raw_value.split(self.separator):
            try:
                parsed.append(self.value_type(value))
            except ValueError:
                return None, None
        try:
            single_value = self.conversion(parsed)
        except ValueError:
            single_value = None
        return parsed, single_value


class FDMMetaData(MetaData):
    """Class for extracting Metadata for FDM gcodes"""

    def set_attr(self, name, value):
        """Set an attribute, but add support for mmu list attributes"""
        if name in self.MMUAttrs:
            value_list, single_value = self.MMUAttrs[name].from_string(value)
            mmu_name = get_mmu_name(name)
            if value_list is not None:
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
                                    conversion=same_or_nothing)
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
        "thumbnails_format": str,
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
    elif fnl.lower().endswith(".sl1"):
        meta_class = SLMetaData(path)
    else:
        raise UnknownGcodeFileType(path)
    return meta_class


def biggest_resolution(thumbnails: Dict[str, bytes]):
    """Get the thumbnail with the biggest resolution from the list of
    thumbnails

    >>> biggest_resolution({'8000x200': b'', '600x400': b'', '800x600': b''})
    '800x600'
    >>> biggest_resolution({'600x1': b'', '320x240': b'', '800x9000': b''})
    '320x240'
    >>> biggest_resolution({'500x100': b'', '50x50': b'', '900x400': b''})
    '50x50'
    >>> biggest_resolution({'500x200': b''})
    '500x200'
    """
    max_resolution_key = None
    max_res = 0

    for resolution in thumbnails:
        width, height = map(int, resolution.split('x'))

        # Calculate ratio and consider only values in between 1 and 2
        ratio = width / height
        res = width * height
        if 1 <= ratio <= 2:
            if res > max_res:
                max_res = res
                max_resolution_key = resolution
        else:
            log.info("Thumbnail ratio is not between 1 and 2: %s", ratio)

    if max_resolution_key is None:
        log.info("No thumbnail with ratio between 1 and 2 found. "
                 "Using biggest thumbnail.")
        max_resolution_key = max(thumbnails.keys())

    return max_resolution_key


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="gcode file")
    args = parser.parse_args()

    meta = get_metadata(args.file)
    for k, v in meta.data.items():
        print(f"{k}: {v}")
