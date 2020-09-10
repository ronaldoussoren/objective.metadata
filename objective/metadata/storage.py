"""
Storage layer for scan and exception data.

The data is stored in text files with JSON encoding.
"""
import json
import os
import re
import typing


def _encode_default(obj: typing.Any) -> typing.Any:
    """
    'default' callback for json.encode,
    encodes set() as a sorted list
    """
    if isinstance(obj, set):
        return sorted(obj, key=lambda v: (type(v).__name__, v))
    elif isinstance(obj, bytes):
        return obj.decode()
    raise TypeError(obj)


def _decode_object(pairs: typing.List[typing.Tuple[str, typing.Any]]) -> typing.Any:
    """
    'object_pairs_hook' callback for json.decode.
    If a fieldname is an integer literal convert it to 'int'.

    Field names and values that are of type 'unicode' will
    be converted to 'str', mostly because this makes it
    easier to generate the compiled metadata files.
    """
    result: typing.Dict[typing.Union[str, int], typing.Any] = {}
    for k, v in pairs:
        if k.isdigit():
            result[int(k)] = v

        elif k in {
            "type",
            "typestr",
            "type_override",
            "typestr_override",
            "sel_of_type",
        }:
            if isinstance(v, list) and len(v) == 2:
                # exception files can contain old metadata with
                # 2 variants for the type [32-bit, 64-bit], this
                # will accept those values and ignores the 32-bit
                # value.
                #
                # This is temporary, those files need to be updated!
                v = v[1]
            assert isinstance(v, str)
            if k in ("typestr_override", "type_override"):
                # Current exception files use ".._override" for replaced
                # type encodings. Translate these to their cannonical form.
                #
                # This is temporary, those files need to be updated!
                result["typestr"] = v.encode()
            else:
                result[k] = v.encode()

        else:
            result[k] = v
    return result


def save_framework_info(
    filename: typing.Union[str, os.PathLike[str]],
    header: str,
    data: typing.Any,
    verbose: bool = False,
) -> None:
    if verbose:
        print(f"Writing framework info to: {filename}")

    with open(filename, "w") as fp:
        fp.write(header)
        json.dump(data, fp, sort_keys=True, indent=1, default=_encode_default)


__javascript_comment_re = re.compile(
    r"(^)?[^\S\n]*/(?:\*(.*?)\*/[^\S\n]*|/[^\n]*)($)?", re.DOTALL | re.MULTILINE
)


def load_framework_info(
    filename: typing.Union[str, os.PathLike[str]], verbose: bool = False
) -> typing.Any:
    if verbose:
        print(f"Reading framework info from: {filename!r}")
    with open(filename) as fp:
        data = fp.read()

        # Get rid of the bogus hash comments
        while data.startswith("#"):
            _, data = data.split("\n", 1)

        # Get rid of "real" JS comments
        match = __javascript_comment_re.search(data)
        while match:
            # single line comment
            data = data[: match.start()] + data[match.end() :]  # noqa: E203
            match = __javascript_comment_re.search(data)

        # Then hand it to the intolerand JSON parser
        try:
            return json.loads(data, object_pairs_hook=_decode_object)
        except BaseException:
            print(filename)
            raise
