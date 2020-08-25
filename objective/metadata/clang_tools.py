from . import clang


class NodeVisitor(object):
    def visit(self, node):
        try:
            method = "visit_" + node.kind.name
        except ValueError:
            visitor = self.generic_visit

        else:
            visitor = getattr(self, method, self.generic_visit)

        visitor(node)

    def generic_visit(self, node):
        if hasattr(node, "get_children"):
            for n in list(node.get_children()):
                self.visit(n)


class FilteredVisitor(NodeVisitor):
    def __init__(self, parser):
        self._parser = parser

    def visit(self, node):
        if not self._parser._select_node(node):
            self.generic_visit(node)
            return

        super(FilteredVisitor, self).visit(node)


def dump_type(node, indent=""):
    print(indent + node.kind.name, node.is_pod())
    print(indent + node.spelling)
    print(indent + "nullability: " + str(node.nullability))
    print(indent + "argument_types:", list(node.argument_types()))
    try:
        print(indent + "availability:", node.availability)
    except AttributeError:
        pass
    # dump_type(node.argument_types(), indent + '  ')
    # print(indent + "element_count:", node.element_count)
    # print(indent + "element_type:", node.element_type)
    # print(indent + "from_result:", node.from_result())
    print(indent + "get_array_element_type:", node.get_array_element_type())
    print(indent + "get_array_size:", node.get_array_size())
    print(indent + "data", node.data)
    print(indent + "get_canonical:", node.get_canonical())
    # print(dir(node))
    canonical = node.get_canonical()
    if canonical is not None and node.kind is clang.TypeKind.TYPEDEF:
        dump_type(node.get_canonical(), indent + "  ")
    print(indent + "get_declaration:", node.get_declaration())
    print(indent + "get_pointee:", node.get_pointee())
    print(indent + "get_result:", node.get_result())
    print(indent + "is_const_qualified:", node.is_const_qualified())
    # print(indent + "is_function_variadic:", node.is_function_variadic())
    print(indent + "is_pod:", node.is_pod())
    print(indent + "is_restrict_qualified:", node.is_restrict_qualified())
    print(indent + "kind:", node.kind)
    print(indent + "translation_unit:", node.translation_unit)


def dump_node(node, indent=""):
    header = [type(node).__name__, node.kind.name]
    if node.spelling:
        header.append("spelling=" + node.spelling)
    if node.displayname:
        header.append("displayname=" + node.displayname)
    if node.objc_type_encoding and node.objc_type_encoding != "?":
        header.append("encoding=%r" % (node.objc_type_encoding,))
    header.append("is_attribute=%r" % (node.kind.is_attribute()))
    print(indent + " ".join(header))
    try:
        print(indent + "availability:", node.availability)
    except AttributeError:
        pass

    print(indent + "platform avail", node.platform_availability)
    if node.type:
        print(indent + "  type:")
        dump_type(node.type, indent + "    ")

    children = list(node.get_children())
    if children:
        print(indent + "  children:")
        for ch in children:
            dump_node(ch, indent + "    ")
