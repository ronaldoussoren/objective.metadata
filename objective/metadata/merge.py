import sys
import pprint

def merge_value(cur_list, value, arch):
    for item in cur_list:
        if item['value'] == value:
            item['arch'].add(arch)
            break
    else:
        cur_list.append(
            { 'value': value, 'arch': set([arch]) }
        )

def merge(output, data):
    arch = data['arch']

    all_sections = set()

    for section in ('aliases', 'enum', 'externs', 'literals', 'structs', 'func_macros', 'functions', 'called_definitions'):
        all_sections.add(section)
        if section not in data['definitions']:
            continue

        if section not in output['definitions']:
            output['definitions'][section] = {}

        out = output['definitions'][section]

        for k, v in data['definitions'][section].items():
            outv = out.get(k)
            if outv is None:
                out[k] = [ { 'value': v, 'arch': set([arch]) } ]
            else:
                merge_value(outv, v, arch)


    for section in ('formal_protocols', 'informal_protocols'):
        all_sections.add(section)
        if section not in data['definitions']:
            continue

        if section not in output['definitions']:
            output['definitions'][section] = {}

        out = output['definitions'][section]

        for name, definition in data['definitions'][section].items():
            outv = out.get(name)
            if outv is None:
                out[name] = {
                        'implements': [ 
                            { 'value': definition['implements'], 'arch': set([arch]) },
                        ],
                }
                out[name]['methods'] = dict([
                    (m['selector'], [{ 'value': m, 'arch': set([arch]) }])

                        for m in definition['methods']
                ])
                out[name]['properties'] = dict([
                    (m['name'], [{ 'value': m, 'arch': set([arch]) }])

                        for m in definition['properties']
                ])
            else:
                merge_value(outv['implements'], definition['implements'], arch)
                for item in definition['methods']:
                    if item['selector'] not in outv['methods']:
                        outv['methods'][item['selector']] = [{'value': item, 'arch':set([arch])}]
                    else:
                        merge_value(outv['methods'][item['selector']], item, arch)
                for item in definition['properties']:
                    if item['name'] not in outv['properties']:
                        outv['methods'][item['name']] = [{'value': item, 'arch':set([arch])}]
                    else:
                        merge_value(outv['properties'][item['name']], item, arch)




    # TODO: other sections 


    rest =  set(data['definitions']) - all_sections
    if len(rest) != 0:
        print "WARNING: Haven't merged", ", ".join(rest)


def cleanup(data, all_archs):
    data['archs'] = all_archs

    for section in ('aliases', 'enum', 'externs', 'literals', 'structs', 'functions', 'called_definitions'):
        if section in data['definitions']:
            for k, v in data['definitions'][section].items():
                if len(v) == 1 and v[0]['arch'] == all_archs:
                    del v[0]['arch']

def main():
    if len(sys.argv) < 3:
        print "Usage: %s output.fwkinfo iput1.fwkinfo ..."%(sys.argv[0],)
        sys.exit(1)

    output_fn = sys.argv[1]
    input_fns = sys.argv[2:]


    output = {'definitions': {}}
    all_archs = set()
    for fn in input_fns:
        with open(fn) as fp:
            data = fp.read()
            contents = eval(data)
            all_archs.add(contents['arch'])
            merge(output, contents)

    cleanup(output, all_archs)

    with open(output_fn, 'w') as fp:
        pprint.pprint(output, stream=fp)
