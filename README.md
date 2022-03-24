This program is used for obtaining metadata from G-code
files e.g. file name, material type, estimated print time, etc.

First, program will try to obtain any useful metadata from the file name.
Then the G-code file is quick parsed by looking at comment blocks in the
beginning and at the end of a file.
If this parsing keeps failing, file is parsed using line by line method.

Any metadata obtained from the path will be overwritten by metadata from
the file if the metadata is contained there as well.

Desired metadata are specified by Attributes in FDMMetaData class.
