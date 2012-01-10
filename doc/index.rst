Welcome to objective.metadata's documentation
=============================================

Introduction
============

Objective.metadata is a project for generating metadata files
for PyObjC 2.4 or later. These metadata files contain information
that the PyObjC bridge cannot extract from the Objective-C
runtime, such as the names and types of global variables.

This project defines a tool, ``objective-metadata-tool`` that
can extract information from Cocoa header files, merge the
information from a number of SDKs and manual annotations and then
write a file that is usable by PyObjC.


More information
================

.. toctree::
   :maxdepth: 2

   usage
   fileformats

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`

