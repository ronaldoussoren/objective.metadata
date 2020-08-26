"""
Tool for creating a sphinx module documentation file
from metadata

This is rough draft, with some missing features:
    - This reads a single raw fwinfo file
      (no merging exception info)
    - Format is suboptimal
    - No mechanism for convenience methods
    - No mechanism for umbrella framweorks (Quartz)
    - No references to upstream documentation
"""

import collections

from .storage import load_framework_info


def header(fp, line, level="-"):
    print(line, file=fp)
    print(level * len(line), file=fp)
    print("", file=fp)


def format_version(value):
    r = []
    while value:
        r.append(str(value % 100))
        value //= 100
    return ".".join(r[::-1])


def available(fp, availability, indent=""):
    if not availability:
        return

    if "introduced" in availability:
        print(
            f"{indent}.. macos_introduced: {format_version(availability['introduced'])}",
            file=fp,
        )
        print(file=fp)
    if "deprecated" in availability:
        print(
            f"{indent}.. macos_deprecated: {format_version(availability['deprecated'])}",
            file=fp,
        )
        if "deprecated_message" in availability:
            print(f"{indent}   {availability['deprecated_message']}", file=fp)
    print("", file=fp)


def group_by(definitions, key):
    groups = collections.defaultdict(list)
    for name, item in definitions.items():
        k = item.get(key, "")
        groups[k].append((name, item))

    return sorted(groups.items())


def document_enumerations(fp, mergedinfo):
    """
    Generate documentation for enumeration types and values

    Note that Objective-C enumerations are *not* represented
    by ``enum`` types in Python. The enum type will be exposed
    as a fake type for typing checking.
    """
    enum_definitions = mergedinfo["definitions"]["enum"]
    enum_types = mergedinfo["definitions"]["enum_type"]
    if enum_definitions:
        header(fp, "Enumerations")

        print("The following enumerations are defined by this framework.", file=fp)

        for enumeration, values in group_by(enum_definitions, "enum_type"):
            values.sort(key=lambda item: item[1]["value"])

            if enumeration:
                # Note sure why this happens, but scanner finds some enumerations
                # without an "enum_type".
                header(fp, enumeration, level=".")

                print(f".. class:: {enumeration}", file=fp)
                print("", file=fp)
                print("   Placeholder type for use with typechecking.", file=fp)
                print("", file=fp)

            if enumeration in enum_types:
                tp = enum_types[enumeration]
                available(fp, tp.get("availability"), indent="   ")

            # Values are not enum labels
            for valname, valinfo in values:
                print(f".. data:: {valname} : int", file=fp)
                print("", file=fp)
                available(fp, valinfo.get("availability"), indent="   ")


def document_externs(fp, mergedinfo):
    """
    Generate documentation for extern definitions (and related types)

    Note that the related types are just there for typing checking, the
    constants are not an instance of the related type.
    """
    extern_definitions = mergedinfo["definitions"]["externs"]

    if extern_definitions:
        header(fp, "Externs")

        print("The following exetern constants are defined by this framework", file=fp)

        for extern_type, values in group_by(extern_definitions, "type_name"):
            values.sort(key=lambda item: item[0])

            header(fp, extern_type, level=".")

            if extern_type:
                print(f".. class:: {extern_type}", file=fp)
                print("", file=fp)
                print("   Placeholder type for use with typechecking.", file=fp)
                print("", file=fp)

                # Need to record extern_types seperately (just like with enums to
                # store availability info)

            for valname, valinfo in values:
                print(f".. data:: {valname} : {extern_type}", file=fp)
                print("", file=fp)
                available(fp, valinfo.get("availability"), indent="   ")


def generate_documentation(output_fn, module_name, exceptions_fn, headerinfo_fns):
    print(f"Generate documentation {output_fn!r}")

    # exceptions = load_framework_info(exceptions_fn)
    headerinfo = [load_framework_info(fn) for fn in headerinfo_fns]

    # TODO: Introduce function to merge headerinfo and exceptions, used here
    #       and by the compiler
    mergedinfo = headerinfo[0]

    with open(output_fn, "w") as fp:
        header(fp, f":mod:`{module_name}` -- Bindings for {module_name}", level="=")

        print(f".. module:: {module_name}", file=fp)
        print("  :platform: macOS", file=fp)
        print(
            f"  :synopsis: Bindings for the {module_name} framework on macOS", file=fp
        )
        print("", file=fp)

        # Insert introduction here
        # - Library availability
        # - Link to other documentation
        # - ...
        #
        # Current plan: Have a way to include a ReST fragment here

        document_enumerations(fp, mergedinfo)
        document_externs(fp, mergedinfo)
