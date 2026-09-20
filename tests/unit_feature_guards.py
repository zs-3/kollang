# tests/unit_feature_guards.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

    def test_all_expr_valid(self):
        code = """
task fn fetch_data() -> int
    return 42
end

fn main()
    let a, b = await all(fetch_data(), fetch_data())
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertEqual(len(errors), 0)

    def test_all_expr_invalid_bare(self):
        code = """
task fn fetch_data() -> int
    return 42
end

fn main()
    let res = all(fetch_data(), fetch_data())
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("bare 'all(...)'" in err.message for err in errors))

    def test_system_block(self):
        code = """
fn main()
    system
        let buf = mem.alloc(64)
        mem.free(buf)
    end
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertEqual(len(errors), 0)

    def test_clean_program(self):
        code = """
task fn worker() -> int
    return 5
end

fn main()
    let x = await worker()
    print(x)
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
    let val = all(background_job())
end
"""
        ast = self._parse(code)
        errors = check_unimplemented_features(ast)
        self.assertGreaterEqual(len(errors), 1)

if __name__ == "__main__":
    unittest.main()
