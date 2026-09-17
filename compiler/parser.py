# compiler/parser.py
from typing import List, Optional, Tuple, Any
from compiler.lexer import Token, TokenType
from compiler.errors import KolError, SourceLocation
from compiler.ast_nodes import *

class Parser:
    def __init__(self, tokens: List[Token], filename: str):
        self.tokens = tokens
        self.filename = filename
        self.pos = 0

    def _curr(self) -> Token:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return self.tokens[-1]

    def _peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return self.tokens[-1]

    def _match(self, *types: TokenType) -> bool:
        if self._curr().type in types:
            self._advance()
            return True
        return False

    def _check(self, *types: TokenType) -> bool:
        return self._curr().type in types

    def _advance(self) -> Token:
        token = self._curr()
        if self.pos < len(self.tokens):
            self.pos += 1
        return token

    def _expect(self, type_: TokenType, msg: str) -> Token:
        if self._check(type_):
            return self._advance()
        raise KolError(f"Expected {type_.name} ({msg}), got {self._curr().type.name} ('{self._curr().value}')", self._curr().location)

    def _skip_newlines(self):
        while self._check(TokenType.NEWLINE):
            self._advance()

    def parse(self) -> ASTNode:
        self._skip_newlines()
        has_main = False
        for tok in self.tokens:
            if tok.type == TokenType.FN and tok.value == "fn":
                idx = self.tokens.index(tok)
                if idx + 1 < len(self.tokens) and self.tokens[idx+1].value == "main":
                    has_main = True
                    break

        statements_or_decls = []
        while not self._check(TokenType.EOF):
            self._skip_newlines()
            if self._check(TokenType.EOF):
                break
            stmt = self._parse_statement_or_decl()
            if stmt:
                statements_or_decls.append(stmt)
            self._skip_newlines()

        if has_main:
            return Program(declarations=statements_or_decls, location=SourceLocation(self.filename, 1, 1))
        else:
            return ScriptProgram(statements=statements_or_decls, location=SourceLocation(self.filename, 1, 1))

    def _parse_statement_or_decl(self) -> ASTNode:
        loc = self._curr().location

        if self._check(TokenType.LET, TokenType.MUT, TokenType.CONST):
            return self._parse_var_decl()

        if self._check(TokenType.FN, TokenType.PURE, TokenType.TASK):
            return self._parse_function_decl()

        if self._check(TokenType.TYPE):
            return self._parse_type_decl()

        if self._check(TokenType.ENUM):
            return self._parse_enum_decl()

        if self._check(TokenType.INTERFACE):
            return self._parse_interface_decl()

        if self._check(TokenType.IF):
            return self._parse_if_stmt()

        if self._check(TokenType.MATCH):
            return self._parse_match_stmt()

        if self._check(TokenType.FOR):
            return self._parse_for_stmt()

        if self._check(TokenType.WHILE):
            return self._parse_while_stmt()

        if self._check(TokenType.LOOP):
            return self._parse_loop_stmt()

        if self._check(TokenType.RETURN):
            self._advance()
            val = None
            if not self._check(TokenType.NEWLINE, TokenType.EOF, TokenType.END):
                val = self._parse_expr()
            return ReturnStmt(value=val, location=loc)

        if self._check(TokenType.BREAK):
            self._advance()
            return BreakStmt(location=loc)

        if self._check(TokenType.CONTINUE):
            self._advance()
            return ContinueStmt(location=loc)

        if self._check(TokenType.FAIL):
            self._advance()
            msg = self._parse_expr()
            return FailStmt(message=msg, location=loc)

        if self._check(TokenType.DEFER):
            self._advance()
            call = self._parse_expr()
            return DeferStmt(call=call, location=loc)

        if self._check(TokenType.ARENA):
            return self._parse_arena_stmt()

        if self._check(TokenType.TEST):
            return self._parse_test_block()

        if self._check(TokenType.USE):
            return self._parse_use_stmt()

        if self._check(TokenType.SYSTEM):
            self._advance()
            self._skip_newlines()
            body = []
            while not self._check(TokenType.END, TokenType.EOF):
                body.append(self._parse_statement_or_decl())
                self._skip_newlines()
            self._expect(TokenType.END, "closing 'end' for system block")
            return SystemBlock(body=body, location=loc)

        if self._check(TokenType.RAW):
            self._advance()
            self._skip_newlines()
            body = []
            while not self._check(TokenType.END, TokenType.EOF):
                body.append(self._parse_statement_or_decl())
                self._skip_newlines()
            self._expect(TokenType.END, "closing 'end' for raw block")
            return RawBlock(body=body, location=loc)

        expr = self._parse_expr()

        if self._check(TokenType.ASSIGN, TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN, TokenType.STAR_ASSIGN, TokenType.SLASH_ASSIGN):
            op_tok = self._advance()
            val = self._parse_expr()
            return Assignment(target=expr, op=op_tok.value, value=val, location=loc)

        if isinstance(expr, TupleExpr) and self._check(TokenType.ASSIGN):
            self._advance()
            val = self._parse_expr()
            return Assignment(target=expr, op="=", value=val, location=loc)

        if isinstance(expr, BinOp) and expr.op == "," and self._check(TokenType.ASSIGN):
            elements = []
            curr = expr
            while isinstance(curr, BinOp) and curr.op == ",":
                elements.insert(0, curr.right)
                curr = curr.left
            elements.insert(0, curr)
            tup = TupleExpr(elements=elements, location=loc)
            self._advance()
            val = self._parse_expr()
            return Assignment(target=tup, op="=", value=val, location=loc)

        return expr

    def _parse_type_annotation(self) -> TypeAnnotation:
        loc = self._curr().location
        if self._check(TokenType.LBRACKET):
            self._advance()
            elem_type = self._parse_type_annotation()
            self._expect(TokenType.RBRACKET, "] in array type")
            t = TypeAnnotation(name="list", generic_args=[elem_type], location=loc)
        elif self._check(TokenType.LBRACE):
            self._advance()
            key_t = self._parse_type_annotation()
            self._expect(TokenType.COLON, ": in map type")
            val_t = self._parse_type_annotation()
            self._expect(TokenType.RBRACE, "} in map type")
            t = TypeAnnotation(name="map", key_type=key_t, val_type=val_t, location=loc)
        elif self._check(TokenType.LPAREN):
            self._advance()
            tuple_types = []
            while not self._check(TokenType.RPAREN, TokenType.EOF):
                tuple_types.append(self._parse_type_annotation())
                if not self._check(TokenType.RPAREN):
                    self._expect(TokenType.COMMA, ", between tuple types")
            self._expect(TokenType.RPAREN, ")")
            t = TypeAnnotation(name="tuple", tuple_types=tuple_types, location=loc)
        elif self._check(TokenType.FN):
            self._advance()
            self._expect(TokenType.LPAREN, "(")
            param_types = []
            while not self._check(TokenType.RPAREN, TokenType.EOF):
                param_types.append(self._parse_type_annotation())
                if not self._check(TokenType.RPAREN):
                    self._expect(TokenType.COMMA, ",")
            self._expect(TokenType.RPAREN, ")")
            ret_t = None
            if self._match(TokenType.ARROW):
                ret_t = self._parse_type_annotation()
            t = TypeAnnotation(name="fn", generic_args=param_types, location=loc)
        else:
            tok = self._expect(TokenType.IDENT, "type name")
            t = TypeAnnotation(name=tok.value, location=loc)
            if self._match(TokenType.LPAREN):
                gen_args = []
                while not self._check(TokenType.RPAREN, TokenType.EOF):
                    gen_args.append(self._parse_type_annotation())
                    if not self._check(TokenType.RPAREN):
                        self._expect(TokenType.COMMA, ",")
                self._expect(TokenType.RPAREN, ")")
                t.generic_args = gen_args

        if self._match(TokenType.QUESTION):
            t.is_optional = True
        elif self._match(TokenType.BANG):
            t.is_fallible = True

        return t

    def _parse_var_decl(self) -> VarDecl:
        loc = self._curr().location
        is_mut = False
        is_const = False

        if self._match(TokenType.MUT):
            is_mut = True
        elif self._match(TokenType.CONST):
            is_const = True
        else:
            self._expect(TokenType.LET, "let")

        tok = self._expect(TokenType.IDENT, "variable name")
        name = tok.value

        type_annot = None
        if self._match(TokenType.COLON):
            type_annot = self._parse_type_annotation()

        val = None
        if self._match(TokenType.ASSIGN):
            val = self._parse_expr()

        return VarDecl(name=name, is_mut=is_mut, is_const=is_const, type_annot=type_annot, value=val, location=loc)

    def _parse_function_decl(self) -> FunctionDecl:
        loc = self._curr().location
        is_pure = self._match(TokenType.PURE)
        is_task = self._match(TokenType.TASK)
        self._expect(TokenType.FN, "fn")

        tok = self._expect(TokenType.IDENT, "function name")
        name = tok.value

        self._expect(TokenType.LPAREN, "(")
        params = []
        while not self._check(TokenType.RPAREN, TokenType.EOF):
            p_loc = self._curr().location
            p_name = self._expect(TokenType.IDENT, "parameter name").value
            p_type = None
            if self._match(TokenType.COLON):
                p_type = self._parse_type_annotation()
            default_val = None
            if self._match(TokenType.ASSIGN):
                default_val = self._parse_expr()
            params.append(Param(name=p_name, type_annot=p_type, default_val=default_val, location=p_loc))
            if not self._check(TokenType.RPAREN):
                self._expect(TokenType.COMMA, ",")
        self._expect(TokenType.RPAREN, ")")

        ret_type = None
        if self._match(TokenType.ARROW):
            ret_type = self._parse_type_annotation()

        generic_params = []
        if self._match(TokenType.FOR):
            self._expect(TokenType.ANY, "any after for")
            g_tok = self._expect(TokenType.IDENT, "generic type parameter name")
            generic_params.append(g_tok.value)

        self._skip_newlines()
        body = []
        while not self._check(TokenType.END, TokenType.EOF):
            stmt = self._parse_statement_or_decl()
            if stmt:
                body.append(stmt)
            self._skip_newlines()
        self._expect(TokenType.END, "closing 'end' for function")

        return FunctionDecl(
            name=name, params=params, return_type=ret_type, body=body,
            is_pure=is_pure, is_task=is_task, generic_params=generic_params, location=loc
        )

    def _parse_type_decl(self) -> TypeDecl:
        loc = self._curr().location
        self._expect(TokenType.TYPE, "type")
        name = self._expect(TokenType.IDENT, "type name").value

        gen_params = []
        if self._match(TokenType.LPAREN):
            gen_tok = self._expect(TokenType.IDENT, "generic param name")
            gen_params.append(gen_tok.value)
            self._expect(TokenType.RPAREN, ")")

        self._skip_newlines()
        fields = []
        methods = []
        impls = []

        while not self._check(TokenType.END, TokenType.EOF):
            if self._check(TokenType.FN, TokenType.PURE, TokenType.TASK):
                methods.append(self._parse_function_decl())
            elif self._check(TokenType.IMPL):
                impls.append(self._parse_impl_block())
            else:
                f_loc = self._curr().location
                f_name = self._expect(TokenType.IDENT, "field name").value
                self._expect(TokenType.COLON, ":")
                f_type = self._parse_type_annotation()
                fields.append(StructField(name=f_name, type_annot=f_type, location=f_loc))
            self._skip_newlines()

        self._expect(TokenType.END, "end for type declaration")
        return TypeDecl(name=name, fields=fields, methods=methods, impls=impls, generic_params=gen_params, location=loc)

    def _parse_enum_decl(self) -> EnumDecl:
        loc = self._curr().location
        self._expect(TokenType.ENUM, "enum")
        name = self._expect(TokenType.IDENT, "enum name").value
        self._skip_newlines()

        variants = []
        while not self._check(TokenType.END, TokenType.EOF):
            v_loc = self._curr().location
            v_name = self._expect(TokenType.IDENT, "variant name").value
            v_fields = []
            if self._match(TokenType.LPAREN):
                while not self._check(TokenType.RPAREN, TokenType.EOF):
                    f_name = self._expect(TokenType.IDENT, "field name").value
                    self._expect(TokenType.COLON, ":")
                    f_type = self._parse_type_annotation()
                    v_fields.append(StructField(name=f_name, type_annot=f_type))
                    if not self._check(TokenType.RPAREN):
                        self._expect(TokenType.COMMA, ",")
                self._expect(TokenType.RPAREN, ")")
            variants.append(EnumVariant(name=v_name, fields=v_fields, location=v_loc))
            self._skip_newlines()

        self._expect(TokenType.END, "end for enum declaration")
        return EnumDecl(name=name, variants=variants, location=loc)

    def _parse_interface_decl(self) -> InterfaceDecl:
        loc = self._curr().location
        self._expect(TokenType.INTERFACE, "interface")
        name = self._expect(TokenType.IDENT, "interface name").value
        self._skip_newlines()

        methods = []
        while not self._check(TokenType.END, TokenType.EOF):
            if self._check(TokenType.FN, TokenType.PURE):
                m_loc = self._curr().location
                is_pure = self._match(TokenType.PURE)
                self._expect(TokenType.FN, "fn")
                m_name = self._expect(TokenType.IDENT, "method name").value
                self._expect(TokenType.LPAREN, "(")
                params = []
                while not self._check(TokenType.RPAREN, TokenType.EOF):
                    p_name = self._expect(TokenType.IDENT, "param name").value
                    p_type = None
                    if self._match(TokenType.COLON):
                        p_type = self._parse_type_annotation()
                    params.append(Param(name=p_name, type_annot=p_type))
                    if not self._check(TokenType.RPAREN):
                        self._expect(TokenType.COMMA, ",")
                self._expect(TokenType.RPAREN, ")")
                ret_t = None
                if self._match(TokenType.ARROW):
                    ret_t = self._parse_type_annotation()
                methods.append(FunctionDecl(name=m_name, params=params, return_type=ret_t, is_pure=is_pure, location=m_loc))
            self._skip_newlines()

        self._expect(TokenType.END, "end for interface declaration")
        return InterfaceDecl(name=name, methods=methods, location=loc)

    def _parse_impl_block(self) -> ImplBlock:
        loc = self._curr().location
        self._expect(TokenType.IMPL, "impl")
        iface_name = self._expect(TokenType.IDENT, "interface name").value
        self._skip_newlines()

        methods = []
        while not self._check(TokenType.END, TokenType.EOF):
            methods.append(self._parse_function_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end for impl block")
        return ImplBlock(interface_name=iface_name, methods=methods, location=loc)

    def _parse_if_stmt(self) -> IfStmt:
        loc = self._curr().location
        self._expect(TokenType.IF, "if")
        cond = self._parse_expr()
        self._skip_newlines()

        then_branch = []
        while not self._check(TokenType.ELIF, TokenType.ELSE, TokenType.END, TokenType.EOF):
            then_branch.append(self._parse_statement_or_decl())
            self._skip_newlines()

        elif_branches = []
        while self._match(TokenType.ELIF):
            elif_cond = self._parse_expr()
            self._skip_newlines()
            elif_body = []
            while not self._check(TokenType.ELIF, TokenType.ELSE, TokenType.END, TokenType.EOF):
                elif_body.append(self._parse_statement_or_decl())
                self._skip_newlines()
            elif_branches.append((elif_cond, elif_body))

        else_branch = None
        if self._match(TokenType.ELSE):
            self._skip_newlines()
            else_branch = []
            while not self._check(TokenType.END, TokenType.EOF):
                else_branch.append(self._parse_statement_or_decl())
                self._skip_newlines()

        self._expect(TokenType.END, "closing 'end' for if statement")
        return IfStmt(condition=cond, then_branch=then_branch, elif_branches=elif_branches, else_branch=else_branch, location=loc)

    def _parse_match_stmt(self) -> MatchStmt:
        loc = self._curr().location
        self._expect(TokenType.MATCH, "match")
        target = self._parse_expr()
        self._skip_newlines()

        arms = []
        while not self._check(TokenType.END, TokenType.EOF):
            self._skip_newlines()
            if self._check(TokenType.END, TokenType.EOF):
                break
            a_loc = self._curr().location
            pat = self._parse_primary()

            guard = None
            if self._match(TokenType.IF):
                guard = self._parse_expr()

            self._expect(TokenType.ARROW, "-> in match arm")

            if self._check(TokenType.NEWLINE):
                self._skip_newlines()
                body = self._parse_statement_or_decl()
            else:
                body = self._parse_statement_or_decl()

            arms.append(MatchArm(pattern=pat, guard=guard, body=body, location=a_loc))
            self._skip_newlines()

        self._expect(TokenType.END, "end for match statement")
        return MatchStmt(expr=target, arms=arms, location=loc)

    def _parse_for_stmt(self) -> ForStmt:
        loc = self._curr().location
        self._expect(TokenType.FOR, "for")
        var1 = self._expect(TokenType.IDENT, "loop variable").value
        index_var = None
        var_name = var1

        if self._match(TokenType.COMMA):
            index_var = var1
            var_name = self._expect(TokenType.IDENT, "second loop variable").value

        self._expect(TokenType.IN, "in")
        iterable = self._parse_expr()

        step = None
        if self._match(TokenType.STEP):
            step = self._parse_expr()

        self._skip_newlines()
        body = []
        while not self._check(TokenType.END, TokenType.EOF):
            body.append(self._parse_statement_or_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end for for loop")
        return ForStmt(var_name=var_name, index_var=index_var, iterable=iterable, step=step, body=body, location=loc)

    def _parse_while_stmt(self) -> WhileStmt:
        loc = self._curr().location
        self._expect(TokenType.WHILE, "while")
        cond = self._parse_expr()
        self._skip_newlines()

        body = []
        while not self._check(TokenType.END, TokenType.EOF):
            body.append(self._parse_statement_or_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end for while loop")
        return WhileStmt(condition=cond, body=body, location=loc)

    def _parse_loop_stmt(self) -> LoopStmt:
        loc = self._curr().location
        self._expect(TokenType.LOOP, "loop")
        self._skip_newlines()

        body = []
        while not self._check(TokenType.END, TokenType.EOF):
            body.append(self._parse_statement_or_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end for loop block")
        return LoopStmt(body=body, location=loc)

    def _parse_arena_stmt(self) -> ArenaStmt:
        loc = self._curr().location
        self._expect(TokenType.ARENA, "arena")
        name = self._expect(TokenType.IDENT, "arena name").value
        self._skip_newlines()

        body = []
        while not (self._check(TokenType.END) and self._peek().type == TokenType.ARENA):
            if self._check(TokenType.EOF):
                raise KolError(f"Unterminated arena block '{name}'", loc)
            body.append(self._parse_statement_or_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end arena")
        self._expect(TokenType.ARENA, "arena in end arena")
        self._expect(TokenType.IDENT, f"arena name {name}")
        return ArenaStmt(name=name, body=body, location=loc)

    def _parse_test_block(self) -> TestBlock:
        loc = self._curr().location
        self._expect(TokenType.TEST, "test")
        name_tok = self._expect(TokenType.STR_LIT, "test name")
        self._skip_newlines()

        body = []
        while not self._check(TokenType.END, TokenType.EOF):
            body.append(self._parse_statement_or_decl())
            self._skip_newlines()

        self._expect(TokenType.END, "end for test block")
        return TestBlock(name=name_tok.value, body=body, location=loc)

    def _parse_use_stmt(self) -> UseStmt:
        loc = self._curr().location
        self._expect(TokenType.USE, "use")
        path_str = ""
        while not self._check(TokenType.COLON, TokenType.NEWLINE, TokenType.EOF):
            path_str += self._curr().value
            self._advance()

        imports = []
        if self._match(TokenType.COLON):
            self._expect(TokenType.LPAREN, "(")
            while not self._check(TokenType.RPAREN, TokenType.EOF):
                imp = self._expect(TokenType.IDENT, "imported identifier").value
                imports.append(imp)
                if not self._check(TokenType.RPAREN):
                    self._expect(TokenType.COMMA, ",")
            self._expect(TokenType.RPAREN, ")")

        return UseStmt(path=path_str.strip(), imports=imports, location=loc)

    def _parse_expr(self) -> ASTNode:
        if self._check(TokenType.IDENT) and self._peek().type == TokenType.ARROW:
            loc = self._curr().location
            p_name = self._advance().value
            self._advance()
            body = self._parse_expr()
            return LambdaExpr(params=[p_name], body=body, location=loc)
        return self._parse_or_catch()

    def _parse_or_catch(self) -> ASTNode:
        expr = self._parse_and()
        while True:
            if self._match(TokenType.OR):
                loc = self._curr().location
                right = self._parse_and()
                expr = OrExpr(expr=expr, default_val=right, location=loc)
            elif self._match(TokenType.CATCH):
                loc = self._curr().location
                err_var = self._expect(TokenType.IDENT, "error variable name").value
                handler = self._parse_statement_or_decl()
                expr = CatchExpr(expr=expr, err_var=err_var, handler=handler, location=loc)
            else:
                break
        return expr

    def _parse_and(self) -> ASTNode:
        expr = self._parse_comparison()
        while self._check(TokenType.AND, TokenType.AMP_AMP):
            op = self._advance().value
            right = self._parse_comparison()
            expr = BinOp(left=expr, op=op, right=right, location=expr.location)
        return expr

    def _parse_comparison(self) -> ASTNode:
        expr = self._parse_range()
        while self._check(TokenType.EQ, TokenType.NEQ, TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE):
            op = self._advance().value
            right = self._parse_range()
            expr = BinOp(left=expr, op=op, right=right, location=expr.location)
        return expr

    def _parse_range(self) -> ASTNode:
        expr = self._parse_addition()
        if self._check(TokenType.RANGE, TokenType.RANGE_INC):
            is_inc = self._curr().type == TokenType.RANGE_INC
            self._advance()
            end_expr = self._parse_addition()
            return RangeExpr(start=expr, end=end_expr, inclusive=is_inc, location=expr.location)
        return expr

    def _parse_addition(self) -> ASTNode:
        expr = self._parse_multiplication()
        while self._check(TokenType.PLUS, TokenType.MINUS):
            op = self._advance().value
            right = self._parse_multiplication()
            expr = BinOp(left=expr, op=op, right=right, location=expr.location)
        return expr

    def _parse_multiplication(self) -> ASTNode:
        expr = self._parse_unary()
        while self._check(TokenType.STAR, TokenType.SLASH, TokenType.MOD, TokenType.POWER):
            op = self._advance().value
            right = self._parse_unary()
            expr = BinOp(left=expr, op=op, right=right, location=expr.location)
        return expr

    def _parse_unary(self) -> ASTNode:
        loc = self._curr().location
        if self._check(TokenType.MINUS, TokenType.NOT, TokenType.BANG, TokenType.TRY, TokenType.AWAIT):
            op_tok = self._advance()
            operand = self._parse_unary()
            if op_tok.type == TokenType.TRY:
                return TryExpr(expr=operand, location=loc)
            elif op_tok.type == TokenType.AWAIT:
                return AwaitExpr(task_expr=operand, location=loc)
            return UnaryOp(op=op_tok.value, operand=operand, location=loc)
        return self._parse_pipe()

    def _parse_pipe(self) -> ASTNode:
        expr = self._parse_postfix()
        while True:
            saved_pos = self.pos
            while self._check(TokenType.NEWLINE):
                self._advance()
            if self._check(TokenType.PIPE_OP):
                self._advance()
                loc = self._curr().location
                right = self._parse_postfix()
                expr = PipeExpr(left=expr, right=right, location=loc)
            else:
                self.pos = saved_pos
                break
        return expr

    def _parse_postfix(self) -> ASTNode:
        expr = self._parse_primary()
        while True:
            loc = self._curr().location
            if self._match(TokenType.LPAREN):
                args = []
                named_args = []
                while not self._check(TokenType.RPAREN, TokenType.EOF):
                    if self._check(TokenType.IDENT) and self._peek().type == TokenType.COLON:
                        arg_name = self._advance().value
                        self._advance()
                        arg_val = self._parse_expr()
                        named_args.append((arg_name, arg_val))
                    else:
                        args.append(self._parse_expr())
                    if not self._check(TokenType.RPAREN):
                        self._expect(TokenType.COMMA, ",")
                self._expect(TokenType.RPAREN, ")")
                expr = CallExpr(callee=expr, args=args, named_args=named_args, location=loc)

            elif self._match(TokenType.DOT):
                member = self._expect(TokenType.IDENT, "field or method name").value
                if self._match(TokenType.LPAREN):
                    args = []
                    while not self._check(TokenType.RPAREN, TokenType.EOF):
                        args.append(self._parse_expr())
                        if not self._check(TokenType.RPAREN):
                            self._expect(TokenType.COMMA, ",")
                    self._expect(TokenType.RPAREN, ")")
                    expr = MethodCallExpr(object=expr, method_name=member, args=args, location=loc)
                else:
                    expr = FieldAccess(target=expr, field_name=member, location=loc)

            elif self._match(TokenType.LBRACKET):
                idx = self._parse_expr()
                self._expect(TokenType.RBRACKET, "]")
                expr = IndexExpr(target=expr, index=idx, location=loc)

            else:
                break
        return expr

    def _parse_primary(self) -> ASTNode:
        loc = self._curr().location

        if self._check(TokenType.INT_LIT):
            val = int(self._advance().value)
            return IntLit(value=val, location=loc)

        if self._check(TokenType.FLOAT_LIT):
            val = float(self._advance().value)
            return FloatLit(value=val, location=loc)

        if self._check(TokenType.STR_LIT):
            val = self._advance().value
            return StrLit(value=val, location=loc)

        if self._check(TokenType.TRUE):
            self._advance()
            return BoolLit(value=True, location=loc)

        if self._check(TokenType.FALSE):
            self._advance()
            return BoolLit(value=False, location=loc)

        if self._check(TokenType.NONE):
            self._advance()
            return NoneLit(location=loc)

        if self._check(TokenType.STR_INTERP_START):
            parts = []
            prefix = self._advance().value
            parts.append(prefix)
            interp_expr = self._parse_expr()
            parts.append(interp_expr)

            while self._check(TokenType.STR_INTERP_MID):
                mid = self._advance().value
                parts.append(mid)
                interp_expr = self._parse_expr()
                parts.append(interp_expr)

            end_part = self._expect(TokenType.STR_INTERP_END, "end of interpolated string").value
            parts.append(end_part)
            return StrInterp(parts=parts, location=loc)

        if self._check(TokenType.IDENT):
            name = self._advance().value
            return Ident(name=name, location=loc)

        if self._match(TokenType.LPAREN):
            if self._check(TokenType.RPAREN):
                self._advance()
                return TupleExpr(elements=[], location=loc)
            first = self._parse_expr()
            if self._match(TokenType.COMMA):
                elements = [first]
                while not self._check(TokenType.RPAREN, TokenType.EOF):
                    elements.append(self._parse_expr())
                    if not self._check(TokenType.RPAREN):
                        self._expect(TokenType.COMMA, ",")
                self._expect(TokenType.RPAREN, ")")
                return TupleExpr(elements=elements, location=loc)
            self._expect(TokenType.RPAREN, ")")
            return first

        if self._match(TokenType.LBRACKET):
            elements = []
            while not self._check(TokenType.RBRACKET, TokenType.EOF):
                elements.append(self._parse_expr())
                if not self._check(TokenType.RBRACKET):
                    self._expect(TokenType.COMMA, ",")
            self._expect(TokenType.RBRACKET, "]")
            return ListExpr(elements=elements, location=loc)

        if self._match(TokenType.LBRACE):
            entries = []
            while not self._check(TokenType.RBRACE, TokenType.EOF):
                k = self._parse_expr()
                self._expect(TokenType.COLON, ": in map literal")
                v = self._parse_expr()
                entries.append(MapEntry(key=k, value=v))
                if not self._check(TokenType.RBRACE):
                    self._expect(TokenType.COMMA, ",")
            self._expect(TokenType.RBRACE, "}")
            return MapExpr(entries=entries, location=loc)

        if self._match(TokenType.ALL):
            self._expect(TokenType.LPAREN, "(")
            tasks = []
            while not self._check(TokenType.RPAREN, TokenType.EOF):
                tasks.append(self._parse_expr())
                if not self._check(TokenType.RPAREN):
                    self._expect(TokenType.COMMA, ",")
            self._expect(TokenType.RPAREN, ")")
            return AllExpr(tasks=tasks, location=loc)

        raise KolError(f"Unexpected token in expression: {self._curr().type.name} ('{self._curr().value}')", loc)
