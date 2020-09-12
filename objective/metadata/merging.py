import collections
from dataclasses import replace
from typing import (
    Callable,
    Dict,
    Iterator,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    Union,
)

from . import datamodel


class Ignorable(Protocol):
    @property
    def ignore(self) -> Optional[bool]:
        ...


T = TypeVar("T", bound=Ignorable)
V = TypeVar("V")


def groupby(items: Iterator[Tuple[str, V]]) -> Dict[V, Set[str]]:
    result: Dict[V, Set[str]] = collections.defaultdict(set)
    for key, value in items:
        result[value].add(key)

    return result


def merge_basic_field(
    exceptions: Dict[str, T],
    infos: Sequence[Tuple[str, Dict[str, V]]],
    get_exception_value: Callable[[T], Optional[V]],
) -> Mapping[str, Union[V, datamodel.MergedInfo[V]]]:
    """
    Generic function for merging basic infos
    """
    result: Mapping[str, Union[V, datamodel.MergedInfo[V]]] = {}

    for _arch, info in infos:
        for key in info:
            if key in result:
                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                if (exc_value := get_exception_value(exceptions[key])) is not None:
                    result[key] = exc_value

            grouped = groupby((a, i[key]) for (a, i) in infos if key in i)
            if len(grouped) == 1:
                result[key] = next(iter(grouped))

            elif len(grouped) != 2:
                raise RuntimeError(f"{key} {grouped}")

            else:
                (first_key, first_value), (second_key, second_value) = grouped.items()
                assert len(first_value) == len(second_value) == 1
                assert first_value != second_value

                if first_value == set("x86_64"):
                    merged = datamodel.MergedInfo(x86_64=first_key, arm64=second_key)
                else:
                    merged = datamodel.MergedInfo(x86_64=second_key, arm64=first_key)

                result[key] = merged
    return result


def merge_enum_type(
    exceptions: Dict[str, datamodel.EnumTypeInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.EnumTypeInfo]]],
) -> Dict[str, datamodel.EnumTypeInfo]:
    """
    Merge information about enum types

    This just uses the latest information found for every type, with overrides from
    exceptions.
    """

    result: Dict[str, datamodel.EnumTypeInfo] = {}

    for _arch, info in reversed(infos):
        for key, value in info.items():
            if key in result:
                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                value = replace(
                    value,
                    **{
                        k: v
                        for k, v in exceptions[key].to_dict().items()
                        if v is not None
                    },
                )

            result[key] = value

    return result


def merge_enum(
    exceptions: Dict[str, datamodel.EnumInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.EnumInfo]]],
) -> Dict[str, datamodel.EnumInfo]:
    """
    Merge enum information.

    This assumes that the 'value' attribute is the only interesting variation
    between scans and that the other attributes have their best value
    in the most recent scan.
    """

    def get_value(enum: datamodel.EnumInfo) -> Optional[int]:
        assert not isinstance(enum.value, datamodel.MergedInfo)
        return enum.value

    merged_values = merge_basic_field(
        exceptions,
        [(arch, {k: v.value for k, v in items.items()}) for arch, items in infos],
        get_value,
    )

    all_values = collections.ChainMap(*[info for _arch, info in reversed(infos)])

    result: Dict[str, datamodel.EnumInfo] = {}

    for key, value in merged_values.items():
        info_value = all_values[key]
        info_value = replace(info_value, value=value)

        if key in exceptions:
            if exceptions[key].ignore:
                continue

            info_value = replace(
                info_value,
                **{k: v for k, v in exceptions[key].to_dict().items() if v is not None},
            )

        result[key] = info_value

    return result


def merge_externs(
    exceptions: Dict[str, datamodel.ExternInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.ExternInfo]]],
) -> Dict[str, datamodel.ExternInfo]:
    """
    Merge enum information.

    This assumes that the 'value' attribute is the only interesting variation
    between scans and that the other attributes have their best value
    in the most recent scan.
    """

    def get_typestr(extern: datamodel.ExternInfo) -> Optional[bytes]:
        assert not isinstance(extern.typestr, datamodel.MergedInfo)
        return extern.typestr

    merged_values = merge_basic_field(
        exceptions,
        [(arch, {k: v.typestr for k, v in items.items()}) for arch, items in infos],
        get_typestr,
    )
    all_values = collections.ChainMap(*[info for _arch, info in reversed(infos)])

    result: Dict[str, datamodel.ExternInfo] = {}

    for key, value in merged_values.items():
        info_value = all_values[key]
        info_value = replace(info_value, typestr=value)

        if key in exceptions:
            if exceptions[key].ignore:
                continue

            info_value = replace(
                info_value,
                **{k: v for k, v in exceptions[key].to_dict().items() if v is not None},
            )
        result[key] = info_value

    return result


def merge_literals(
    exceptions: Dict[str, datamodel.LiteralInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.LiteralInfo]]],
) -> Dict[str, datamodel.LiteralInfo]:
    """
    Merge literal information.

    This assumes that the 'value' attribute is the only interesting variation
    between scans and that the other attributes have their best value
    in the most recent scan.
    """

    def get_value(literal: datamodel.LiteralInfo) -> Union[None, int, float, str]:
        assert not isinstance(literal.value, datamodel.MergedInfo)
        return literal.value

    merged_values = merge_basic_field(
        exceptions,
        [(arch, {k: v.value for k, v in items.items()}) for arch, items in infos],
        get_value,
    )
    all_values = collections.ChainMap(*[info for _arch, info in reversed(infos)])

    result: Dict[str, datamodel.LiteralInfo] = {}

    for key, value in merged_values.items():
        info_value = all_values[key]
        info_value = replace(info_value, value=value)

        if key in exceptions:
            if exceptions[key].ignore:
                continue

            info_value = replace(
                info_value,
                **{k: v for k, v in exceptions[key].to_dict().items() if v is not None},
            )

        result[key] = info_value

    return result


def merge_aliases(
    exceptions: Dict[str, datamodel.AliasInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.AliasInfo]]],
) -> Dict[str, datamodel.AliasInfo]:
    """
    Merge information about aliases types

    This just uses the latest information found for every alias, with overrides from
    exceptions.  Primary reason for this is that aliases tend to be used for renamed
    constants.
    """

    result: Dict[str, datamodel.AliasInfo] = {}

    for _arch, info in reversed(infos):
        for key, value in info.items():
            if key in result:
                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                value = replace(
                    value,
                    **{
                        k: v
                        for k, v in exceptions[key].to_dict().items()
                        if v is not None
                    },
                )

            result[key] = value

    return result


def merge_expressions(
    exceptions: Dict[str, datamodel.ExpressionInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.ExpressionInfo]]],
) -> Dict[str, datamodel.ExpressionInfo]:
    """
    Merge information about aliases types

    This just uses the latest information found for every alias, with overrides from
    exceptions.  Primary reason for this is that aliases tend to be used for renamed
    constants.
    """

    result: Dict[str, datamodel.ExpressionInfo] = {}

    for _arch, info in reversed(infos):
        for key, value in info.items():
            if key in result:

                if key not in exceptions or exceptions[key].expression is None:
                    # The expection is that expressions are the same in the various
                    # scans, check this.
                    assert result[key].expression == value.expression

                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                value = replace(
                    value,
                    **{
                        k: v
                        for k, v in exceptions[key].to_dict().items()
                        if v is not None
                    },
                )

            result[key] = value

    return result


def merge_func_macros(
    exceptions: Dict[str, datamodel.FunctionMacroExceptionInfo],
    infos: Sequence[Tuple[str, Dict[str, datamodel.FunctionMacroInfo]]],
) -> Dict[str, datamodel.FunctionMacroInfo]:
    """
    Merge information about function macros

    This just uses the latest information found for every alias, with overrides from
    exceptions.
    """

    result: Dict[str, datamodel.FunctionMacroInfo] = {}

    for _arch, info in reversed(infos):
        for key, value in info.items():
            if key in result:

                if key not in exceptions or exceptions[key].definition is None:
                    # The expection is that expressions are the same in the various
                    # scans, check this.
                    assert result[key].definition == value.definition

                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                value = replace(value, **exceptions[key].exception_info())

            result[key] = value

    return result


def merge_framework_metadata(
    exception_info: datamodel.ExceptionData,
    framework_infos: Sequence[datamodel.FrameworkMetadata],
) -> datamodel.FrameworkMetadata:
    """
    Return the merger between the *frameworks* infos and *exception_info*.

    This will raise an exception when the information cannot be merged.
    """
    result = datamodel.FrameworkMetadata()

    for info in framework_infos:
        result.architectures.update(info.architectures)

    # enum_type
    result = replace(
        result,
        enum_type=merge_enum_type(
            exception_info.enum_type,
            [
                (next(iter(info.architectures)), info.enum_type)
                for info in framework_infos
            ],
        ),
    )

    # enum
    result = replace(
        result,
        enum=merge_enum(
            exception_info.enum,
            [(next(iter(info.architectures)), info.enum) for info in framework_infos],
        ),
    )

    # structs
    ...

    # externs
    result = replace(
        result,
        externs=merge_externs(
            exception_info.externs,
            [
                (next(iter(info.architectures)), info.externs)
                for info in framework_infos
            ],
        ),
    )

    # cftypes
    ...

    # literals
    result = replace(
        result,
        literals=merge_literals(
            exception_info.literals,
            [
                (next(iter(info.architectures)), info.literals)
                for info in framework_infos
            ],
        ),
    )

    # formal_protocols
    ...

    # informal_protocols
    ...

    # classes
    ...

    # aliases
    result = replace(
        result,
        aliases=merge_aliases(
            exception_info.aliases,
            [
                (next(iter(info.architectures)), info.aliases)
                for info in framework_infos
            ],
        ),
    )

    # expressions
    result = replace(
        result,
        aliases=merge_expressions(
            exception_info.expressions,
            [
                (next(iter(info.architectures)), info.expressions)
                for info in framework_infos
            ],
        ),
    )

    # func_macros
    result = replace(
        result,
        aliases=merge_func_macros(
            exception_info.func_macros,
            [
                (next(iter(info.architectures)), info.func_macros)
                for info in framework_infos
            ],
        ),
    )

    # functions
    ...

    return result


"""
    structs: Dict[str, StructInfo] = field(default_factory=dict)

    cftypes: Dict[str, CFTypeInfo] = field(default_factory=dict)

    formal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    informal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    classes: Dict[str, ClassInfo] = field(default_factory=dict)

    functions: Dict[str, FunctionInfo] = field(default_factory=dict)
"""
