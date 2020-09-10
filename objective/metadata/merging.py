import collections
from dataclasses import replace
from typing import (
    Dict,
    Iterable,
    Iterator,
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


def groupby(items: Iterator[Tuple[str, T]]) -> Dict[T, Set[str]]:
    result: Dict[T, Set[str]] = collections.defaultdict(set)
    for key, value in items:
        result[value].add(key)

    return result


def merge_basic_field(
    exceptions: Dict[str, T], infos: Iterable[Tuple[str, Dict[str, V]]], field_name: str
) -> Dict[str, Union[V, datamodel.MergedInfo[V]]]:
    """
    Generic function for merging basic infos
    """
    result: Dict[str, V] = {}

    for idx, (_arch, info) in enumerate(infos):
        seen = collections.ChainMap(infos[:idx])

        for key in info:
            if key in seen:
                continue

            if key in exceptions:
                if exceptions[key].ignore:
                    continue

                if exc_value := getattr(exceptions[key], field_name) is not None:
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


def merge_enum(
    exceptions: Dict[str, T], infos: Sequence[Tuple[str, Dict[str, T]]]
) -> Dict[str, T]:
    """
    Merge enum information.

    This assumes that the 'value' attribute is the only interesting variation
    between scans and that the other attributes have their best value
    in the most recent scan.
    """
    merged_values = merge_basic_field(
        exceptions.enum,
        [(arch, {k: v.value for k, v in items.items()}) for arch, items in infos],
        "value",
    )

    result: Dict[str, T] = {}

    for key, value in infos[-1][1].items():
        if key in exceptions.enum:
            if exceptions.enum[key].ignore:
                continue

            value = replace(
                value,
                **{
                    k: v
                    for k, v in exceptions.enum[key].to_dict().items()
                    if v is not None
                },
            )
            value = replace(value, value=merged_values[key])

        result[key] = value

    return result


def merge_framework_metadata(
    exception_info: Optional[datamodel.FrameworkMetadata],
    framework_infos: Sequence[datamodel.FrameworkMetadata],
) -> datamodel.FrameworkMetadata:
    """
    Return the merger between the *frameworks* infos and *exception_info*.

    This will raise an exception when the information cannot be merged.
    """
    result = datamodel.FrameworkMetadata()

    for info in framework_infos:
        result.architectures.update(info.architectures)

    result = replace(
        result,
        enum=merge_enum(
            exception_info,
            [(next(iter(info.architectures)), info.enum) for info in framework_infos],
        ),
    )

    return result


"""
    enum_type: Dict[str, EnumTypeInfo] = field(default_factory=dict)
    enum: Dict[str, EnumInfo] = field(default_factory=dict)
    structs: Dict[str, StructInfo] = field(default_factory=dict)
    externs: Dict[str, ExternInfo] = field(default_factory=dict)
    cftypes: Dict[str, CFTypeInfo] = field(default_factory=dict)
    literals: Dict[str, LiteralInfo] = field(default_factory=dict)
    formal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    informal_protocols: Dict[str, ProtocolInfo] = field(default_factory=dict)
    classes: Dict[str, ClassInfo] = field(default_factory=dict)
    aliases: Dict[str, AliasInfo] = field(default_factory=dict)
    expressions: Dict[str, ExpressionInfo] = field(default_factory=dict)
    func_macros: Dict[str, FunctionMacroInfo] = field(default_factory=dict)
    functions: Dict[str, FunctionInfo] = field(default_factory=dict)
"""
