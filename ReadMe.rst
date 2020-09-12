PyObjC metadata generator

This project is fairly rough and mostly targetted at
updating the metadata included with PyObjC

NOTE: The repository is unstable at this point, I'm
currently working on code cleanup and additional features
in a fairly complex code base without tests :-(

Open issues:
- I'm working on the merging code
- "Compiler" needs to be converted to the new data model
- "docgen" is incomplete
- add "mypy" command to generate .pyi stub files
- add proper unittests
- "clang" bindings only have partial typing support
  + Remove unnecessary bindings
  + Add type annotations
- Test tooling with non-system libraries
