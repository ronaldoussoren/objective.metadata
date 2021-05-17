"""
Tool for generating type stub (".pyi") for
a framework.
"""

import os
from typing import IO, Sequence, Set, TypeVar, Union

from .datamodel import FrameworkMetadata

# from .merging import merge_framework_metadata
# from .topsort import topological_sort

# import objc
# from objc._callable_docstr import describe_type  # type: ignore


T = TypeVar("T")
FILE_TYPE = Union[str, os.PathLike[str]]

HEADER = """\
'''
Typestubs for framework {framework}
'''
from typing import NewType

"""

FOOTER = """\
__all__ = {allnames!r}
"""


def emit_enums(fp: IO[str], allnames: Set[str], mergedinfo: FrameworkMetadata) -> None:
    """
    Emit type stubs for collected "enum" information
    """
    # This generates a NewType for every C enum because that's what
    # the bridge effectively does.  Will be changed if I find an
    # efficient way to generate "real" enums at runtime.

    for type_name, type_info in sorted(mergedinfo.enum_type.items()):
        if not type_name:
            continue  # XXX: Why is there an empty type name?
        if type_info.ignore:
            continue
        print(f"{type_name} = NewType('{type_name}', int)", file=fp)
        allnames.add(type_name)

    for enum_name, enum_info in sorted(mergedinfo.enum.items()):
        if enum_info.ignore:
            continue

        if enum_info.enum_type:
            print(f"{enum_name}: {enum_info.enum_type}", file=fp)
        else:
            print(f"{enum_name}: int", file=fp)
        allnames.add(enum_name)


def generate_typestubs(
    output_fn: FILE_TYPE,
    module_name: str,
    exceptions_fn: FILE_TYPE,
    headerinfo_fns: Sequence[FILE_TYPE],
) -> None:
    # XXX:
    #   - Maybe need more information
    print(f"Generate typestubs {output_fn!r}")

    # exceptions = ExceptionData.from_file(exceptions_fn)
    headerinfo = [FrameworkMetadata.from_file(fn) for fn in headerinfo_fns]

    # TODO: Introduce function to merge headerinfo and exceptions, used here
    #       and by the compiler
    # mergedinfo = merge_framework_metadata(exceptions, headerinfo)
    mergedinfo = headerinfo[0]

    # Record all names in the type stub
    allnames: Set[str] = set()

    with open(output_fn, "w") as fp:
        print(HEADER.format(framework=module_name), file=fp)

        # XXX: generate import statements for parent frameworks
        #      (e.g. "from Foundation import *")

        emit_enums(fp, allnames, mergedinfo)

        print(FOOTER.format(allnames=tuple(sorted(allnames))), file=fp)
