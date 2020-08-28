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
import os
from typing import (
    IO,
    DefaultDict,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypeVar,
    Union,
)

import objc
from objc._callable_docstr import describe_type

from .datamodel import (
    ArgInfo,
    AvailabilityInfo,
    FrameworkMetadata,
    FunctionInfo,
    MethodInfo,
    ReturnInfo,
)
from .topsort import topological_sort

T = TypeVar("T")
FILE_TYPE = Union[str, os.PathLike[str]]


L1 = "="
L2 = "-"
L3 = "."
L4 = "~"


def header(fp: IO[str], line: str, *, level: str = L2, indent: str = "") -> None:
    print(indent + line, file=fp)
    print(indent + (level * len(line)), file=fp)
    print("", file=fp)


def format_version(value: int) -> str:
    """
    Return a pretty printed version given an
    integer version.
    """
    r = []
    while value:
        r.append(str(value % 100))
        value //= 100
    return ".".join(r[::-1])


def group_by(
    definitions: Mapping[str, T], key: str
) -> List[Tuple[str, List[Tuple[str, T]]]]:
    """
    Group the items of a directory based on the value
    of one of the attributes of the value. Returns a list
    of groups, where evere group is a tuple of a key and item.
    """
    groups: DefaultDict[str, List[Tuple[str, T]]] = collections.defaultdict(list)
    for name, item in definitions.items():
        # The specified attribute might be optional
        k = getattr(item, key, None) or ""
        groups[k].append((name, item))

    return sorted(groups.items())


def group_list_by(definitions: Sequence[T], key: str) -> List[Tuple[str, List[T]]]:
    """
    Group the values in a list on the value of the given
    attribute of an item. Returns a list of groups.
    """
    groups: DefaultDict[str, List[T]] = collections.defaultdict(list)
    for item in definitions:
        # The specified attribute might be optional
        k = getattr(item, key, None) or ""
        groups[k].append(item)

    return sorted(groups.items())


def available(
    fp: IO[str], availability: Optional[AvailabilityInfo], *, indent: str = ""
) -> None:
    """
    Document availability information
    """
    # This uses custom directives that I haven't implemented yet.
    if availability is None:
        return

    if availability.introduced is not None:
        print(
            f"{indent}.. macos_introduced: {format_version(availability.introduced)}",
            file=fp,
        )
        print(file=fp)
    if availability.deprecated:
        print(
            f"{indent}.. macos_deprecated: {format_version(availability.deprecated)}",
            file=fp,
        )
        if availability.deprecated_message is not None:
            print(f"{indent}   {availability.deprecated_message}", file=fp)
    print("", file=fp)


def describe_returnvalue(info: ReturnInfo) -> str:
    description = []

    if info.null_accepted:
        description.append("Value can be *None*")

    if info.c_array_of_variable_length:
        description.append("sequence with unspecified length")

    elif info.c_array_delimited_by_null:
        description.append("sequence, will be NUL terminated in C")

    return ", ".join(description)


def describe_argument(info: ArgInfo, metadata: Union[MethodInfo, FunctionInfo]) -> str:
    # The argument description needs to be fine-tuned using actual metadata with
    # exception information, the scan output that I'm currently using does not
    # contain the interesting attributes.

    if info.printf_format:
        if info.null_accepted:
            return "%-style format-string or None"
        else:
            return "%-style format-string"

    elif info.callable:
        return "callable"  # To be described

    else:
        description = []

        typestr = info.typestr
        modifier = info.type_modifier
        if not modifier:
            if typestr[0] in (objc._C_IN, objc._C_OUT, objc._C_INOUT):
                modifier = typestr[:1]
                typestr = typestr[1:]

        if typestr == objc._C_ID:
            # The attribute is 3-valued:
            # - True:  It is known that passing NULL for the argument is OK
            # - False: It is known that passing NULL for the argument is not OK
            # - None:  It isn't known if passing NULL is acceptable
            if info.null_accepted:
                description.append("None accepted")
            else:
                description.append("None not accepted")

        arg = info.c_array_length_in_arg
        array = False
        if arg is not None:
            if isinstance(arg, tuple):
                description.append(
                    f"array with length on input in *{metadata.args[arg[0]].name}* and output in *{metadata.args[arg[1]].name}*"  # noqa: B950
                )
            elif info.c_array_length_in_result:
                description.append(
                    f"array with length on input in *{metadata.args[arg].name}* and output in the return value"  # noqa: B950
                )
            else:
                description.append(f"array with length in *{metadata.args[arg].name}*")

            array = True

        elif info.c_array_of_variable_length:
            description.append("sequence with unspecified length")

            array = True

        elif info.c_array_delimited_by_null:
            description.append("sequence, will be NUL terminated in C")

            array = True

        if not array:
            # See above for "null_accepted" semantics.
            if modifier == objc._C_OUT:
                if info.null_accepted:
                    description.append(
                        "Pass by reference output argument. Pass :data:`None` to get a value, of :data:`objc.NULL` to not get a value"  # noqa: B950
                    )
                else:
                    description.append(
                        "Pass by reference output argument. Pass :data:`None`"
                    )

            elif modifier in (objc._C_IN, objc._C_INOUT):
                inout = "input" if modifier == objc._C_IN else "input/out"
                if info.null_accepted:
                    description.append(
                        f"Pass by reference {inout} argument. Pass :data:`objc.NULL` to not pass a value."  # noqa: B950
                    )
                else:
                    description.append(f"Pass by reference {inout} argument.")

        else:
            description.append("Describe array direction")

        return ", ".join(description)


def document_callable(
    fp: IO[str],
    name: str,
    metadata: Union[FunctionInfo, MethodInfo],
    *,
    ismethod: bool,
    indent: str,
    native_name: Optional[str] = None,
) -> None:
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

    for info in metadata.args:
        hdr_name.append(f"{info.name}, ")

    if metadata.variadic:
        hdr_name.append("..., ")

    if metadata.args:
        hdr_name.append("/)")

    print("".join(hdr_name), file=fp)

    if isinstance(metadata, MethodInfo):
        if metadata.class_method:
            print(f"{indent}   :classmethod:", file=fp)
        print("", file=fp)

    if native_name is not None:
        print(f"{indent}   Binding for {native_name}", file=fp)
        print("", file=fp)

    for info in metadata.args:
        if info.type_name:
            type_name = info.type_name
        else:
            type_name = describe_type(info.typestr)

        print(
            f"{indent}   :param {type_name} {info.name}: {describe_argument(info, metadata)}",  # noqa: B950
            file=fp,
        )

    # This code is way to basic and doesn't handle pass-by-reference
    # output and input/output arguments, those are returned from the
    # callable.
    rv = metadata.retval
    if rv.type_name:
        type_name = rv.type_name
    else:
        type_name = describe_type(rv.typestr)

    if rv.typestr != objc._C_VOID:
        description = describe_returnvalue(rv)
        if description:
            print(f"{indent}   :returns: {description}", file=fp)
        print(f"{indent}   :rtype: {type_name}", file=fp)
    print("", file=fp)
    available(fp, metadata.availability, indent=indent + "   ")
    print("", file=fp)


def document_enumerations(fp: IO[str], mergedinfo: FrameworkMetadata):
    """
    Generate documentation for enumeration types and values

    Note that Objective-C enumerations are *not* represented
    by ``enum`` types in Python. The enum type will be exposed
    as a fake type for typing checking.
    """
    # ObjC also has "enum.IntEnum" and "enum.IntFlag", try to
    # collect that information and include this in the documentation.
    if not mergedinfo.enum:
        return

    header(fp, "Enumerations", level=L2)

    print("The following enumerations are defined by this framework.", file=fp)

    for enumeration, values in group_by(mergedinfo.enum, "enum_type"):
        values.sort(key=lambda item: item[1].value)

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

        if enumeration in mergedinfo.enum_type:
            tp = mergedinfo.enum_type[enumeration]
            available(fp, tp.availability, indent="   ")

        # Values are not enum labels
        for valname, valinfo in values:
            print(
                f".. data:: {valname} : { enumeration if enumeration else 'int' }",
                file=fp,
            )
            print("", file=fp)
            available(fp, valinfo.availability, indent="   ")


def document_externs(fp: IO[str], mergedinfo: FrameworkMetadata):
    """
    Generate documentation for extern definitions (and related types)

    Note that the related types are just there for typing checking, the
    constants are not an instance of the related type.
    """
    #
    # A large fraction of these are "string enums" in ObjC, try to collect that
    # information and document them as such (?).
    #
    if not mergedinfo.externs:
        return

    header(fp, "Externs", level=L2)

    print("The following exetern constants are defined by this framework", file=fp)

    for extern_type, values in group_by(mergedinfo.externs, "type_name"):
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
            available(fp, valinfo.availability, indent="   ")


def document_classes(fp: IO[str], mergedinfo: FrameworkMetadata):
    """
    Generate documentation for Objecive-C classes.

    Classes are ordered by a topological sort that ensures that superclasses are
    documented before subclasses, and within classes methods are grouped by
    category and sorted by name within categories.
    """

    if not mergedinfo.classes:
        return

    header(fp, "Classes", level=L2)

    class_names = list(mergedinfo.classes)
    partial_order = []

    extern_categories = []
    for cls, classinfo in mergedinfo.classes.items():
        if classinfo.super is None:
            # Categories on a class not defined in this framework.
            #  NSObject and NSProxy are the root classes
            # Note: This is not entirely correct, the if statement
            # should only trigger for the Foundation bindings, other
            # frameworks can define categories on NSObject.
            if cls not in ("NSObject", "NSProxy"):
                class_names.remove(cls)
            extern_categories.append(cls)
        elif classinfo.super in class_names:
            partial_order.append((classinfo.super, cls))

    class_order = topological_sort(class_names, partial_order)

    for cls in class_order:
        classinfo = mergedinfo.classes[cls]

        header(fp, cls, level=L3)
        print(f".. class:: {cls}", file=fp)
        print("", file=fp)
        if classinfo.super is None:
            print("   Subclass of :class:`objc.objc_object`.", file=fp)
        else:
            # Possible problem: The superclass can be in a different
            # framework, I haven't decided yet how to get the correct
            # reference here.
            print(f"   Subclass of :class:`{classinfo.super}`.", file=fp)
        print("", file=fp)
        available(fp, classinfo.availability, indent="   ")

        for category, values in group_list_by(classinfo.methods, "category"):
            if category:
                header(fp, f"Category {category}", level=L4, indent="   ")

            for method in values:
                # Need to calculate the python name and signature!
                python_name = method.selector.replace(":", "_")
                native_name = ("+" if method.class_method else "-") + method.selector

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


def generate_documentation(
    output_fn: FILE_TYPE,
    module_name: FILE_TYPE,
    exceptions_fn: FILE_TYPE,
    headerinfo_fns: Sequence[FILE_TYPE],
):
    print(f"Generate documentation {output_fn!r}")

    # exceptions = load_framework_info(exceptions_fn)
    headerinfo = [FrameworkMetadata.from_file(fn) for fn in headerinfo_fns]

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
