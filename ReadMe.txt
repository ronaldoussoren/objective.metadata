PyObjC metadata generator using objective.cparser

* Classes for parsing headers, various SDKs and architecture flags

  Store results in generic data structure

* Compile small test programs to enhance parsed data 
  - values for enums
  - real type encodings
    (but: try to calculate them in Python code, to deal with
     our enhanced encodings)

* Merge parsed data for various platforms

* Merge existing ".brigesupport" files.

* GUI that shows header files and parsed data, goals

  1) Verify that everything gets parsed
  2) Add custom metadata (in/out annotations, ...)

* Compile into new format
