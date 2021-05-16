import pathlib
import unittest

from objective.metadata import parsing, xcode

FRAGMENTS = pathlib.Path(__file__).parent / "fragments.framework"


class TestParsingDefines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parsed = parsing.FrameworkParser(
            "fragments",
            start_header="defines.h",
            sdk=xcode.path_for_default_sdk(),
            cflags=["-isystem", str(FRAGMENTS)],
        )
        cls.parsed.parse()

    def assert_alias(
        self,
        *,
        name,
        alias,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.aliases)
        info = self.parsed.meta.aliases[name]

        self.assertEqual(info.alias, alias)
        self.assertIs(info.availability, None)
        self.assertIs(info.ignore, ignore)

    def assert_define_value(
        self,
        *,
        name,
        value,
        unicode=False,
        ignore=False,
    ):
        self.assertIn(name, self.parsed.meta.literals)
        info = self.parsed.meta.literals[name]

        self.assertEqual(info.value, value)
        self.assertEqual(info.unicode, unicode)
        self.assertIs(info.ignore, ignore)

    def assert_function_macro(self, *, name, definition, ignore=False):
        self.assertIn(name, self.parsed.meta.func_macros)
        info = self.parsed.meta.func_macros[name]

        try:
            gl = {}
            exec(info.definition, gl, gl)
        except Exception as exc:
            self.fail(f"Cannot compile definition {exc}")

        self.assertIn(name, gl)
        self.assertIsInstance(gl[name], type(lambda: None))

        self.assertEqual(info.definition, definition)
        self.assertIs(info.availability, None)
        self.assertIs(info.ignore, ignore)

    def assert_expression(self, *, name, expression):
        self.assertIn(name, self.parsed.meta.expressions)
        info = self.parsed.meta.expressions[name]

        self.assertEqual(info.expression, expression)

    def test_alias(self):
        self.assert_alias(name="ALIAS", alias="NSWindowBackingLocationDefault")

    def test_basic_values(self):
        self.assert_define_value(name="INT_VALUE", value=42)
        self.assert_define_value(name="HEX_VALUE", value=0x20)
        self.assert_define_value(name="OCT_VALUE", value=0o30)
        self.assert_define_value(name="FLOAT_VALUE", value=19.5)

    def test_string_values(self):
        self.assert_define_value(name="BYTESTR", value="hello", unicode=False)
        self.assert_define_value(name="CORESTRING", value="world", unicode=True)
        self.assert_define_value(name="OBJSTRING", value="moon", unicode=True)

    def test_function_macros(self):
        self.assert_function_macro(
            name="DOUBLEUP", definition="def DOUBLEUP(a): return ((a) * 2)"
        )
        self.assert_function_macro(
            name="DIFFERENCE", definition="def DIFFERENCE(a, b): return ((b) - (a))"
        )

    def test_null_values(self):
        self.assert_define_value(name="NULL_POINTER", value=None)
        self.assert_define_value(name="NIL_POINTER", value=None)
        self.assert_define_value(name="NIL_CLASS", value=None)
        self.assert_define_value(name="CASTED_NULL", value=None)

    def test_expressions(self):
        self.assert_expression(name="ORED_VALUES", expression="N1|N2|N3")
        self.assert_expression(name="FUNCTION_VALUE", expression="SomeFunction(42)")
