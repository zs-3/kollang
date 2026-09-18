# compiler/ast_nodes.py
from dataclasses import dataclass, field
from typing import List, Optional, Any
from compiler.errors import SourceLocation

@dataclass
class ASTNode:
    location: Optional[SourceLocation] = None

@dataclass
class TypeAnnotation(ASTNode):
    name: str = ""
    is_optional: bool = False
    is_fallible: bool = False
    generic_args: List['TypeAnnotation'] = field(default_factory=list)
    key_type: Optional['TypeAnnotation'] = None
    val_type: Optional['TypeAnnotation'] = None
    tuple_types: List['TypeAnnotation'] = field(default_factory=list)

@dataclass
class Program(ASTNode):
    declarations: List[ASTNode] = field(default_factory=list)

@dataclass
class ScriptProgram(ASTNode):
    statements: List[ASTNode] = field(default_factory=list)

@dataclass
class VarDecl(ASTNode):
    name: str = ""
    is_mut: bool = False
    is_const: bool = False
    type_annot: Optional[TypeAnnotation] = None
    value: Optional[ASTNode] = None

@dataclass
class Param(ASTNode):
    name: str = ""
    type_annot: Optional[TypeAnnotation] = None
    default_val: Optional[ASTNode] = None

@dataclass
class FunctionDecl(ASTNode):
    name: str = ""
    params: List[Param] = field(default_factory=list)
    return_type: Optional[TypeAnnotation] = None
    body: List[ASTNode] = field(default_factory=list)
    is_pure: bool = False
    is_task: bool = False
    generic_params: List[str] = field(default_factory=list)

@dataclass
class TaskDecl(ASTNode):
    fn_decl: FunctionDecl = field(default_factory=FunctionDecl)

@dataclass
class StructField(ASTNode):
    name: str = ""
    type_annot: Optional[TypeAnnotation] = None

@dataclass
class TypeDecl(ASTNode):
    name: str = ""
    fields: List[StructField] = field(default_factory=list)
    methods: List[FunctionDecl] = field(default_factory=list)
    impls: List['ImplBlock'] = field(default_factory=list)
    constants: List[VarDecl] = field(default_factory=list)
    nested_types: List['TypeDecl'] = field(default_factory=list)
    generic_params: List[str] = field(default_factory=list)

@dataclass
class EnumVariant(ASTNode):
    name: str = ""
    fields: List[StructField] = field(default_factory=list)

@dataclass
class EnumDecl(ASTNode):
    name: str = ""
    variants: List[EnumVariant] = field(default_factory=list)

@dataclass
class InterfaceDecl(ASTNode):
    name: str = ""
    methods: List[FunctionDecl] = field(default_factory=list)

@dataclass
class ImplBlock(ASTNode):
    interface_name: str = ""
    methods: List[FunctionDecl] = field(default_factory=list)

@dataclass
class Assignment(ASTNode):
    target: ASTNode = field(default_factory=ASTNode)
    op: str = "="
    value: ASTNode = field(default_factory=ASTNode)

@dataclass
class IfStmt(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    then_branch: List[ASTNode] = field(default_factory=list)
    elif_branches: List[tuple] = field(default_factory=list)
    else_branch: Optional[List[ASTNode]] = None

@dataclass
class MatchArm(ASTNode):
    pattern: ASTNode = field(default_factory=ASTNode)
    guard: Optional[ASTNode] = None
    body: ASTNode = field(default_factory=ASTNode)

@dataclass
class MatchStmt(ASTNode):
    expr: ASTNode = field(default_factory=ASTNode)
    arms: List[MatchArm] = field(default_factory=list)

@dataclass
class ForStmt(ASTNode):
    var_name: str = ""
    index_var: Optional[str] = None
    iterable: ASTNode = field(default_factory=ASTNode)
    step: Optional[ASTNode] = None
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class WhileStmt(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class LoopStmt(ASTNode):
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class ReturnStmt(ASTNode):
    value: Optional[ASTNode] = None

@dataclass
class BreakStmt(ASTNode):
    pass

@dataclass
class ContinueStmt(ASTNode):
    pass

@dataclass
class FailStmt(ASTNode):
    message: ASTNode = field(default_factory=ASTNode)

@dataclass
class DeferStmt(ASTNode):
    call: ASTNode = field(default_factory=ASTNode)

@dataclass
class ArenaStmt(ASTNode):
    name: str = ""
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class TestBlock(ASTNode):
    name: str = ""
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class AssertStmt(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    expr_text: str = ""

@dataclass
class WhenStmt(ASTNode):
    condition: ASTNode = field(default_factory=ASTNode)
    then_branch: List[ASTNode] = field(default_factory=list)
    else_branch: Optional[List[ASTNode]] = None

@dataclass
class SystemBlock(ASTNode):
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class RawBlock(ASTNode):
    body: List[ASTNode] = field(default_factory=list)

@dataclass
class AsmStmt(ASTNode):
    asm_code: str = ""

@dataclass
class UseStmt(ASTNode):
    path: str = ""
    imports: List[str] = field(default_factory=list)

@dataclass
class ExternDecl(ASTNode):
    fn_decl: FunctionDecl = field(default_factory=FunctionDecl)

@dataclass
class BinOp(ASTNode):
    left: ASTNode = field(default_factory=ASTNode)
    op: str = ""
    right: ASTNode = field(default_factory=ASTNode)

@dataclass
class UnaryOp(ASTNode):
    op: str = ""
    operand: ASTNode = field(default_factory=ASTNode)

@dataclass
class PipeExpr(ASTNode):
    left: ASTNode = field(default_factory=ASTNode)
    right: ASTNode = field(default_factory=ASTNode)

@dataclass
class CallExpr(ASTNode):
    callee: ASTNode = field(default_factory=ASTNode)
    args: List[ASTNode] = field(default_factory=list)
    named_args: List[tuple] = field(default_factory=list)

@dataclass
class MethodCallExpr(ASTNode):
    object: ASTNode = field(default_factory=ASTNode)
    method_name: str = ""
    args: List[ASTNode] = field(default_factory=list)

@dataclass
class IndexExpr(ASTNode):
    target: ASTNode = field(default_factory=ASTNode)
    index: ASTNode = field(default_factory=ASTNode)

@dataclass
class FieldAccess(ASTNode):
    target: ASTNode = field(default_factory=ASTNode)
    field_name: str = ""

@dataclass
class IntLit(ASTNode):
    value: int = 0

@dataclass
class FloatLit(ASTNode):
    value: float = 0.0

@dataclass
class StrLit(ASTNode):
    value: str = ""

@dataclass
class BoolLit(ASTNode):
    value: bool = False

@dataclass
class NoneLit(ASTNode):
    pass

@dataclass
class Ident(ASTNode):
    name: str = ""

@dataclass
class StrInterp(ASTNode):
    parts: List[Any] = field(default_factory=list)

@dataclass
class TupleExpr(ASTNode):
    elements: List[ASTNode] = field(default_factory=list)

@dataclass
class ListExpr(ASTNode):
    elements: List[ASTNode] = field(default_factory=list)

@dataclass
class MapEntry(ASTNode):
    key: ASTNode = field(default_factory=ASTNode)
    value: ASTNode = field(default_factory=ASTNode)

@dataclass
class MapExpr(ASTNode):
    entries: List[MapEntry] = field(default_factory=list)

@dataclass
class LambdaExpr(ASTNode):
    params: List[str] = field(default_factory=list)
    body: ASTNode = field(default_factory=ASTNode)

@dataclass
class TryExpr(ASTNode):
    expr: ASTNode = field(default_factory=ASTNode)

@dataclass
class OrExpr(ASTNode):
    expr: ASTNode = field(default_factory=ASTNode)
    default_val: ASTNode = field(default_factory=ASTNode)

@dataclass
class CatchExpr(ASTNode):
    expr: ASTNode = field(default_factory=ASTNode)
    err_var: str = ""
    handler: ASTNode = field(default_factory=ASTNode)

@dataclass
class RangeExpr(ASTNode):
    start: ASTNode = field(default_factory=ASTNode)
    end: ASTNode = field(default_factory=ASTNode)
    inclusive: bool = False

@dataclass
class AwaitExpr(ASTNode):
    task_expr: ASTNode = field(default_factory=ASTNode)

@dataclass
class AllExpr(ASTNode):
    tasks: List[ASTNode] = field(default_factory=list)
