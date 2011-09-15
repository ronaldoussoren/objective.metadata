"""
Utility module for parsing the header files in a framework and extracting 
interesting definitions.
"""
import operator
import os
import subprocess
import platform

from objective.cparser import parse_file, c_ast

OPERATORS = {
    '+':    operator.add,
    '-':    operator.sub,
    '*':    operator.mul,
    '/':    operator.floordiv,
    '<<':   operator.lshift,
    '>>':   operator.rshift,
}

def parse_int(value):
    value = value.lower().rstrip('l').rstrip('u')
    return int(value, 0)

def constant_fold(node):
    """
    Try to constant-fold an expression
    """
    if isinstance(node, c_ast.Constant):
        return node

    if isinstance(node, c_ast.UnaryOp):
        expr = constant_fold(node.expr)
        if isinstance(expr, c_ast.Constant):
            return c_ast.Constant(expr.type, node.op + expr.value, node.coord)
        elif expr is not node.expr:
            return c_ast.UnaryOp(node.op, expr, node.coord)

    elif isinstance(node, c_ast.BinaryOp):
        left  = constant_fold(node.left)
        right = constant_fold(node.right)
        if isinstance(left, c_ast.Constant) and isinstance(right, c_ast.Constant):
            if left.type == 'int' and right.type == 'int':
                left = parse_int(left.value)
                right = parse_int(right.value)

                fun = OPERATORS.get(node.op)
                if fun is not None:
                    return c_ast.Constant('int', str(fun(left, right)), node.coord)

        if left is not node.left or right is not node.right:
            return c_ast.BinaryOp(node.op, left, right, node.coord)

    return node


class FilteredVisitor (c_ast.NodeVisitor):
    """ 
    A node visitor that will only call the visit_* 
    methods for nodes that should be selected according
    to a framework parser
    """
    def __init__(self, parser):
        self._parser = parser

    def visit(self, node):
        if not self._parser._select_node(node):
            self.generic_visit(node)
            return

        super(FilteredVisitor, self).visit(node)

class DefinitionVisitor (FilteredVisitor):
    """
    A NodeVisitor that calls back to the framework parser when it
    locates interesting definitions.
    """
    def visit_EnumeratorList(self, node):
        self._parser.append_enumerator_list(node)


class FrameworkParser (object):
    """
    Parser for framework headers. 
    
    This class uses objective.cparser to to the actual work and stores
    all interesting information found in the headers.
    """
    def __init__(self, framework, arch='x86_64', sdk='/'):
        self.framework = framework
        self.start_header = framework + '.h'
        self.additional_headers = []
        self.arch = arch
        self.sdk = sdk
        

        self.enum_values= {}

    def _gen_includes(self, fp):
        fp.write('#import <%s/%s>\n'%(self.framework, self.start_header))
        for hdr in self.additional_headers:
            fp.write('#import <%s/%s>'%(self.framework, hdr))


    def parse(self):

        # - Generate a temporary file that #imports the framework
        #   We need to create the file because we need to use the 
        #   preprocessor
        fname = '_prs_%s.m'%(self.framework,)
        with open(fname, 'w') as fp:
            self._gen_includes(fp)

        # - Parse the file. 
        #   The -D and -U options are needed to strip out bits of code
        #   that are not yet supported by objective.cparser
        try:
            ast = parse_file(fname, 
                use_cpp=True, cpp_args=[
                    '-E', '-arch', self.arch, '-D__attribute__(x)=', '-D__asm(x)=',
                    '-D__typeof__(x)=long', '-U__BLOCKS__'], cpp_path='clang')
        finally:
            os.unlink(fname)
        
        # - And finally walk the AST to find useful definitions
        visitor = DefinitionVisitor(self)
        visitor.visit(ast)

    def definitions(self):
        """
        Returns a dictionary with information about what was parsed and
        the definitions.
        """
        return {
            'framework':    self.framework,
            'arch':         self.arch,
            'sdk':          self.sdk,
            'release':      platform.mac_ver()[0],

            'definitions': {
                'enum':     self.enum_values,
            },
        }

    def _select_node(self, node):
        """ 
        Return True iff ``node`` is an AST node that's loaded from a 
        header for the current framework.
        """
        if not isinstance(node, c_ast.Node):
            return False

        if node.coord is None:
            return False

        if node.coord.file is None:
            return False

        if '/%s.framework'%(self.framework,) in node.coord.file:
            return True

        return False

    def _calculate_enum_value(self, name):
        fname = '_prs_%s.m'%(self.framework,)
        with open(fname, 'w') as fp:
            self._gen_includes(fp)

            fp.write("#include <stdio.h>\n")
            fp.write("int main(void) {")
            fp.write("   printf(\"%%d\n\", %s);\n"%(name,))
            fp.write("   return 0;\n")
            fp.write("}\n")

        p = subprocess.Popen(['clang', 
            '-o', fname[:-2], 
            'arch', self.arch,
            fname,
            '-framework', self.framework])
        xit = p.wait()
        os.unlink(fname)
        if xit != 0:
            print "WARNING: Cannot calculate value for '%s'"%(name,)
            return None

        p = subprocess.Popen(['./' + fname[:-2]], stdout=subprocess.PIPE)
        data = p.communicate()[0]
        xit = p.wait()
        if xit != 0:
            print "WARNING: Cannot calculate value for '%s'"%(name,)
            return None

        return int(data.strip())


    def append_enumerator_list(self, node):

        prev_name = None
        prev_value = None
        for item in node.children():

            value = item.value
            if value is not None:
                value = constant_fold(value)

            if value is None:
                if prev_value is not None:
                    value = prev_value + 1

                elif prev_name is not None:
                    value = c_ast.BinaryOp(
                        '+', prev_name, c_ast.Constant('int', 1))

                else:
                    value = 0

            elif isinstance(value, c_ast.Constant) and value.type == 'int':
                value = parse_int(value.value)

            prev_name = item.name
            if isinstance(value, int):
                prev_value = value
            else:
                prev_value = None

                value = self._calculate_enum_value(item.name)
                if value is None:
                    continue

            self.enum_values[item.name] = value


if __name__ == "__main__":
    p = FrameworkParser('CoreFoundation')
    p.parse()

    import pprint
    pprint.pprint(p.definitions())
