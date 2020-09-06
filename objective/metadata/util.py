import typing

T = typing.TypeVar("T")


class sorted_set(set, typing.Generic[T]):
    def __iter__(self) -> typing.Iterator[T]:
        """
        Yield items of the set in sorted order.
        """
        return iter(sorted(super().__iter__()))
