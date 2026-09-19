# tests/unit_feature_guards.py
import unittest
from compiler.lexer import Lexer
from compiler.parser import Parser
from compiler.feature_guards import check_unimplemented_features

class TestFeatureGuards(unittest.TestCase):
    def _parse(self, code: str, filename: str = "test.kol"):
        lexer = Lexer(filename, code)
        tokens = lexer.tokenize()
        parser = Parser(tokens, filename)
        return parser.parse()

    def test_task_and_await(self):
        code = """
task fn fetch_data() -> int
    return 42
end

fn main()
    let res = await fetch_data()
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("not yet implemented" in err.message for err in errors))

    def test_system_block(self):
        code = """
fn main()
    system
        print("sys")
    end
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("system blocks are not yet implemented" in err.message for err in errors))

    def test_clean_program(self):
        code = """
fn main()
    print("hi")
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertEqual(len(errors), 0)

    def test_multiple_unimplemented_features(self):
        code = """
task fn background_job() -> int
    return 100
end

fn main()
    let val = await background_job()
    system
        print("system operation")
    end
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertGreaterEqual(len(errors), 2)

if __name__ == "__main__":
    unittest.main()
