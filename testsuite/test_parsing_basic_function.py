import pathlib
import unittest

import objc
from objective.metadata import datamodel, parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParseBasicFunctions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parsing.FrameworkParser(
            "fragments",
            start_header="basic_function.h",
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        cls.parsed.parse()

    def assert_function(
        self,
        *,
        name,
        retval,
        args,
        inline=False,
        variadic=False,
        printf_format=None,
        availability=datamodel.AvailabilityInfo(),  # noqa: B008
        ignore=False
    ):
        self.assertIn(name, self.parsed.meta.functions)
        info = self.parsed.meta.functions[name]

        self.assertEqual(info.retval, retval)
        self.assertEqual(info.args, args)
        self.assertEqual(info.variadic, variadic)
        self.assertEqual(info.printf_format, printf_format)
        self.assertEqual(info.availability, availability)
        self.assertEqual(info.ignore, ignore)

    def test_simple_functions(self):
        self.assert_function(
            name="function1", retval=datamodel.ReturnInfo(typestr=objc._C_INT), args=[]
        )
        self.assert_function(
            name="function2",
            retval=datamodel.ReturnInfo(typestr=objc._C_FLT),
            args=[datamodel.ArgInfo(typestr=objc._C_INT, name="a")],
        )
        self.assert_function(
            name="function3",
            retval=datamodel.ReturnInfo(typestr=objc._C_VOID),
            args=[
                datamodel.ArgInfo(typestr=objc._C_INT, name=None),
                datamodel.ArgInfo(typestr=objc._C_FLT, name=None),
            ],
        )

        # extern void function3(int, float);

    def test_variadic_functions(self):
        self.assert_function(
            name="vararg_function",
            retval=datamodel.ReturnInfo(typestr=objc._C_INT),
            args=[datamodel.ArgInfo(typestr=objc._C_INT, name=None)],
            variadic=True,
        )

    def test_kandr_functions(self):
        # Classic K&R function, there is no prototype hence assume the function is
        # variadic. That's not entirely true, but this allows recognizing them in
        # other parts of the code base.
        self.assert_function(
            name="kandr_function",
            retval=datamodel.ReturnInfo(typestr=objc._C_INT),
            args=[],
            variadic=True,
        )

    def test_private_function_ignored(self):
        self.assertNotIn("__private_function", self.parsed.meta.functions)

    def test_simple_inline_ffunctions(self):
        self.assert_function(
            name="static_inline_function",
            retval=datamodel.ReturnInfo(typestr=objc._C_INT),
            args=[datamodel.ArgInfo(typestr=objc._C_INT, name=None)],
            inline=True,
        )
        self.assert_function(
            name="extern_inline_function",
            retval=datamodel.ReturnInfo(typestr=objc._C_DBL),
            args=[datamodel.ArgInfo(typestr=objc._C_FLT, name=None)],
            inline=True,
        )
