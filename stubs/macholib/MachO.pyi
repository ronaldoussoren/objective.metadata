import typing

class mach_header:
    magic: int
    cputype: int
    cpusubtype: int
    filetype: int
    ncmd: int
    sizeofcmds: int
    flags: int

class MachOHeader:
    header: mach_header

class MachO:
    headers: typing.List[MachOHeader]
    def __init__(self, filename: str) -> None: ...
