Format of data files
====================

"Raw" and "exception files
--------------------------

The files containing the results of header file scans and the
files containing manual annotations (the exception files)
are JSON files.

The exact format will be specified later.


Compiled metadata files
-----------------------

The compiled metadata file is a python source file, which
makes it easier to use the resulting file with tools like
py2app (which doesn't understand egg metadata at this time).

The fileformat is not specified at this time and should
be considered instable. That is, the format will change as
needed to get better performance in PyObjC and without
regard to backward compatibility.
