"""
Utility module for parsing the header files in a framework and extracting
interesting definitions.
"""
import os
import platform
import re
import sys

import objc

from .clang import (
    Config,
    Cursor,
    CursorKind,
    Index,
    LinkageKind,
    NullabilityKind,
    ObjcDeclQualifier,
    TranslationUnit,
    TranslationUnitLoadError,
    Type,
    TypeKind,
)

Config.set_library_path(
    "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/lib/"  # noqa: B950
)

from objective.metadata.clanghelpers import (  # isort:skip  # noqa: E402
    AbstractClangVisitor,
)


class FrameworkParser(object):
    """
    Parser for framework headers.

    This class uses libclang to do the actual work and stores
    all interesting information found in the headers.
    """

    def __init__(
        self,
        framework,
        arch="x86_64",
        sdk="/",
        start_header=None,
        preheaders=(),
        extraheaders=(),
        link_framework=None,
        only_headers=None,
        typemap=None,
        min_deploy=None,
        verbose=False,
    ):
        self.verbose = verbose
        self.framework = framework
        self.link_framework = (
            link_framework if link_framework is not None else framework
        )
        self.framework_path = "/%s.framework/" % (framework,)

        if start_header is not None:
            self.start_header = start_header
        else:
            self.start_header = "%s/%s.h" % (framework, framework)

        self.only_headers = only_headers

        self.preheaders = preheaders
        self.extraheaders = extraheaders
        self.additional_headers = []
        self.arch = arch
        self.sdk = sdk
        self.typemap = typemap
        self.min_deploy = min_deploy

        self.headers = set()

        self.enum_type = {}
        self.enum_values = {}
        self.structs = {}
        self.externs = {}
        self.literals = {}
        self.aliases = {}
        self.expressions = {}
        self.functions = {}
        self.cftypes = {}
        self.func_macros = {}
        self.typedefs = {}
        self.formal_protocols = {}
        self.informal_protocols = {}
        self.classes = {}
        self.called_definitions = {}
        self.macro_names = set()

    def parse(self):

        index = Index.create()

        translation_unit = None

        if self.verbose:
            print("Beginning compilation with libclang...")

        options = (
            TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD
            | TranslationUnit.PARSE_SKIP_FUNCTION_BODIES
            | TranslationUnit.INCLUDE_ATTRIBUTED_TYPES
            | TranslationUnit.VISIT_IMPLICIT_ATTRIBUTES
        )

        fake_path = "main.m"
        code_string = self.__includes_string()

        unsaved_files = [(fake_path, code_string)]
        args = ["-arch", self.arch, "-isysroot", self.sdk]

        # Handle min OS version deployment setting
        if self.min_deploy:
            # Replaces -DMAC_OS_X_VERSION_MIN_REQUIRED=1040 etc
            args.append("-mmacosx-version-min=" + str(self.min_deploy))

        # Parse
        try:
            translation_unit = index.parse(
                fake_path, args, unsaved_files, options=options
            )
        except TranslationUnitLoadError:
            sys.stderr.write("ERROR: libclang compilation failed")
            exit(1)

        # Not sure this is possible, but...
        if not translation_unit:
            sys.stderr.write("ERROR: libclang compilation failed")
            exit(1)

        if self.verbose:
            print("libclang compilation complete.")
            print("Beginning scan...")

        definitions_visitor = DefinitionVisitor(self)
        definitions_visitor.visit(translation_unit.cursor)

        if self.verbose:
            print("Scan complete.")

    def definitions(self):
        """
        Returns a dictionary with information about what was parsed and
        the definitions.
        """
        return {
            "framework": self.framework,
            "arch": self.arch,
            "sdk": self.sdk,
            "release": platform.mac_ver()[0],
            "headers": sorted(self.headers),
            "definitions": {
                "enum": self.enum_values,
                "enum_type": self.enum_type,
                "structs": self.structs,
                "externs": self.externs,
                "literals": self.literals,
                "aliases": self.aliases,
                "expressions": self.expressions,
                "functions": self.functions,
                "cftypes": self.cftypes,
                "func_macros": self.func_macros,
                "formal_protocols": self.formal_protocols,
                "informal_protocols": self.informal_protocols,
                "classes": self.classes,
                "called_definitions": self.called_definitions,
            },
        }

    @property
    def exceptions(self):
        functions = {}
        for nm, definition in self.functions.items():
            info = {}

            # My Read: Get rid of all function specs that take ptr arguments?
            for idx, a in enumerate(definition["args"]):
                if a["typestr"].startswith(b"^"):
                    a = dict(a)
                    del a["typestr"]
                    del a["name"]
                    try:
                        info["args"][idx] = a
                    except KeyError:
                        info["args"] = {idx: a}

            # My Read: Get rid of all typestrs of pointer return types
            if definition["retval"]["typestr"].startswith(b"^"):
                a = dict(definition["retval"])
                del a["typestr"]
                info["retval"] = a

            # My Read: Copy over the variadicness
            if definition.get("variadic", False):
                info["variadic"] = True

            # My Read: Add it
            if info:
                functions[nm] = info

        prots = {}
        for section in ("formal_protocols", "informal_protocols"):
            prots[section] = {}
            for prot, protdef in getattr(self, section).items():
                for meth in protdef["methods"]:
                    info = {}
                    # My Read: remove metadata for protocol methods that
                    # take pointer args
                    for idx, a in enumerate(meth["args"]):
                        if a["typestr"].startswith(b"^"):
                            a = dict(a)
                            del a["typestr"]
                            del a["typestr_special"]
                            try:
                                info["args"][idx] = a
                            except KeyError:
                                info["args"] = {idx: a}

                    # My Read: remove metadata for protocol methods that return pointers
                    if meth["retval"]["typestr"].startswith(b"^"):
                        a = dict(meth["retval"])
                        del a["typestr"]
                        del a["typestr_special"]
                        info["retval"] = a

                    # My Read: copy over variadic flag
                    if meth.get("variadic", False):
                        info["variadic"] = True

                    # My Read: copy over other stuff
                    if info:
                        if prot not in prots[section]:
                            prots[section][prot] = {}
                        if "methods" not in prots[section][prot]:
                            prots[section][prot]["methods"] = []

                        info["selector"] = meth["selector"]
                        info["class_method"] = meth["class_method"]
                        prots[section][prot]["methods"].append(info)

                # My Read: remove metadata for propeties of pointer types
                for prop in protdef["properties"]:
                    if prop["typestr"].startswith(b"^"):
                        # My Read: for informal prots only, add a dictionary
                        if prot not in self.formal_protocols:
                            prots[section][prot] = {}
                        if "properties" not in prots[section][prot]:
                            prots[section][prot]["properties"] = []

                        prop = dict(prop)
                        del prop["typestr"]
                        del prop["typestr_special"]
                        prots[section][prot]["properties"].append(prop)

        classes = {}
        for clsname, clsdef in self.classes.items():
            for method in clsdef["methods"]:
                info = {}
                for idx, a in enumerate(method["args"]):
                    if a["typestr"].startswith(b"^"):
                        a = dict(a)
                        del a["typestr"]
                        del a["typestr_special"]
                        try:
                            info["args"][idx] = a
                        except KeyError:
                            info["args"] = {idx: a}

                if method["retval"]["typestr"].startswith(b"^"):
                    a = dict(method["retval"])
                    del a["typestr"]
                    del a["typestr_special"]
                    info["retval"] = a

                if method.get("variadic", False):
                    info["variadic"] = True

                if info:
                    if clsname not in classes:
                        classes[clsname] = {}
                    if "methods" not in classes[clsname]:
                        classes[clsname]["methods"] = []

                    info["selector"] = method["selector"]
                    info["class_method"] = method["class_method"]
                    classes[clsname]["methods"].append(info)

            for prop in clsdef["properties"]:
                if prop["typestr"].startswith(b"^"):
                    if clsname not in classes:
                        classes[clsname] = {}
                    if "properties" not in classes[clsname]:
                        classes[clsname]["properties"] = []

                    prop = dict(prop)
                    del prop["typestr"]
                    del prop["typestr_special"]
                    classes[clsname]["properties"].append(prop)

        return {
            "definitions": {
                "functions": functions,
                "formal_protocols": prots["formal_protocols"],
                "informal_protocols": prots["informal_protocols"],
                "classes": classes,
            }
        }

    def should_process_cursor(self, cursor):
        """
        Return True iff ``node`` is an AST node that's loaded from a
        header for the current framework.
        """
        # always descend from the top...
        if cursor.kind == CursorKind.TRANSLATION_UNIT:
            return True

        framework_dir = self.framework_path
        while cursor and cursor.kind != CursorKind.TRANSLATION_UNIT:
            node_file = cursor.location.file
            if node_file and framework_dir in node_file.name:
                # make a note of it in our headers list, unless it's
                # the umbrella headers
                base_name = os.path.basename(node_file.name)
                if base_name != os.path.basename(self.start_header):
                    self.headers.add(base_name)
                # descend
                return True
            else:
                # check the next node up
                cursor = cursor.lexical_parent

        return False

    def add_alias(self, alias, value):
        assert isinstance(alias, str)
        self.aliases[alias] = value

    def add_typedef(self, name, typedef):
        self.typedefs[name] = typedef

    def add_enumeration(self, node):
        # Need to do something with availability information
        name = node.spelling
        if name is None or name == "":
            name = "<anon>"

        value_count = 0

        if node.kind == CursorKind.ENUM_CONSTANT_DECL:
            self.enum_values[name] = {"value": node.enum_value}
            value_count = 1

        elif node.kind == CursorKind.ENUM_DECL:
            self.enum_type[node.spelling] = {"typestr": self.__get_typestr(node)[0]}
            for val in filter(
                lambda x: x.kind == CursorKind.ENUM_CONSTANT_DECL,
                node.get_children() or [],
            ):
                # Add the enum to the name -> value mapping
                val_name = val.spelling
                self.enum_values[val_name] = {
                    "value": val.enum_value,
                    "enum_type": node.spelling,
                }

                # Check to see if there's also an alias inherent in this declaration
                children = list(
                    filter(lambda x: x.kind.is_expression(), val.get_children())
                )
                if len(children) == 1:
                    referenced_decl = children[0].referenced_distinct
                    if (
                        referenced_decl
                        and referenced_decl.kind == CursorKind.ENUM_CONSTANT_DECL
                    ):
                        other_name = referenced_decl.spelling
                        if len(other_name or ""):
                            self.aliases[val_name] = other_name
                elif len(children) > 1:
                    debug_break()

                value_count += 1

        if self.verbose:
            print("Added enumeration: %s with %d values" % (name, value_count))

    def add_cftype(self, name, cftype, override_typestring=None):
        typestr = (
            override_typestring
            if override_typestring is not None
            else self.__get_typestr(cftype)[0]
        )
        self.cftypes[name] = {"typestr": typestr}
        if self.verbose:
            print("Added CFType: " + name)

    def add_struct(self, name, struct_type):
        assert "union" not in name, name
        typestr, special = self.__get_typestr(struct_type, exogenous_name=name)

        fieldnames = []

        walker = struct_type
        while walker and walker.kind == TypeKind.TYPEDEF:
            next_type = walker.next_typedefed_type
            if next_type:
                walker = next_type
            else:
                break

        type_decl = walker.declaration
        if type_decl is not None:
            for field_decl in type_decl.get_struct_field_decls():
                fieldnames.append(field_decl.spelling)
                ts, _ = self.__get_typestr(field_decl.type)
                assert ts

                if b"?" in ts:
                    # print("Skip %s: contains function pointers"%(name,))
                    return

        self.structs[name] = {
            "typestr": typestr,
            "fieldnames": fieldnames,
            "special": special,
        }

        if self.verbose:
            print("Added struct: ", name)

    def add_static_const(self, name, node):
        node_type = node.type
        typestr, _ = self.__get_typestr(node_type)

        if typestr == b"?":
            clang_typestr = node.objc_type_encoding
            if len(str(clang_typestr)) > 0:
                typestr = clang_typestr

        for n in node.get_children():  # noqa: B007
            pass

        else:
            for m in n.get_children():
                if m.kind == CursorKind.INTEGER_LITERAL:
                    self.enum_values[name] = {"value": int(m.token_string)}

                elif m.kind == CursorKind.FLOATING_LITERAL:
                    self.enum_values[name] = {"value": float(m.token_string)}

    def add_extern(self, name, node):
        node_type = node.type
        typestr, special = self.__get_typestr(node_type)

        if not special and b"?" == typestr:
            clang_typestr = node.objc_type_encoding
            if len(str(clang_typestr)) > 0:
                typestr = clang_typestr

        # assert (
        #    name is not None
        #    and typestr is not None
        #    and typestr != b""
        #    and typestr != b"?"
        # ), "Bad params"
        self.externs[name] = extern = {"typestr": typestr}
        self._update_availability(extern, node)
        if self.verbose:
            print("Added extern: " + name + " typestr: " + typestr)

    def add_function(self, node):
        name = node.spelling
        if "CFBundleGetFunctionPointersForNames" in name:
            print("stop")
        if name.startswith("__"):
            return

        funcspec = node.get_function_specifiers()

        self.functions[node.spelling] = func = {"retval": {"typestr": None}, "args": []}
        self._update_availability(func, node)

        # Does it follow the CF returns-retained pattern?
        if "Create" in name:
            parts = name.split("Create", 1)
            second_part = parts[-1]
            first_letter = None if len(second_part) == 0 else second_part[0]
            if not first_letter or first_letter.isupper():
                func["retval"]["already_cfretained"] = True

        elif "Copy" in name:
            parts = name.split("Copy", 1)
            second_part = parts[-1]
            first_letter = None if len(second_part) == 0 else second_part[0]
            if not first_letter or first_letter.isupper():
                func["retval"]["already_cfretained"] = True

        # is function inline?
        if "inline" in funcspec or "__inline__" in funcspec:
            func["inline"] = True

        # get return type
        return_type = node.result_type
        return_type_typestr = self.__get_typestr(return_type)[0]
        func["retval"]["typestr"] = return_type_typestr

        # get arguments
        arg_gen = node.get_arguments()
        args = [] if arg_gen is None else list(arg_gen)

        if node.type.is_function_variadic():
            func["variadic"] = True

        for arg in args:
            arg_type = arg.type

            arginfo = {"name": arg.spelling, "typestr": self.__get_typestr(arg_type)[0]}

            if arg_type.kind == TypeKind.BLOCKPOINTER:
                arginfo["block"] = self.__extract_block(arg.type)

            if arg_type.looks_like_function:
                arginfo["function"] = self.__extract_function(arg_type)

            # get qualifiers
            qualifiers = arg.objc_decl_qualifiers or ObjcDeclQualifier(
                ObjcDeclQualifier.Flag_None
            )

            # update for special cases
            if FrameworkParser.__node_is_cferror_ptr(arg_type):
                # Function returns a CFError object by reference
                qualifiers.is_out = True

                # Where we can pass in 'NULL' if we don't want the error object
                arginfo["null_accepted"] = True

                # User must CFRelease the error object
                arginfo["already_cfretained"] = True

            # write back qualifiers
            encode_str = qualifiers.to_encode_string()
            if encode_str != "":
                arginfo["type_modifier"] = encode_str

            # add the arginfo to the args array
            func["args"].append(arginfo)

        # special handling for CF GetTypeID functions
        if name.endswith("GetTypeID"):
            assert return_type.spelling == "CFTypeID"
            arg_type = name[:-9] + "Ref"
            if arg_type in self.cftypes:
                self.cftypes[arg_type]["gettypeid_func"] = name

        # Ideally libclang would give us full parsing of __attributes__
        # but it doesn't. We do the best we can, nd what libclang gives us
        # is arguably better than is_variadic and arg_name == "format"
        index_of_printf_format_arg = FrameworkParser.__index_of_printf_format_arg(node)
        if index_of_printf_format_arg is not None:
            if index_of_printf_format_arg < len(func["args"]):
                arg = func["args"][index_of_printf_format_arg]
                arg["printf_format"] = True
            else:
                # basically, libclang's C API doesnt expose the values
                # we need to be sure about this in all cases
                for arg in func["args"]:
                    if arg["name"] == "format":
                        arg["printf_format"] = True
        else:
            # Let's check the old way just in case. There *are* functions
            # in Apple's frameworks that don't have
            # the right __attribute__, believe it or not.
            if func.get("variadic"):
                for arg in func["args"]:
                    if arg["name"] == "format":
                        diagnostic_string = (
                            "Looks like we might have a printf function, but we didn't"
                            + "find the attribute; take a look: "
                            + node.get_raw_contents()
                            + str(node.location)
                        )
                        print(diagnostic_string)

        # This is arguably not necessary, but matches the behavior of
        # the previous parsing engine.
        # Ideally we would get rid of this.
        if len(func["args"]) == 0:
            func["args"].append({"name": None, "typestr": b"v"})

        # Log message
        if self.verbose:
            string = (
                "Added function: "
                + name
                + " return typestr: "
                + func["retval"]["typestr"]
                + " args: "
            )
            for idx, arg in enumerate(func["args"]):
                string = (
                    string
                    + " "
                    + str(idx)
                    + ": "
                    + (arg["name"] or "")
                    + ", "
                    + (arg["typestr"] or "")
                )
            print(string)

    def add_protocol(self, node):
        self.formal_protocols[node.spelling] = protocol = {
            "implements": [
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            "methods": [],
            "properties": [],
        }
        self._update_availability(protocol, node)

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                meth["required"] = not decl.is_optional_for_protocol
                protocol["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                this_property = {
                    "name": decl.spelling,
                    "typestr": typestr,
                    "typestr_special": special,
                }
                attributes = decl.get_property_attributes()
                if attributes and len(attributes):
                    this_property["attributes"] = attributes

                protocol["properties"].append(this_property)
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print("Added protocol: " + node.spelling)

    def add_informal_protocol(self, node):
        self.informal_protocols[node.spelling] = protocol = {
            "implements": [
                x.referenced.spelling for x in node.get_adopted_protocol_nodes()
            ],
            "methods": [],
            "properties": [],
        }
        self._update_availability(protocol, node)

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                protocol["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                this_property = {
                    "name": decl.spelling,
                    "typestr": typestr,
                    "typestr_special": special,
                }
                attributes = decl.get_property_attributes()
                if attributes and len(attributes):
                    this_property["attributes"] = attributes

                protocol["properties"].append(this_property)
            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print("Added informal protocol: " + node.spelling)

    def _update_availability(self, meta, node):
        def encode_version(version):
            if version.major == -1:
                raise ValueError("Cannot encode version {version}")
            elif version.minor == -1:
                # "TO BE DEPRECATED"
                return version.major * 10000

            if version.minor < 9:
                return version.major * 100 + version.minor

            else:
                return (
                    version.major * 10000
                    + version.minor * 100
                    + (version.subminor if version.subminor != -1 else 0)
                )

        try:
            availability = node.platform_availability
        except AttributeError:
            return

        if availability["always_unavailable"]:
            suggestion = availability["unavailable_message"]
            meta["unavailable"] = True
            meta["suggestion"] = suggestion if suggestion else "not available"

        if availability["always_deprecated"]:
            # Deprecated without version number, assume this is deprecated starting
            # at the first darwin-based macOS (10.0)
            meta["deprecated"] = 10_00

        try:
            platform = availability["platform"]["macos"]

        except KeyError:
            pass

        else:
            if platform["introduced"] is not None:
                meta["introduced"] = encode_version(platform["introduced"])
            if platform["deprecated"] is not None:
                meta["deprecated"] = encode_version(platform["deprecated"])
                if platform["message"]:
                    meta["deprecated_message"] = platform["message"]
            if platform["unavailable"]:
                meta["unavailable"] = True
                meta["suggestion"] = (
                    platform["message"] if platform["message"] else "not available"
                )

    def add_class(self, node):
        # from .clang_tools import dump_node
        # dump_node(node)

        if node.spelling in self.classes:
            class_info = self.classes[node.spelling]
        else:
            # get superclass
            superclass_name = None
            for child in filter(
                lambda x: x.kind == CursorKind.OBJC_SUPER_CLASS_REF, node.get_children()
            ):
                superclass = child.referenced
                superclass_name = superclass.spelling

            class_info = self.classes[node.spelling] = {
                "name": node.spelling,
                "super": superclass_name,
                "protocols": set(),
                "methods": [],
                "categories": [],
                "properties": [],
            }
            self._update_availability(class_info, node)

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            class_info["protocols"].add(proto_str)

        # This used to track visibility for methods, which is not a thing
        # (only for ivars) and made no sense

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                class_info["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                class_info["properties"].append(
                    {
                        "name": decl.spelling,
                        "typestr": typestr,
                        "typestr_special": special,
                        "attributes": decl.get_property_attributes(),
                    }
                )

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print("Added class: " + node.spelling)

    def add_macro(self, macro_name):
        self.macro_names.add(macro_name)
        if self.verbose:
            print("Noted macro: " + macro_name)

    def add_category(self, node):
        category_name = node.spelling
        print(node.spelling)
        # dump_node(node)
        # class_cursor = node.get_category_class_cursor().referenced
        # class_name = class_cursor.referenced.spelling
        for decl in node.get_children():
            if decl.kind == CursorKind.OBJC_CLASS_REF:
                class_name = decl.spelling
                break
        else:
            raise RuntimeError("Category without class reference")

        if (class_name or "") == "":
            print("s")

        if class_name in self.classes:
            class_info = self.classes[class_name]
        else:
            class_info = self.classes[class_name] = {
                "name": class_name,
                "methods": [],
                "protocols": set(),
                "properties": [],
            }

        for proto_ref in node.get_adopted_protocol_nodes():
            proto_str = proto_ref.referenced.spelling
            class_info["protocols"].add(proto_str)

        for decl in node.get_children() or []:
            if (
                decl.kind == CursorKind.OBJC_INSTANCE_METHOD_DECL
                or decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL
            ):
                meth = self.__extract_methoddecl(decl)
                class_info["methods"].append(meth)

            elif decl.kind == CursorKind.OBJC_PROPERTY_DECL:
                typestr, special = self.__get_typestr(decl.type)
                class_info["properties"].append(
                    {
                        "name": decl.spelling,
                        "typestr": typestr,
                        "typestr_special": special,
                        "attributes": decl.get_property_attributes(),
                    }
                )
                self._update_availability(class_info["properties"][-1], decl)

            else:
                # Declaration can contain nested definitions that are picked
                # up by other code, ignore those here.
                pass

        if self.verbose:
            print(
                "Added category on class: "
                + str(class_name)
                + " named: "
                + str(category_name)
            )

    # This is largely unmolested from the original parser, but
    # operates on a single define and not the whole
    # file and no longer makes its own invocation, but uses
    # libclang with the PARSE_DETAILED_PROCESSING_RECORD option
    def parse_define(self, define_str, curfile=""):
        if not define_str.startswith("#define "):
            define_str = "#define " + define_str
        lines = [define_str]
        for ln_idx, ln in enumerate(lines):

            m = LINE_RE.match(ln)
            if m is not None:
                curfile = m.group(1)

            if curfile is None or self.framework_path not in curfile:
                # Ignore definitions in wrong file
                continue

            if self.only_headers:
                if os.path.basename(curfile) not in self.only_headers:
                    if curfile and "NSSimpleHorizontalTypesetter" in curfile:
                        print("skip", curfile)
                    continue

            m = DEFINE_RE.match(ln)
            if m is not None:
                key = m.group(1)
                if key.startswith("__"):
                    # Ignore private definitions
                    continue

                value = m.group(2)
                if value.endswith("\\"):
                    # Complex macro, ignore
                    print("IGNORE", repr(key), repr(value))
                    continue

                value = value.strip()

                if value in ("static inline",):
                    continue

                if not value:
                    # Ignore empty macros
                    continue

                m = INT_RE.match(value)
                if m is not None:
                    self.enum_values[key] = int(m.group(1), 0)
                    if self.verbose:
                        print(
                            "Added enum_value name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = FLOAT_RE.match(value)
                if m is not None:
                    self.literals[key] = float(m.group(1))
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                m = STR_RE.match(value)
                if m is not None:
                    self.literals[key] = {"unicode": False, "value": m.group(1)}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key]["value"])
                        )
                    continue

                m = UNICODE_RE.match(value)
                if m is not None:
                    self.literals[key] = {"unicode": True, "value": m.group(1)}
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key]["value"])
                        )
                    continue

                m = UNICODE2_RE.match(value)
                if m is not None:
                    self.literals[key] = m.group(1)
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                if value in ("nil", "NULL", "Nil"):
                    self.literals[key] = None
                    if self.verbose:
                        print(
                            "Added macro literal name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )
                    continue

                m = ALIAS_RE.match(value)
                if m is not None:
                    if key.startswith("AVAILABLE_MAC_OS_X"):
                        # Ignore availability macros
                        continue

                    if key.endswith("_EXPORT") or key.endswith("_IMPORT"):
                        continue

                    value = m.group(1)
                    if value not in ("extern", "static", "inline", "float"):
                        self.aliases[key] = m.group(1)
                        if self.verbose:
                            print(
                                "Added alias name: "
                                + key
                                + " value: "
                                + str(self.aliases[key])
                            )
                    continue

                if value == "(INT_MAX-1)":
                    if self.arch in ("i386", "ppc"):
                        self.enum_values[key] = (1 << 31) - 1
                        if self.verbose:
                            print(
                                "Added enum_values name: "
                                + key
                                + " value: "
                                + str(self.enum_values[key])
                            )
                        continue

                    elif self.arch in ("x86_64", "ppc64"):
                        self.enum_values[key] = (1 << 63) - 1
                        if self.verbose:
                            print(
                                "Added enum_values name: "
                                + key
                                + " value: "
                                + str(self.enum_values[key])
                            )
                        continue

                if key in ("NS_DURING", "NS_HANDLER", "NS_ENDHANDLER"):
                    # Classic exception handling in Foundation
                    continue

                if "__attribute__" in value or "__inline__" in value:
                    # It's fairly common to define macros for attributes,
                    # like '#define AM_UNUSED __attribute__((unused))'
                    continue

                if value.startswith("CFUUIDGetConstantUUIDWithBytes("):
                    # Constant CFUUID definitions, used in a number of
                    # frameworks
                    self.called_definitions[key] = value.replace("NULL", "None")
                    if self.verbose:
                        print(
                            "Added called_definitions name: "
                            + key
                            + " value: "
                            + str(self.called_definitions[key])
                        )
                    continue

                m = ERR_SUB_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.enum_values[key] = (v & 0xFFF) << 14
                    if self.verbose:
                        print(
                            "Added enum_values name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = ERR_SYSTEM_DEFINE_RE.match(value)
                if m is not None:
                    v = FrameworkParser.__parse_int(m.group(1))
                    self.enum_values[key] = (v & 0x3F) << 26
                    if self.verbose:
                        print(
                            "Added enum_values name: "
                            + key
                            + " value: "
                            + str(self.enum_values[key])
                        )
                    continue

                m = NULL_VALUE.match(value)
                if m is not None:
                    # For #define's like this:
                    #     #define kDSpEveryContext ((DSpContextReference)NULL)
                    self.literals[key] = None
                    if self.verbose:
                        print(
                            "Added literals name: "
                            + key
                            + " value: "
                            + str(self.literals[key])
                        )

                    continue

                m = SC_SCHEMA_RE.match(value)
                if m is not None:
                    # noinspection PyProtectedMember
                    self.externs[key] = objc._C_ID
                    if self.verbose:
                        print(
                            "Added externs name: "
                            + key
                            + " value: "
                            + str(self.externs[key])
                        )
                    continue

                m = OR_EXPR_RE.match(value)
                if m is not None:
                    self.expressions[key] = value
                    if self.verbose:
                        print(
                            "Added expressions name: "
                            + key
                            + " value: "
                            + str(self.expressions[key])
                        )
                    continue

                m = CALL_VALUE.match(value)
                if m is not None:
                    self.expressions[key] = value
                    if self.verbose:
                        print(
                            "Added expressions name: "
                            + key
                            + " value: "
                            + str(self.expressions[key])
                        )
                    continue

                print("Warning: ignore #define %r %r" % (key, value))

            m = FUNC_DEFINE_RE.match(ln)
            if m is not None:
                proto = m.group(1)
                body = m.group(2).strip()
                if body == "\\":
                    body = body[ln_idx + 1].strip()
                    if body.endswith("\\"):
                        print("Warning: ignore complex function #define %s" % (proto,))
                        continue

                funcdef = "def %s: return %s" % (proto, body)
                try:
                    compile(funcdef, "-", "exec")
                except SyntaxError:
                    pass

                else:
                    key = proto.split("(")[0]
                    self.func_macros[key] = funcdef
                    if self.verbose:
                        print(
                            "Added func_macros name: "
                            + key
                            + " value: "
                            + self.func_macros[key]
                        )

    @staticmethod
    def __node_is_cferror_ptr(node):
        """

        @type node: Cursor
        @param node:
        @rtype : bool
        """
        if isinstance(node, Cursor):
            node = node.type

        if node.kind != TypeKind.POINTER:
            return False

        node = node.get_pointee()

        if getattr(node, "spelling", None) == "CFErrorRef":
            return True

        return False

    @staticmethod
    def __index_of_printf_format_arg(node):
        """

        @type node: Cursor
        @param node:
        @rtype : int
        """
        index_of_format_arg = None
        for child in node.get_children():
            if child.kind.is_attribute():
                has_format_attr = False
                is_printf_not_scanf = False
                for token in child.get_tokens():
                    string = token.spelling
                    if string == "format":
                        has_format_attr = True

                    elif string in [
                        "printf",
                        "__printf__",
                        "NSString",
                        "__NSString__",
                        "CFString",
                        "__CFString__",
                    ]:
                        is_printf_not_scanf = True

                    elif string == "scanf":
                        is_printf_not_scanf = False

                    elif (
                        has_format_attr
                        and is_printf_not_scanf
                        and index_of_format_arg is None
                    ):
                        try:
                            # attribute syntax uses counting
                            # numbers,
                            # we use array indexes
                            val = int(string) - 1

                        except ValueError:
                            index_of_format_arg = None

                        else:
                            index_of_format_arg = val if val >= 0 else None

        return index_of_format_arg

    def __extract_function_like_thing(self, thing):
        """

        @rtype : dict
        """
        return_type = thing.get_result()
        assert return_type.kind != TypeKind.INVALID

        result = {}
        result["retval"] = {"typestr": self.__get_typestr(return_type)[0]}
        result["args"] = []

        arg_types_iter = thing.argument_types()
        arg_types = [] if arg_types_iter is None else list(arg_types_iter)

        if thing.is_function_variadic():
            result["variadic"] = True

        for arg in arg_types:
            assert arg.kind != TypeKind.INVALID
            result["args"].append({"typestr": self.__get_typestr(arg)[0]})

        if len(result["args"]) == 0:
            result["args"].append({"name": None, "typestr": b"v"})

        return result

    def __extract_block(self, block):
        """

        @rtype : dict
        """
        if isinstance(block, Cursor):
            block = block.type

        assert isinstance(block, Type), "huh"

        if isinstance(block, Type) and block.kind == TypeKind.BLOCKPOINTER:
            block = block.get_pointee()

        return self.__extract_function_like_thing(block)

    def __extract_function(self, func):
        """

        @rtype : dict
        """
        if isinstance(func, Cursor):
            func = func.type

        assert isinstance(func, Type), "huh"

        # func = func.get_canonical()
        # Going straight to the canonical robs us of "special" conversions
        if func.kind == TypeKind.TYPEDEF:
            td = func.declaration
            ult = None if td is None else td.underlying_typedef_type_valid
            if ult:
                func = ult
            else:
                func = func.get_canonical()

        while func.kind == TypeKind.POINTER:
            func = func.get_pointee()

        return self.__extract_function_like_thing(func)

    def __extract_methoddecl(self, decl):
        """

        @rtype : dict
        """
        if decl.result_type.valid_type is None:
            typestr, special = objc._C_ID, False
        else:
            typestr, special = self.__get_typestr(decl.result_type.valid_type)

        extras = {}
        type_name = self.get_typename(decl.result_type)
        if type_name is not None:
            extras["type_name"] = type_name

        meth = {
            "selector": decl.spelling,
            "visibility": "public",
            "class_method": bool(decl.kind == CursorKind.OBJC_CLASS_METHOD_DECL),
            "retval": {"typestr": typestr, "typestr_special": special, **extras},
            "args": [],
        }
        self._update_availability(meth, decl)

        if decl.is_variadic:
            meth["variadic"] = True

        for a in decl.get_arguments():
            if a is None:
                typestr, special = objc._C_ID, False
            else:
                typestr, special = self.__get_typestr(a.type)

            extras = {}
            if a.type.nullability == NullabilityKind.NONNULL:
                extras["null_accepted"] = False
            elif a.type.nullability == NullabilityKind.NULLABLE:
                extras["null_accepted"] = True

            type_name = self.get_typename(decl.result_type)
            if type_name is not None:
                extras["type_name"] = type_name

            meth["args"].append(
                {"typestr": typestr, "typestr_special": special, **extras}
            )

        self._update_availability(meth, decl)
        return meth

    def __includes_string(self):
        """

        @rtype : str
        """
        include_string = ""
        for hdr in self.preheaders:
            include_string += "#import <%s>\n" % (hdr,)

        include_string += "#import <%s>\n" % (self.start_header,)

        for hdr in self.additional_headers:
            include_string += "#import <%s/%s>" % (self.framework, hdr)

        for hdr in self.extraheaders:
            include_string += "#import <%s>\n" % (hdr,)

        return include_string

    @staticmethod
    def __parse_int(value):
        """
        Parse a C integer literal and return its value

        @type value: str
        @param value:
        @rtype : int
        """
        if isinstance(value, int):
            return value
        value = value.lower().rstrip("l").rstrip("u")
        return int(value, 0)

    def get_typename(self, obj):
        """
        Return the name of a type when that name is relevant
        for PyObjC (class name, typedef name).
        """
        is_cursor = isinstance(obj, Cursor)
        is_type = (not is_cursor) and isinstance(obj, Type)

        if is_type:
            if obj.kind == TypeKind.ATTRIBUTED:
                return self.get_typename(obj.modified_type)

            primitive_objc_type_for_arch = obj.kind.objc_type_for_arch(self.arch)
            if primitive_objc_type_for_arch == objc._C_ID:
                # This doesn't work yet: return 'id' instead of the
                # name of the class
                return obj.spelling.rstrip("*").strip()

            if obj.kind == TypeKind.TYPEDEF:
                # This needs to check if the typedef is for an interesting type:
                # - struct
                # - enum
                return obj.spelling

            # Missing: Pointer to one of the above should return the name of
            # the pointed-to type.

            return None

        else:
            return None

    def __get_typestr(self, obj, exogenous_name=None):
        """
        There was a bunch of logic around "Special", but in practice,
        the only things that are special are ObjC BOOL and
        anonymous structs in typedef i.e.
            typedef struct { int foo; } MyStruct;
        (Technically that struct is anonymous
        because there's no tag, like this:
            typedef struct _MyStruct { int foo; } MyStruct;

        @type obj: [Cursor, Type]
        @param obj:
        @type exogenous_name: str
        @param exogenous_name:
        @rtype : ( str, bool )
        """
        assert exogenous_name is None or "union" not in exogenous_name
        is_cursor = isinstance(obj, Cursor)
        is_type = (not is_cursor) and isinstance(obj, Type)
        if not is_cursor and not is_type:
            return None, False

        # get the map of arch to typestr
        if not hasattr(obj, "_typestr_cache"):
            obj._typestr_cache = {}

        # see if we have an entry for this arch
        cached_type_str = obj._typestr_cache.get(self.arch)

        # if we don't, make one
        if not cached_type_str:
            if is_cursor:
                cached_type_str = self.__typestr_from_node(
                    obj, exogenous_name=exogenous_name
                )
            elif is_type:
                cached_type_str = self.__typestr_from_type(
                    obj, exogenous_name=exogenous_name
                )

            assert cached_type_str is not None
            obj._typestr_cache[self.arch] = cached_type_str

        # return it
        return cached_type_str

    # noinspection PyProtectedMember
    def __typestr_from_type(self, clang_type, exogenous_name=None):
        """

        @type clang_type: Type
        @param clang_type:
        @type exogenous_name: str
        @param exogenous_name:
        @return: @raise UnsuportedClangOptionError:
        @rtype : ( str, bool )
        """
        assert isinstance(clang_type, Type)

        typestr = None
        special = False
        assert clang_type is not None, "bad type"

        # Check for built in types
        primitive_objc_type_for_arch = clang_type.kind.objc_type_for_arch(self.arch)
        if primitive_objc_type_for_arch:  # should handle all builtins
            typestr = primitive_objc_type_for_arch
            special = False  # used to be "typestr in self._special" - That seems wrong

        # OK, it's not a built-in type

        # CXType_Invalid = 0,
        elif clang_type.kind == TypeKind.INVALID:
            pass  # no typestr for this

        # CXType_Unexposed = 1,
        elif clang_type.kind == TypeKind.UNEXPOSED:
            # Opaque struct?
            typedecl = clang_type.get_declaration()
            if typedecl.kind != CursorKind.NO_DECL_FOUND:
                typestr, special = self.__typestr_from_node(
                    typedecl, exogenous_name=exogenous_name
                )
            elif clang_type.looks_like_function:
                typestr = "?"
                special = False
            else:
                typestr = None
                special = False

        # CXType_Complex = 100,
        elif clang_type.kind == TypeKind.COMPLEX:
            raise UnsuportedClangOptionError(clang_type.kind)

        # CXType_Pointer = 101,
        elif clang_type.kind == TypeKind.POINTER:
            pointee = clang_type.get_pointee()
            t, special = self.__typestr_from_type(
                pointee, exogenous_name=exogenous_name
            )
            typestr = objc._C_PTR + (t if t is not None else "")

        # CXType_BlockPointer = 102,
        elif clang_type.kind == TypeKind.BLOCKPOINTER:
            typestr = objc._C_ID + objc._C_UNDEF

        # CXType_LValueReference = 103,
        elif clang_type.kind == TypeKind.LVALUEREFERENCE:
            raise UnsuportedClangOptionError(clang_type.kind)

        # CXType_RValueReference = 104,
        elif clang_type.kind == TypeKind.RVALUEREFERENCE:
            raise UnsuportedClangOptionError(clang_type.kind)

        # CXType_Record = 105,
        elif clang_type.kind == TypeKind.RECORD:
            typedecl = clang_type.get_declaration()
            assert (
                CursorKind.NO_DECL_FOUND != typedecl.kind
            ), "Couldn't find declaration for non-primitive type"
            exogenous_name = (
                exogenous_name
                if exogenous_name
                else getattr(typedecl.type, "spelling", None)
            )
            if "union" in exogenous_name:
                # Nested anonymous unions cause problems.
                exogenous_name = None

            typestr, special = self.__typestr_from_node(
                typedecl, exogenous_name=exogenous_name
            )

        # CXType_Enum = 106,
        elif clang_type.kind == TypeKind.ENUM:
            typedecl = clang_type.get_declaration()
            assert (
                CursorKind.NO_DECL_FOUND != typedecl.kind
            ), "Couldn't find declaration for non-primitive type"
            typestr, special = self.__typestr_from_node(
                typedecl, exogenous_name=exogenous_name
            )

        # CXType_Typedef = 107,
        elif clang_type.kind == TypeKind.TYPEDEF:
            canonical_type = clang_type.get_canonical()

            if clang_type.ever_defines_to("BOOL"):
                # Give a hint to look into it if things don't add up
                assert canonical_type.kind == TypeKind.SCHAR
                typestr = objc._C_NSBOOL
                special = True
            elif clang_type.ever_defines_to("Boolean"):
                # Give a hint to look into it if things don't add up
                assert canonical_type.kind == TypeKind.UCHAR
                typestr = objc._C_NSBOOL
                special = True

            elif clang_type.ever_defines_to("CFTypeRef"):
                typestr = objc._C_ID

            elif clang_type.ever_defines_to("UniChar"):
                typestr = objc._C_UNICHAR

            else:
                # In an ideal world, we would go right to canonical
                # here, but we can't because we treat some typedefs
                # like BOOL and Boolean and UniChar specially.
                next_type = clang_type.next_typedefed_type
                exogenous_name = (
                    exogenous_name
                    if exogenous_name
                    else clang_type.declaration.spelling
                )
                if next_type:
                    typestr, special = self.__typestr_from_type(
                        next_type, exogenous_name=exogenous_name
                    )
                else:
                    typestr, special = self.__typestr_from_type(
                        canonical_type, exogenous_name=exogenous_name
                    )

                if typestr == b"^?" and clang_type.looks_like_function:
                    # Maybe we can do better in the future?
                    pass

                if b"{" in typestr:
                    # hopefully checking the string is cheaper
                    # than fetching the declaration
                    canonical_type_decl = canonical_type.declaration
                    # anonymous struct... we treat these as "special" for some reason
                    if (
                        canonical_type_decl
                        and canonical_type_decl.kind == CursorKind.STRUCT_DECL
                        and canonical_type_decl.spelling == ""
                    ):
                        special = True

        # CXType_ObjCInterface = 108,
        elif clang_type.kind == TypeKind.OBJCINTERFACE:
            pass  # no typestr for this

        # CXType_ObjCObjectPointer = 109,
        elif clang_type.kind == TypeKind.OBJCOBJECTPOINTER:
            typestr = objc._C_ID

        # CXType_FunctionNoProto = 110,
        elif clang_type.kind == TypeKind.FUNCTIONNOPROTO:
            typestr = b""

        # CXType_FunctionProto = 111,
        elif clang_type.kind == TypeKind.FUNCTIONPROTO:
            typestr = objc._C_UNDEF

        # CXType_ConstantArray = 112,
        elif clang_type.kind in [
            TypeKind.CONSTANTARRAY,
            TypeKind.INCOMPLETEARRAY,
            TypeKind.VECTOR,
        ]:
            result = []
            result.append(objc._C_ARY_B)
            dim = clang_type.element_count
            if dim is None or int(dim) is None or int(dim) == 0:
                t = ""
                s = False
                element_type = clang_type.element_type
                if element_type:
                    t, s = self.__typestr_from_type(
                        element_type, exogenous_name=exogenous_name
                    )
                return objc._C_PTR + t, s
            else:
                result.append(b"%d" % (dim,))

            element_type = clang_type.element_type
            t, s = self.__typestr_from_type(element_type, exogenous_name=exogenous_name)
            result.append(t)
            if s:
                special = True

            result.append(objc._C_ARY_E)
            typestr = b"".join(result)

        # CXType_Vector = 113,
        elif clang_type.kind == TypeKind.VECTOR:
            pass  # handled with CONSTANTARRAY above

        # CXType_IncompleteArray = 114,
        elif clang_type.kind == TypeKind.INCOMPLETEARRAY:
            pass  # handled with CONSTANTARRAY above

        # CXType_VariableArray = 115,
        elif clang_type.kind == TypeKind.VARIABLEARRAY:
            raise UnsuportedClangOptionError(clang_type.kind)

        # CXType_DependentSizedArray = 116,
        elif clang_type.kind == TypeKind.DEPENDENTSIZEDARRAY:
            raise UnsuportedClangOptionError(clang_type.kind)

        # CXType_MemberPointer = 117,
        elif clang_type.kind == TypeKind.MEMBERPOINTER:
            raise UnsuportedClangOptionError(clang_type.kind)

        elif clang_type.kind == TypeKind.ATTRIBUTED:
            return self.__typestr_from_type(clang_type.modified_type, exogenous_name)

        else:
            typedecl = clang_type.get_declaration()
            if typedecl.kind == CursorKind.NO_DECL_FOUND:
                return b"?", True
            # assert (
            # CursorKind.NO_DECL_FOUND != typedecl.kind
            # ), "Couldn't find declaration for non-primitive type"
            typestr, special = self.__typestr_from_node(
                typedecl, exogenous_name=exogenous_name
            )

        return typestr, special

    __typestr_from_node_ignore = [
        CursorKind.OBJC_PROTOCOL_DECL,
        CursorKind.OBJC_INTERFACE_DECL,
        CursorKind.OBJC_CATEGORY_DECL,
        CursorKind.OBJC_IMPLEMENTATION_DECL,
        CursorKind.OBJC_CATEGORY_IMPL_DECL,
        CursorKind.OBJC_SUPER_CLASS_REF,
        CursorKind.OBJC_CLASS_REF,
        CursorKind.OBJC_PROTOCOL_REF,
    ]

    __typestr_from_node_use_type = [
        CursorKind.TYPE_REF,
        CursorKind.OBJC_CLASS_REF,
        CursorKind.UNEXPOSED_DECL,
        CursorKind.INTEGER_LITERAL,
        CursorKind.FLOATING_LITERAL,
        CursorKind.STRING_LITERAL,
        CursorKind.CHARACTER_LITERAL,
        CursorKind.FIELD_DECL,
        CursorKind.ENUM_CONSTANT_DECL,
        CursorKind.FUNCTION_DECL,
        CursorKind.VAR_DECL,
        CursorKind.PARM_DECL,
        CursorKind.OBJC_PROPERTY_DECL,
        CursorKind.OBJC_IVAR_DECL,
    ]

    # noinspection PyProtectedMember
    def __typestr_from_node(self, node, exogenous_name=None):
        """

        @type node: Cursor
        @param node:
        @type exogenous_name: str
        @param exogenous_name:
        @rtype : ( str, bool )
        """
        assert isinstance(node, Cursor)

        special = False
        typestr = node.objc_type_encoding
        if typestr != "?" and typestr != "" and typestr is not None:
            return typestr.encode(), special

        # bail out of irrelevant cases

        if (
            node.kind.is_translation_unit()
            or node.kind.is_attribute()
            or node.kind.is_invalid()
            or node.kind.is_statement()
            or node.kind in self.__typestr_from_node_ignore
        ):
            return None, special

        # drill down into the type
        if node.kind in self.__typestr_from_node_use_type or node.kind.is_expression():
            clang_type = node.type
            typestr, special = self.__typestr_from_type(
                clang_type, exogenous_name=exogenous_name
            )
            assert isinstance(typestr, bytes)
        # follow typedefs
        elif node.kind == CursorKind.TYPEDEF_DECL:
            underlying_type = node.underlying_typedef_type
            typestr, special = self.__typestr_from_type(
                underlying_type, exogenous_name=exogenous_name
            )
            assert isinstance(typestr, bytes)
        # enums
        elif node.kind == CursorKind.ENUM_DECL:
            clang_type = node.enum_type
            typestr, special = self.__typestr_from_type(
                clang_type, exogenous_name=exogenous_name
            )
        # structs
        elif node.kind == CursorKind.STRUCT_DECL:
            result = [objc._C_STRUCT_B]
            if node.spelling is None or node.spelling == "":
                if exogenous_name is not None:
                    result.append(b"_" + exogenous_name.encode())
                    assert not exogenous_name.startswith(
                        "const"
                    ), "Qualifiers leaking into names!"
                    special = True
            else:
                assert not node.spelling.startswith(
                    "const"
                ), "Qualifiers leaking into names!"
                result.append(node.spelling.encode())

            result.append(b"=")

            for c in node.get_children():
                if c.kind.is_attribute():
                    continue
                is_bf = c.is_bitfield()
                bf_width = -1 if not is_bf else c.get_bitfield_width()
                if is_bf and bf_width != -1:
                    result.append(b"b%d" % (bf_width,))
                else:
                    c_type = c.type
                    t, s = self.__typestr_from_type(c_type)
                    if s:
                        special = True
                    assert t is not None

                    result.append(t)

            result.append(objc._C_STRUCT_E)
            typestr, special = b"".join(result), special
        # unions
        elif node.kind == CursorKind.UNION_DECL:
            result = [objc._C_UNION_B]
            if node.spelling is None or node.spelling == "":
                assert (
                    exogenous_name is None or "union" not in exogenous_name
                ), exogenous_name
                if exogenous_name is not None:
                    result.append(b"_" + exogenous_name.encode())
                    assert not exogenous_name.startswith(
                        "const"
                    ), "Qualifiers leaking into names!"
                    special = True
            else:
                assert "union" not in node.spelling, node.spelling
                assert not node.spelling.startswith(
                    "const"
                ), "Qualifiers leaking into names!"
                result.append(node.spelling.encode())

            result.append(b"=")

            for c in node.get_children():
                if c.kind.is_attribute():
                    continue

                t, s = self.__typestr_from_node(c)
                assert isinstance(t, bytes)
                if s:
                    special = True
                if t is not None:
                    result.append(t)

            result.append(objc._C_UNION_E)
            typestr, special = b"".join(result), special
        else:
            # try drilling down to canonical
            canonical = node.canonical
            if node != canonical:
                return self.__typestr_from_node(
                    node.canonical, exogenous_name=exogenous_name
                )
            else:
                print("Unhandled node:", node)

        assert isinstance(typestr, bytes)
        return typestr, special


class UnsuportedClangOptionError(Exception):
    """
    Exception for flagging unsupported/un-implemented language constructs.

    """

    def __init__(self, unsupported_thing=None):
        self.unsupported_thing = unsupported_thing


class DefinitionVisitor(AbstractClangVisitor):
    """
    A AbstractClangVisitor that calls back to the framework parser when it
    locates interesting definitions.
    """

    def __init__(self, parser):
        if parser is None:
            raise ValueError(parser)

        self._parser = parser
        # Record all records seen in header files, even
        # if they are not in a framework we're scanning.
        # This way we can emit the expected metadata
        # for "typedef CGRect NSRect" (as used in the Foundation
        # headers on x86_64)
        self.__all_structs = {}

    def visit(self, node):
        if not self._parser.should_process_cursor(node):
            return

        super(DefinitionVisitor, self).visit(node)

    def visit_var_decl(self, node):
        linkage = node.linkage
        if linkage == LinkageKind.EXTERNAL or linkage == LinkageKind.UNIQUE_EXTERNAL:
            self._parser.add_extern(node.spelling, node)

        elif linkage == LinkageKind.INTERNAL:
            self._parser.add_static_const(node.spelling, node)

    def visit_enum_decl(self, node):
        self._parser.add_enumeration(node)

    def visit_struct_decl(self, node):
        self.descend(node)
        assert "union" not in node.type.spelling
        self.__all_structs[getattr(node.type, "spelling", None)] = node.type

    def visit_objc_protocol_decl(self, node):
        self.descend(node)
        self._parser.add_protocol(node)

    def visit_function_decl(self, node):
        self._parser.add_function(node)

    def visit_typedef_decl(self, node):
        self.descend(node)

        typedef_type = node.type
        typedef_name = typedef_type.declaration.spelling

        underlying_type = node.underlying_typedef_type
        underlying_name = getattr(underlying_type, "spelling", None)
        if "union" in underlying_name:
            from .clang_tools import dump_node

            dump_node(node)
            assert 0

        # Add typedef to parser
        self._parser.add_typedef(typedef_name, underlying_name)

        # Ignore typedefs that resolve to function declarations
        if (
            underlying_type.declaration is not None
            and underlying_type.declaration.kind == CursorKind.FUNCTION_DECL
        ):
            return

        if underlying_name in self._parser.cftypes:
            self._parser.add_alias(typedef_name, underlying_name)

        if underlying_name in self.__all_structs:
            self._parser.add_struct(typedef_name, self.__all_structs[underlying_name])

        canonical_type = typedef_type.get_canonical()
        if canonical_type and canonical_type.kind == TypeKind.RECORD:
            self._parser.add_struct(typedef_name, underlying_type)

        if underlying_type.kind == TypeKind.POINTER:
            pointee_type = underlying_type.get_pointee()
            pointee_type_decl = None if not pointee_type else pointee_type.declaration
            if (
                pointee_type_decl is not None
                and pointee_type_decl.kind == CursorKind.STRUCT_DECL
            ):
                if pointee_type_decl.spelling.startswith("__"):
                    self._parser.add_cftype(typedef_name, typedef_type)

    def visit_objc_category_decl(self, node):
        self.descend(node)

        self._parser.add_category(node)

        if node.get_is_informal_protocol():
            self._parser.add_informal_protocol(node)

    def visit_objc_interface_decl(self, node):
        self.descend(node)
        self._parser.add_class(node)

    def visit_macro_definition(self, node):
        macro = node.reconstitute_macro()
        file_name = (
            "" if node.extent.start.file is None else node.extent.start.file.name
        )
        self._parser.parse_define(macro, file_name)


LINE_RE = re.compile(r'^# \d+ "([^"]*)" ')
DEFINE_RE = re.compile(r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$")
INT_RE = re.compile(
    r"^\(?(?:\([A-Za-z][A-Za-z0-9]*(?:\s+long)?\))?((?:-?0[Xx][0-9A-Fa-f]+)|(?:-?\d+))[UL]*\)?$"  # noqa: B950
)
FLOAT_RE = re.compile(r"^\(?(?:\([A-Za-z][A-Za-z0-9]*\))?\(?([-]?\d+\.\d+)\)?\)?$")
STR_RE = re.compile(r'^"(.*)"$')
UNICODE_RE = re.compile(r'^@"(.*)"$')
UNICODE2_RE = re.compile(r'^CFSTR\("(.*)"\)$')
ALIAS_RE = re.compile(r"^(?:\(\s*[A-Za-z0-9_]+\s*\))?\s*([A-Za-z_][A-Za-z0-9_]*)$")
NULL_VALUE = re.compile(r"\(\([A-Za-z0-9]+\)NULL\)")
CALL_VALUE = re.compile(r"^[A-Za-z0-9]+\([A-Za-z0-9]*\)$")
FUNC_DEFINE_RE = re.compile(
    r"#\s*define\s+([A-Za-z_][A-Za-z0-9_]*\([A-Za-z0-9_, ]*\))\s+(.*)$"
)
ERR_SUB_DEFINE_RE = re.compile(r"err_sub\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)")
ERR_SYSTEM_DEFINE_RE = re.compile(r"err_system\s*\(\s*((?:0x)?[0-9a-fA-F]+)\s*\)")
SC_SCHEMA_RE = re.compile(r"SC_SCHEMA_KV\s*\(\s*([A-Za-z0-9_]*)\s*,.*\)")
OR_EXPR_RE = re.compile(r"\([A-Za-z0-9]*(\s*\|\s*[A-Za-z0-9]*)*\)")


def debug_break():
    if False:
        pass
