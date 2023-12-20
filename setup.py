"""Setup of PrusaConnect SDK for Printer."""
import re

from setuptools import setup  # type: ignore


def doc():
    """Return README.md content."""
    with open('README.md', 'r', encoding="utf-8") as readme:
        return readme.read().strip()


METADATA = {}
with open("gcode_metadata/__init__.py", "r", encoding="utf-8") as info:
    METADATA = dict(re.findall(r'__([a-z_]+)__ = "([^"]+)"', info.read()))


setup(
    name='py-gcode-metadata',
    version=METADATA["version"],
    packages=['gcode_metadata'],
    url='https://github.com/prusa3d/gcode-metadata',
    license='LGPLv2+',
    author='Prusa Connect Developers',
    author_email='link@prusa3d.com',
    maintainer='Prusa Connect Developers',
    maintainer_email='link@prusa3d.com',
    description='Python library for extraction of metadata from g-code files',
    project_urls={
        "Bug Tracker": "https://github.com/prusa3d/gcode-metadata/issues",
        "Source Code": "https://github.com/prusa3d/gcode-metadata",
    },
    package_data={'gcode_metadata': ['py.typed']},
    data_files=[("share/doc/gcode-metadata",
                 ["README.md", "ChangeLog", "LICENSE"])],
    long_description=doc(),
    long_description_content_type="text/markdown",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "Natural Language :: English",
        "License :: OSI Approved :: "
        "GNU Lesser General Public License v2 or later (LGPLv2+)",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
