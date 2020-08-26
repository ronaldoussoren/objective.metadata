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

Missing are:
    - Functions
      Should be fairly trivial given the bindings for
      classes

    - Protocols (formal and informal)

    - Expressions
    - Literals
      These are similar to "extern" and "enum", it might
      be better to merge those in one section.

    - Aliases
      Also similar to "extern" and "enum", but I'd prefer
      to document what the name is an alias for.

      Metadata parser needs to be updated with availability
      information (which changes the data format).
"""

import collections

import objc
from objc._callable_docstr import describe_type

from .storage import load_framework_info
from .topsort import topological_sort

L1 = "="
L2 = "-"
L3 = "."
L4 = "~"


def header(fp, line, *, level=L2, indent=""):
    print(indent + line, file=fp)
    print(indent + (level * len(line)), file=fp)
    print("", file=fp)


def format_version(value):
    r = []
    while value:
        r.append(str(value % 100))
        value //= 100
    return ".".join(r[::-1])


def group_by(definitions: dict, key: str):
    groups = collections.defaultdict(list)
    for name, item in definitions.items():
        k = item.get(key, "")
        groups[k].append((name, item))

    return sorted(groups.items())


def group_list_by(definitions: list, key: str):
    groups = collections.defaultdict(list)
    for item in definitions:
        k = item.get(key, "")
        groups[k].append(item)

    return sorted(groups.items())


def available(fp, availability, *, indent=""):
    """
    Document availability information
    """
    # This uses custom directives that I haven't implemented yet.
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


def describe_argument(info, metadata):
    # The argument description needs to be fine-tuned using actual metadata with
    # exception information, the scan output that I'm currently using does not
    # contain the interesting attributes.
    if info.get("printf_format"):
        if info.get("nullable"):
            return "%-style format-string or None"
        else:
            return "%-style format-string"

    elif info.get("callable"):
        return "callable"  # To be described

    else:
        description = []

        typestr = info["typestr"]
        modifier = info.get("type_modifier")
        if not modifier:
            if typestr[0] in (objc._C_IN, objc._C_OUT, objc._C_INOUT):
                modifier = typestr[0]
                typestr = typestr[1:]

        if typestr == objc._C_ID:
            # The attribute is 3-valued:
            # - True:  It is known that passing NULL for the argument is OK
            # - False: It is known that passing NULL for the argument is not OK
            # - None:  It isn't known if passing NULL is acceptable
            if info.get("null_accepted"):
                description.append("None accepted")
            else:
                description.append("None not accepted")

        arg = info.get("c_array_length_in_arg")
        array = False
        if arg is not None:
            if isinstance(arg, tuple):
                description.append(
                    f"array with length on input in *{metadata['args'][arg[0]]}* and output in *{metadata['args'][arg[0]]}*"  # noqa: B950
                )
            elif info.get("c_array_length_in_result"):
                description.append(
                    f"array with length on input in *{metadata['args'][arg[0]]}* and output in the return value"  # noqa: B950
                )
            else:
                description.append(f"array with length in *{metadata['args'][arg[0]]}*")

            array = True

        elif info.get("c_array_of_variable_length"):
            description.append("sequence with unspecified length")

            array = True

        elif info.get("c_array_delimited_by_null"):
            description.append("sequence, will be NUL terminated in C")

            array = True

        if not array:
            # See above for "null_accepted" semantics.
            if modifier == objc._C_OUT:
                if arg.get("null_accepted"):
                    description.append(
                        "Pass by reference output argument. Pass :data:`None` to get a value, of :data:`objc.NULL` to not get a value"  # noqa: B950
                    )
                else:
                    description.append(
                        "Pass by reference output argument. Pass :data:`None`"
                    )

            elif modifier in (objc._C_IN, objc._C_INOUT):
                inout = "input" if modifier == objc._C_IN else "input/out"
                if arg.get("null_accepted"):
                    description.append(
                        f"Pass by reference {inout} argument. Pass :data:`objc.NULL` to not pass a value."  # noqa: B950
                    )
                else:
                    description.append(f"Pass by reference {inout} argument.")

        else:
            description.append("Describe array direction")

        return ", ".join(description)


def document_callable(fp, name, metadata, *, ismethod, indent, native_name=None):
    """
    Generate documentation for a callable (function or method).
    """
    #
    # This should be kept in sync with objc._callable_docstr, which
    # does something similar with more limited information.
    #
    # Note that the collected metadata does not include the implicit
    # arguments for selectors.
    #
    hdr_name = []

    kind = "method" if ismethod else "function"

    hdr_name.append(f"{indent}.. {kind}:: {name}(")

    for info in metadata["args"]:
        hdr_name.append(f"{info['name']}, ")

    if metadata.get("variadic"):
        hdr_name.append("..., ")
    hdr_name.append("/)")

    print("".join(hdr_name), file=fp)
    if metadata.get("class_method"):
        print(f"{indent}   :classmethod:", file=fp)
    print("", file=fp)

    if native_name is not None:
        print(f"{indent}   Binding for {native_name}", file=fp)
        print("", file=fp)

    for info in metadata["args"]:
        if info.get("type_name"):
            type_name = info["type_name"]
        else:
            type_name = describe_type(info["typestr"])

        print(
            f"{indent}   :param {type_name} {info['name']}: {describe_argument(info, metadata)}",  # noqa: B950
            file=fp,
        )

    # This code is way to basic and doesn't handle pass-by-reference
    # output and input/output arguments, those are returned from the
    # callable.
    info = metadata["retval"]
    if info.get("type_name"):
        type_name = info["type_name"]
    else:
        type_name = describe_type(info["typestr"])

    if info["typestr"] != objc._C_VOID:
        description = describe_argument(info, metadata)
        if description:
            print(f"{indent}   :returns: {description}", file=fp)
        print(f"{indent}   :rtype: {type_name}", file=fp)
    print("", file=fp)
    available(fp, metadata.get("availability"), indent=indent + "   ")
    print("", file=fp)


def document_enumerations(fp, mergedinfo):
    """
    Generate documentation for enumeration types and values

    Note that Objective-C enumerations are *not* represented
    by ``enum`` types in Python. The enum type will be exposed
    as a fake type for typing checking.
    """
    # ObjC also has "enum.IntEnum" and "enum.IntFlag", try to
    # collect that information and include this in the documentation.
    enum_definitions = mergedinfo["definitions"]["enum"]
    enum_types = mergedinfo["definitions"]["enum_type"]
    if enum_definitions:
        header(fp, "Enumerations", level=L2)

        print("The following enumerations are defined by this framework.", file=fp)

        for enumeration, values in group_by(enum_definitions, "enum_type"):
            values.sort(key=lambda item: item[1]["value"])

            if enumeration:
                # Note sure why this happens, but scanner finds some enumerations
                # without an "enum_type".
                header(fp, enumeration, level=L3)

                print(f".. class:: {enumeration}", file=fp)
                print("", file=fp)
                # Mention if this is IntEnum or an IntFlag, also check if
                # some enums are open-ended.
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
    #
    # A large fraction of these are "string enums" in ObjC, try to collect that
    # information and document them as such (?).
    #
    extern_definitions = mergedinfo["definitions"]["externs"]

    if extern_definitions:
        header(fp, "Externs", level=L2)

        print("The following exetern constants are defined by this framework", file=fp)

        for extern_type, values in group_by(extern_definitions, "type_name"):
            values.sort(key=lambda item: item[0])

            header(fp, extern_type, level=L3)

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


def document_classes(fp, mergedinfo):
    """
    Generate documentation for Objecive-C classes.

    Classes are ordered by a topological sort that ensures that superclasses are
    documented before subclasses, and within classes methods are grouped by
    category and sorted by name within categories.
    """
    #
    classinfo = mergedinfo["definitions"]["classes"]
    if classinfo:
        header(fp, "Classes", level=L2)

        class_names = list(classinfo)
        partial_order = []

        extern_categories = []
        for cls in classinfo:
            if classinfo[cls]["super"] is None:
                # Categories on a class not defined in this framework.
                #  NSObject and NSProxy are the root classes
                # Note: This is not entirely correct, the if statement
                # should only trigger for the Foundation bindings, other
                # frameworks can define categories on NSObject.
                if cls not in ("NSObject", "NSProxy"):
                    class_names.remove(cls)
                extern_categories.append(cls)
            elif classinfo[cls]["super"] in class_names:
                partial_order.append((classinfo[cls]["super"], cls))

        class_order = topological_sort(class_names, partial_order)

        for cls in class_order:
            info = classinfo[cls]

            header(fp, cls, level=L3)
            print(f".. class:: {cls}", file=fp)
            print("", file=fp)
            if info["super"] is None:
                print("   Subclass of :class:`objc.objc_object`.", file=fp)
            else:
                # Possible problem: The superclass can be in a different
                # framework, I haven't decided yet how to get the correct
                # reference here.
                print(f"   Subclass of :class:`{info['super']}`.", file=fp)
            print("", file=fp)
            available(fp, info.get("availability"), indent="   ")

            for category, values in group_list_by(info["methods"], "category"):
                if category:
                    header(fp, f"Category {category}", level=L4, indent="   ")

                for method in values:
                    # Need to calculate the python name and signature!
                    python_name = method["selector"].replace(":", "_")
                    native_name = ("+" if method["class_method"] else "-") + method[
                        "selector"
                    ]

                    document_callable(
                        fp,
                        python_name,
                        method,
                        ismethod=True,
                        indent="   ",
                        native_name=native_name,
                    )

            print("", file=fp)

        if extern_categories:
            header(fp, "Categories on classes from other frameworks", level=L2)

            # Implementation is missing!  I'm waiting until the documentation
            # for regular classes is done.

            for cls in extern_categories:
                print(cls, file=fp)


def generate_documentation(output_fn, module_name, exceptions_fn, headerinfo_fns):
    print(f"Generate documentation {output_fn!r}")

    # exceptions = load_framework_info(exceptions_fn)
    headerinfo = [load_framework_info(fn) for fn in headerinfo_fns]

    # TODO: Introduce function to merge headerinfo and exceptions, used here
    #       and by the compiler
    mergedinfo = headerinfo[0]

    with open(output_fn, "w") as fp:
        header(fp, f":mod:`{module_name}` -- Bindings for {module_name}", level=L1)

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

        document_classes(fp, mergedinfo)
        document_enumerations(fp, mergedinfo)
        document_externs(fp, mergedinfo)
