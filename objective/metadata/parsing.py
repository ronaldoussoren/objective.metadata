"""
Utility module for parsing the header files in a framework and extracting 
interesting definitions.
"""
import operator
import os
import subprocess
import platform


from typecodes import TypeCodes
from ast_tools import parse_int, constant_fold

from objective.cparser import parse_file, c_ast


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
        self.generic_visit(node)
        self._parser.append_enumerator_list(node)

    def visit_Typedef(self, node):
        self.generic_visit(node)
        # TODO: add type to typestr registry

        if not node.type.quals:
            if isinstance(node.type.type, c_ast.Struct):
                self._parser.add_struct(node.name, node.type.type)

    def visit_Decl(self, node):
        self.generic_visit(node)
        if node.name is None:
            return

        if isinstance(node.type, c_ast.TypeDecl) and 'extern' in node.storage:
            self._parser.add_extern(node.name, node.type)

class FrameworkParser (object):
    """
    Parser for framework headers. 
    
    This class uses objective.cparser to to the actual work and stores
    all interesting information found in the headers.
    """
    def __init__(self, framework, arch='x86_64', sdk='/'):
        self.framework = framework
        self.framework_path = '/%s.framework/'%(framework,)
        self.start_header = framework + '.h'
        self.additional_headers = []
        self.arch = arch
        self.sdk = sdk
        
        self.headers = set()

        self.enum_values= {}
        self.structs = {}
        self.externs = {}

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

        self.typecodes = TypeCodes()
        self.typecodes.fill_from_ast(ast)
        
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

            'headers':      self.headers,

            'definitions': {
                'enum':     self.enum_values,
                'structs':  self.structs,
                'externs':  self.externs,
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

        if self.framework_path in node.coord.file:
            i = node.coord.file.index(self.framework_path)
            p = node.coord.file[i+len(self.framework_path) +len('Headers/'):]
             
            self.headers.add(p)
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

    def add_struct(self, name, type):
        self.structs[name] = {
            'typestr': None,
        }

    def add_extern(self, name, type):
        self.externs[name] = {
            'typestr': None,
        }

if __name__ == "__main__":
    p = FrameworkParser('CoreFoundation')
    p.parse()

    import pprint
    pprint.pprint(p.definitions())
