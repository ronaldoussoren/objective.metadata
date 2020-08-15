"""
Storage layer for scan and exception data.

The data is stored in text files with JSON encoding.
"""
import json
import re


def _encode_default(obj):
    """
    'default' callback for json.encode,
    encodes set() as a sorted list
    """
    if isinstance(obj, set):
        return list(sorted(obj, key=lambda v: (type(v).__name__, v)))
    elif isinstance(obj, bytes):
        return obj.decode()
    raise TypeError(obj)


def _decode_object(pairs):
    """
    'object_pairs_hook' callback for json.decode.
    If a fieldname is an integer literal convert it to 'int'.

    Field names and values that are of type 'unicode' will
    be converted to 'str', mostly because this makes it
    easier to generate the compiled metadata files.
    """
    result = {}
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
            result[k] = v.encode()

        else:
            result[k] = v
    return result


def save_framework_info(filename, header, data, verbose=False):
    if verbose:
        print("Writing framework info to: " + filename)

    with open(filename, "w") as fp:
        fp.write(header)
        json.dump(data, fp, sort_keys=True, indent=1, default=_encode_default)


__javascript_comment_re = re.compile(
    "(^)?[^\S\n]*/(?:\*(.*?)\*/[^\S\n]*|/[^\n]*)($)?", re.DOTALL | re.MULTILINE
)


def load_framework_info(filename, verbose=False):
    if verbose:
        print("Reading framework info from: " + filename)
    with open(filename) as fp:
        data = fp.read()

        # Get rid of the bogus hash comments
        while data.startswith("#"):
            _, data = data.split("\n", 1)

        # Get rid of "real" JS comments
        match = __javascript_comment_re.search(data)
        while match:
            # single line comment
            data = data[: match.start()] + data[match.end() :]
            match = __javascript_comment_re.search(data)

        # Then hand it to the intolerand JSON parser
        try:
            return json.loads(data, object_pairs_hook=_decode_object)
        except:
            print(filename)
            raise
