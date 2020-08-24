import typing

class fat_arch:
    cputype: int

class MachOHeader:
    headers: typing.List[fat_arch]

class MachO:
    headers: typing.List[MachOHeader]
    def __init__(self, filename: str) -> None: ...
