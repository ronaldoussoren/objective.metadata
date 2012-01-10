import json

def _encode_default(obj):
    """
    'default' callback for json.encode,
    encodes set() as a sorted list
    """
    if isinstance(obj, set):
        return list(sorted(obj))
    raise TypeError(obj)

def _decode_object(pairs):
    """ 
    'object_pairs_hook' callback for json.decode. 
    If a fieldname is an integer literal convert it to 'int'.
    """
    result = {}
    for k, v in pairs:
        if k.isdigit():
            result[int(k)] = v
        else:
            result[k] = v
    return result

def save_framework_info(filename, header, data):
    with open(filename, 'w') as fp:
        fp.write(header)
        json.dump(data, fp, sort_keys=True, indent=1, default=_encode_default)

def load_framework_info(filename):
    with open(filename) as fp:
        data = fp.read()
        while data.startswith('#'):
            _, data = data.split('\n', 1)
        return json.loads(data, object_pairs_hook=_decode_object)
