"""Tests for gcode-metadata tool for g-code files."""
import json
import os
import tempfile
import shutil
from importlib.metadata import version

import time
import pytest

from gcode_metadata import (get_metadata, UnknownGcodeFileType, MetaData,
                            get_meta_class)

gcodes_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                          "gcodes")

# pylint: disable=redefined-outer-name


@pytest.fixture
def tmp_dir():
    """Temporary directory creation fixture"""
    # pylint: disable=consider-using-with
    temp = tempfile.TemporaryDirectory()
    yield temp.name
    del temp


def chunk_read_file(meta_class, filepath, chunk_size=None):
    """Util method which reads the file in chunk and call load_from_chunk
    on the blocks of data from given meta_class."""
    chunk_size = chunk_size or 10 * 1024  # 10 KiB test on small chunks
    with open(filepath, 'rb') as file:
        # find out file size
        file_size = file.seek(0, os.SEEK_END)
        file.seek(0)
        while True:
            chunk = file.read(chunk_size)
            if not chunk:
                break
            meta_class.load_from_chunk(chunk, file_size)


def give_cache_version(path, version_to_give):
    """Modifies the cache file and adds a valid version number"""
    with open(path, "r", encoding='utf-8') as cache_file:
        cache = json.load(cache_file)
    my_cache = {"version": version_to_give}
    my_cache.update(cache)
    with open(path, "w", encoding='utf-8') as cache_file:
        json.dump(my_cache, cache_file)


def test_get_metadata_file_does_not_exist():
    """Test get_metadata() with a non-existing file"""
    fname = '/somehwere/in/the/rainbow/my.gcode'
    metadata = get_metadata(fname)
    assert not metadata.data


def test_save_cache_empty_file():
    """Test save-cache() with empty file"""
    fname = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
    fn_cache = os.path.join(gcodes_dir, ".fdn_all_empty.gcode")
    meta = get_metadata(fname)
    meta.save_cache()
    with pytest.raises(FileNotFoundError):
        with open(fn_cache, "r", encoding='utf-8'):
            pass


def test_load_cache_file_does_not_exist(tmp_dir):
    """Test load_cache() with a non-existing cache file"""
    with pytest.raises(ValueError):
        fname = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
        temp_gcode = shutil.copy(fname, tmp_dir)
        MetaData(temp_gcode).load_cache()


def test_load_cache_key_error():
    """test load_cache() with incorrect, or missing key"""
    fname = os.path.join(gcodes_dir, "fdn_filename_empty.gcode")
    with pytest.raises(ValueError):
        MetaData(fname).load_cache()


def test_is_cache_recent_fresher(tmp_dir):
    """is_cache_recent, when cache file is fresher, than original file"""
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, tmp_dir)
    # Create the time difference
    time.sleep(0.01)
    fn_cache = os.path.join(gcodes_dir, ".fdn_filename.gcode.cache")
    cache_path = shutil.copy(fn_cache, tmp_dir)
    assert MetaData(temp_gcode).is_cache_recent()
    os.remove(cache_path)
    os.remove(temp_gcode)


def test_is_cache_recent_older(tmp_dir):
    """is_cache_recent, when cache file is older, than original file"""
    fn_cache = os.path.join(gcodes_dir, ".fdn_filename.gcode.cache")
    cache_path = shutil.copy(fn_cache, tmp_dir)
    # Create the time difference
    time.sleep(0.01)
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, tmp_dir)
    assert MetaData(temp_gcode).is_cache_recent() is False
    os.remove(cache_path)
    os.remove(temp_gcode)


def test_is_cache_correct_version_none():
    """is_cache_correct_version, when cache file has no version"""
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    assert not MetaData(fn_gcode).is_cache_correct_version()


def test_is_cache_correct_version_mismatch(tmp_dir):
    """is_cache_correct_version, when cache file has different version"""
    fn_cache = os.path.join(gcodes_dir, ".fdn_filename.gcode.cache")
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, tmp_dir)
    cache_path = shutil.copy(fn_cache, tmp_dir)
    give_cache_version(cache_path, "0.0.0")
    assert MetaData(temp_gcode).is_cache_correct_version() is False
    os.remove(cache_path)
    os.remove(temp_gcode)


def test_is_cache_correct_version_match(tmp_dir):
    """is_cache_correct_version, when cache file has the same version"""
    fn_cache = os.path.join(gcodes_dir, ".fdn_filename.gcode.cache")
    fn_gcode = os.path.join(gcodes_dir, "fdn_filename.gcode")
    temp_gcode = shutil.copy(fn_gcode, tmp_dir)
    cache_path = shutil.copy(fn_cache, tmp_dir)
    give_cache_version(cache_path, version('py-gcode-metadata'))
    assert MetaData(temp_gcode).is_cache_correct_version() is True
    os.remove(cache_path)
    os.remove(temp_gcode)


def test_get_metadata_invalid_file():
    """Test get_metadata() with a file that has a wrong ending"""
    fname = tempfile.mkstemp()[1]
    with pytest.raises(UnknownGcodeFileType):
        get_metadata(fname)


class TestFDNMetaData:
    """Tests for standard gcode metadata."""

    def test_mmu(self):
        """Test MMU metadata.
        mmu_full_0.4n_0.15mm_PETG,PLA_MK4ISMMU3_1h16m.gcode
        ; filament used [mm] = 3067.34, 1274.58, 0.00, 0.00, 0.00
        ; filament used [cm3] = 1, 2, , , 0.00
        ; filament used [g] = 9.37, 3.80, 0.00, 0.00, 0.00
        ; filament cost = 0.26, 0.10, 0.00, 0.00, 0.00
        ; temperature = 280,-280,280,280,280
        ; nozzle_diameter = 0.4,0.4,0.4,porkchop,0.4
        ; temperature = 280,-280,280,280,280
        ; bed_temperature = 90,60,105,105,105
        ; filament_type = PETG;PLA;ASA;PETG;PETG
        ; extruder_colour = #FF8000;#DB5182;#3EC0FF;#FF4F4F;#FBEB7D
        """
        fname = os.path.join(gcodes_dir, "mmu_attribute_test.gcode")
        meta = get_metadata(fname, False)
        # check mmu data
        assert meta.data['filament used [mm]'] == 6
        assert round(meta.data['filament used [g]'], 2) == 42.69
        assert meta.data['filament used [mm] per tool'] == [
            1.0, 2.0, 3.0, 0.0, 0.0
        ]
        assert meta.data['filament used [g] per tool'] == [
            27.0, 4.0, 5.0, 5.23, 1.46
        ]
        assert meta.data['filament cost per tool'] == [
            3.0, -3.0, 0.0, 0.0, 0.0
        ]
        assert meta.data['bed_temperature per tool'] == [90, 60, 105, 105, 105]
        assert meta.data['temperature per tool'] == [280, -280, 280, 280, 280]
        assert meta.data['extruder_colour per tool'] == [
            '#FF8000', '#DB5182', '#3EC0FF', '#FF4F4F', '#FBEB7D'
        ]
        # This might be wrong, we might want to not allow negative values,
        # but it's fun, so whatever
        assert meta.data['filament cost'] == 0
        # if tool specific value are not the same, the value is not added
        # !!! NOTE but it will still get parsed from the file name, but it
        # might be good for the backward compatibility
        assert 'filament used [cm3]' not in meta.data
        assert 'filament_type' not in meta.data
        assert 'nozzle_diameter' not in meta.data
        assert 'temperature' not in meta.data
        assert 'extruder_colour' not in meta.data

    def test_full(self):
        """Both the file and filename contain metadata. There are thumbnails.
        """
        fname = os.path.join(gcodes_dir,
                             "fdn_full_0.15mm_PETG_MK3S_2h6m.gcode")
        meta = get_metadata(fname, False)
        assert meta.data == {
            'bed_temperature': 90,
            'bed_temperature per tool': [90],
            'brim_width': 0,
            'estimated printing time (normal mode)': '2h 6m 5s',
            'filament cost': 0.41,
            'filament cost per tool': [0.41],
            'filament used [cm3]': 10.65,
            'filament used [cm3] per tool': [10.65],
            'filament used [g]': 13.52,
            'filament used [g] per tool': [13.52],
            'filament used [mm]': 4427.38,
            'filament used [mm] per tool': [4427.38],
            'filament_type': 'PETG',
            'filament_type per tool': ['PETG'],
            'fill_density': '20%',
            'nozzle_diameter': 0.4,
            'nozzle_diameter per tool': [0.4],
            'printer_model': 'MK3S',
            'layer_height': 0.15,
            'support_material': 0,
            'temperature': 250,
            'temperature per tool': [250],
            'ironing': 0,
            'layer_info_present': True,
            'normal_left_present': True,
            'normal_percent_present': True,
            'quiet_left_present': True,
            'quiet_percent_present': True,
        }
        assert len(meta.thumbnails['640x480_PNG']) == 158644

    def test_m73_and_layer_info(self):
        """Tests an updated file with additional suppported info"""
        fname = os.path.join(gcodes_dir,
                             "full_m73_layers_0.2mm_PLA_MK3SMMU2S_11m.gcode")
        meta = get_metadata(fname, False)
        assert meta.data['estimated printing time (normal mode)'] == '10m 32s'
        assert meta.data['filament_type'] == 'PLA'
        assert meta.data['filament_type per tool'] == [
            'PLA', 'PLA', 'PLA', 'PLA', 'PLA'
        ]
        assert meta.data['printer_model'] == 'MK3SMMU2S'
        assert meta.data['layer_height'] == 0.2
        assert meta.data['normal_percent_present'] is True
        assert meta.data['normal_left_present'] is True
        assert meta.data['quiet_percent_present'] is True
        assert meta.data['quiet_left_present'] is True
        assert meta.data['layer_info_present'] is True
        assert meta.data['brim_width'] == 0
        assert meta.data['fill_density'] == '15%'
        assert meta.data['ironing'] == 0
        assert meta.data['support_material'] == 0
        assert len(meta.thumbnails['160x120_PNG']) == 5616

    def test_only_path(self):
        """Only the filename contains metadata. There are no thumbnails."""
        fname = os.path.join(gcodes_dir,
                             "fdn_only_filename_0.25mm_PETG_MK3S_2h9m.gcode")
        meta = get_metadata(fname, False)
        assert meta.data == {
            'estimated printing time (normal mode)': '2h9m',
            'filament_type': 'PETG',
            'printer_model': 'MK3S',
            'layer_height': 0.25,
        }
        assert not meta.thumbnails

    def test_fdn_all_empty(self):
        """Only the file contains metadata. There are thumbnails."""
        fname = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
        meta = get_metadata(fname, False)
        assert not meta.data
        assert not meta.thumbnails
        assert meta.path == fname

    @pytest.mark.parametrize('chunk_size',
                             [10 * 1024, 20 * 1024 * 1024, 2 * 1024 * 1024])
    def test_from_chunks(self, chunk_size):
        """Test chunks from file read ok. Test on several chunk size."""
        fname = os.path.join(gcodes_dir,
                             "fdn_full_0.15mm_PETG_MK3S_2h6m.gcode")
        chunk_meta = get_meta_class(fname)
        chunk_read_file(chunk_meta, fname, chunk_size)
        # check that same result came from parsing metadata as a whole file
        meta = get_metadata(fname, False)
        assert meta.thumbnails == chunk_meta.thumbnails
        assert meta.data == chunk_meta.data

    def test_from_chunks_fake_data(self):
        """Test chunks fake file without metadata with string"""
        data = b'*\n' * 1024 * 1024 * 200  # 400 MB file
        chunk_meta = get_meta_class('fake_file.gcode')
        chunk_size = 10 * 1024  # 10 KiB test on small chunks
        data_size = len(data)
        for i in range(0, data_size, chunk_size):
            chunk_meta.load_from_chunk(data[i:i + chunk_size], data_size)
        assert not chunk_meta.data

    def test_from_chunks_meta_only_path(self):
        """Test chunks from file with no metadata."""
        fname = os.path.join(gcodes_dir,
                             "fdn_only_filename_0.25mm_PETG_MK3S_2h9m.gcode")
        chunk_meta = get_meta_class(fname)
        chunk_read_file(chunk_meta, fname)
        assert chunk_meta.data == {}
        chunk_meta.load_from_path(fname)
        assert chunk_meta.data == {
            'estimated printing time (normal mode)': '2h9m',
            'filament_type': 'PETG',
            'printer_model': 'MK3S',
            'layer_height': 0.25,
        }
        assert not chunk_meta.thumbnails

    def test_from_chunks_empty_file(self):
        """Test chunks from file with no metadata."""
        fname = os.path.join(gcodes_dir, "fdn_all_empty.gcode")
        chunk_meta = get_meta_class(fname)
        chunk_read_file(chunk_meta, fname)
        assert chunk_meta.data == {}

    def test_from_chunks_invalid_file(self):
        """Test reading metadata as chunks invalid file."""
        fname = tempfile.mkstemp()[1]
        with pytest.raises(UnknownGcodeFileType):
            get_meta_class(fname)


class TestSLMetaData:
    """Tests for SL print file metadata."""

    def test_sl(self):
        """Basic test."""
        fname = os.path.join(gcodes_dir, "pentagonal-hexecontahedron-1.sl1")
        meta = get_metadata(fname, False)

        assert meta.data == {
            'printer_model': 'SL1',
            'printTime': 8720,
            'faded_layers': 10,
            'exposure_time': 7.5,
            'initial_exposure_time': 35.0,
            'max_initial_exposure_time': 300.0,
            'max_exposure_time': 120.0,
            'min_initial_exposure_time': 1.0,
            'min_exposure_time': 1.0,
            'layer_height': 0.05,
            'materialName': 'Prusa Orange Tough @0.05',
            'fileCreationTimestamp': '2020-09-17 at 13:53:21 UTC'
        }

        assert len(meta.thumbnails["400x400_PNG"]) == 19688
        assert len(meta.thumbnails["800x480_PNG"]) == 64524

    def test_sl_empty_file(self):
        """Test a file that is empty"""
        fname = os.path.join(gcodes_dir, "empty.sl1")
        meta = get_metadata(fname, False)

        assert not meta.data
        assert not meta.thumbnails

    def test_sl1_chunk_read(self):
        """TODO For now when chunk reading sl file metadata are not loaded"""
        fname = os.path.join(gcodes_dir, "empty.sl1")
        chunk_meta = get_meta_class(fname)
        chunk_read_file(chunk_meta, fname)
        assert chunk_meta.data == {}
