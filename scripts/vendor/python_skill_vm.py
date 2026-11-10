"""A small, metered Python AST evaluator, NOT exec/eval or a CPython sandbox.

Only JSON values cross this interpreter. User functions cannot access host
objects, attributes, modules or a Python frame. The host runs it in a disposable
process too. Syntax outside this documented Python subset fails closed.
"""
import ast
import math
import operator

LANGUAGE = "python_policy_v1"
MAX_SOURCE = 16384
MAX_ITEMS = 4096
MAX_TEXT = 16384
MAX_STEPS = 50000
BUILTINS = {"len", "abs", "min", "max", "sum", "round", "sorted", "range",
            "int", "float", "bool", "list", "dict", "eligible"}
# The generator and evaluator share this contract. Callbacks never become data.
KEY_BUILTINS = {"len", "abs", "int", "float", "bool"}
KEYWORDS = {"sorted": {"key", "reverse"}, "min": {"key", "default"},
            "max": {"key", "default"}, "sum": {"start"},
            "round": {"ndigits"}, "int": {"base"}}
SYNTAX_DESCRIPTION = """List comprehensions are allowed: single-name targets, up to
3 for clauses per comprehension, optional if filters, no async or unpacking.
Keyword arguments: named helper parameters; dict(field=value); sorted(key=,
reverse=); min/max(key=, default=); sum(start=); round(ndigits=); int(base=).
Other builtins and .get/.append accept positional arguments only. No *args/**kwargs.
A key= callback may be None, a one-argument helper, len/abs/int/float/bool,
or a one-argument lambda expression. Lambdas are permitted ONLY directly in key=.
Callbacks execute in the same metered VM; they cannot escape as data.
No dict/set comprehensions, generator expressions, or general lambda functions.
"""
ALLOWED = (
    ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return, ast.Assign,
    ast.AugAssign, ast.Expr, ast.If, ast.For, ast.Break, ast.Continue, ast.Pass,
    ast.Name, ast.Load, ast.Store, ast.Constant, ast.List, ast.Tuple, ast.Dict,
    ast.Subscript, ast.Slice, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Attribute, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot, ast.keyword, ast.ListComp, ast.comprehension, ast.Lambda,
)


class PolicyError(ValueError):
    def __init__(self, code, node=None):
        super().__init__(code)
        self.diagnostic = dict(code=code, node_type=type(node).__name__ if node else None,
            line=getattr(node, "lineno", None), column=getattr(node, "col_offset", None),
            end_line=getattr(node, "end_lineno", None), end_column=getattr(node, "end_col_offset", None))


def parse(source):
    if type(source) is not str or not source or len(source.encode("utf-8")) > MAX_SOURCE:
        raise PolicyError("python_source_size")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as error:
        result = PolicyError("python_syntax")
        result.diagnostic.update(line=error.lineno, column=max(0, (error.offset or 1)-1))
        raise result from None
    except (RecursionError, MemoryError):
        raise PolicyError("python_syntax") from None
    nodes = list(ast.walk(tree))
    if len(nodes) > 2048:
        raise PolicyError("python_ast_limit")
    names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    if (len(names) != len(tree.body) or not 1 <= len(names) <= 16
            or len(names) != len(set(names)) or "propose" not in names):
        raise PolicyError("python_module_requires_unique_functions_and_propose", tree.body[0] if tree.body else None)
    functions = {f.name: f for f in tree.body}
    key_lambdas = set()
    for call in (n for n in nodes if isinstance(n, ast.Call)):
        keys = [k.arg for k in call.keywords]
        if None in keys or len(keys) != len(set(keys)):
            raise PolicyError("python_keyword_unpack_or_duplicate", call)
        if isinstance(call.func, ast.Name):
            name = call.func.id
            allowed = ({a.arg for a in functions[name].args.args} if name in functions
                       else set(keys) if name == "dict" else KEYWORDS.get(name, set()))
            if set(keys) - allowed:
                raise PolicyError("python_keyword_not_supported", call)
            if name in ("sorted", "min", "max"):
                for key in call.keywords:
                    if key.arg != "key":
                        continue
                    value = key.value
                    if isinstance(value, ast.Lambda):
                        key_lambdas.add(value)
                    elif isinstance(value, ast.Name) and value.id in KEY_BUILTINS | set(names):
                        if value.id in functions and len(functions[value.id].args.args) != 1:
                            raise PolicyError("python_key_callback_contract", value)
                    elif not (isinstance(value, ast.Constant) and value.value is None):
                        raise PolicyError("python_key_callback_contract", value)
        elif keys:
            raise PolicyError("python_keyword_not_supported", call)
    for node in nodes:
        if type(node) not in ALLOWED:
            raise PolicyError("python_forbidden_syntax:" + type(node).__name__, node)
        if isinstance(node, (ast.Name, ast.arg, ast.FunctionDef)):
            name = node.id if isinstance(node, ast.Name) else node.arg if isinstance(node, ast.arg) else node.name
            if name.startswith("_") or "__" in name:
                raise PolicyError("python_private_name", node)
        if isinstance(node, ast.Lambda):
            args = node.args
            if (node not in key_lambdas or len(args.args) != 1 or args.posonlyargs
                    or args.kwonlyargs or args.vararg or args.kwarg or args.defaults or args.kw_defaults):
                raise PolicyError("python_lambda_key_only", node)
        if isinstance(node, ast.ListComp) and len(node.generators) > 3:
            raise PolicyError("python_comprehension_clause_limit", node)
        if isinstance(node, ast.comprehension) and (node.is_async or not isinstance(node.target, ast.Name)):
            raise PolicyError("python_comprehension_contract", node.target)
        if isinstance(node, ast.FunctionDef):
            args = node.args
            if (node.name in BUILTINS or args.posonlyargs or args.kwonlyargs
                    or args.vararg or args.kwarg or args.defaults or args.kw_defaults
                    or node.decorator_list or node.returns
                    or any(arg.annotation for arg in args.args)
                    or len({arg.arg for arg in args.args}) != len(args.args)
                    or getattr(node, "type_params", [])):
                raise PolicyError("python_function_contract", node)
            if node.name == "propose" and len(args.args) != 1:
                raise PolicyError("python_propose_takes_one_context", node)
        if isinstance(node, ast.Attribute) and node.attr not in ("get", "append"):
            raise PolicyError("python_attribute_forbidden", node)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id not in BUILTINS | set(names):
                    raise PolicyError("python_unknown_function", node)
            elif not isinstance(node.func, ast.Attribute):
                raise PolicyError("python_indirect_call_forbidden", node)
        if isinstance(node, ast.Assign) and (len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name)):
            raise PolicyError("python_assignment_local_names_only", node)
        if isinstance(node, ast.AugAssign) and not isinstance(node.target, ast.Name):
            raise PolicyError("python_assignment_local_names_only", node)
        if isinstance(node, ast.For) and (not isinstance(node.target, ast.Name) or node.orelse):
            raise PolicyError("python_for_loop_contract", node)
        if isinstance(node, ast.Dict) and any(k is None for k in node.keys):
            raise PolicyError("python_dict_unpack_forbidden", node)
        if isinstance(node, ast.Constant):
            bounded(node.value)
    return tree


def bounded(value, depth=0, count=None):
    """Bound each VM value, including intermediate allocations and numeric size."""
    count = [0] if count is None else count
    count[0] += 1
    if depth > 24 or count[0] > 30000:
        raise PolicyError("python_value_complexity")
    kind = type(value)
    if value is None or kind is bool:
        return value
    if kind is int:
        if value.bit_length() > 256:
            raise PolicyError("python_integer_limit")
    elif kind is float:
        if not math.isfinite(value):
            raise PolicyError("python_nonfinite")
    elif kind is str:
        if len(value) > MAX_TEXT:
            raise PolicyError("python_text_limit")
    elif kind in (list, tuple, dict):
        if len(value) > MAX_ITEMS:
            raise PolicyError("python_container_limit")
        values = value.items() if kind is dict else value
        for item in values:
            bounded(item, depth + 1, count)
    else:
        raise PolicyError("python_non_data_value")
    return value


class _Return(Exception):
    def __init__(self, value):
        self.value = value


class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class VM:
    def __init__(self, source, eligible, max_steps=MAX_STEPS, read_audit=None):
        self.read_audit = read_audit
        self.functions = {f.name: f for f in parse(source).body}
        self.eligible = eligible
        if type(max_steps) is not int or not 1 <= max_steps <= MAX_STEPS:
            raise PolicyError("python_step_budget")
        self.limit, self.steps, self.depth = max_steps, 0, 0

    def tick(self):
        self.steps += 1
        if self.steps > self.limit:
            raise PolicyError("python_step_limit")

    def call(self, name, values, keywords=None):
        self.tick()
        keywords = {} if keywords is None else keywords
        if name == "eligible":
            if len(values) != 1 or keywords:
                raise PolicyError("python_eligible_arity")
            return self.eligible(values[0])
        if name in self.functions:
            function = self.functions[name]
            names = [a.arg for a in function.args.args]
            if (len(values) > len(names) or self.depth >= 16
                    or set(keywords) != set(names[len(values):])):
                raise PolicyError("python_call_arity_or_depth")
            local = dict(zip(names, values))
            local.update(keywords)
            self.depth += 1
            try:
                self.block(function.body, local)
            except _Return as result:
                return bounded(result.value)
            finally:
                self.depth -= 1
            return None
        if name == "range":
            result = range(*values)
            if len(result) > MAX_ITEMS:
                raise PolicyError("python_range_limit")
            return list(result)
        functions = dict(len=len, abs=abs, min=min, max=max, sum=sum, round=round,
                         sorted=sorted, int=int, float=float, bool=bool, list=list, dict=dict)
        result = bounded(functions[name](*values, **keywords))
        if self.read_audit is not None and name == 'dict' and len(values) == 1:
            self.read_audit.copied(values[0], result)
        return result

    def key_callback(self, node, env):
        """Internal host closure only; generated code cannot obtain this object."""
        if isinstance(node, ast.Constant) and node.value is None:
            return None
        def key(value):
            self.tick()
            if isinstance(node, ast.Lambda):
                local = dict(env)
                local[node.args.args[0].arg] = value
                return bounded(self.expr(node.body, local))
            return bounded(self.call(node.id, [value]))
        return key

    def comprehension(self, node, env):
        result, local = [], dict(env)
        count = [1]
        def visit(index):
            generator = node.generators[index]
            sequence = self.expr(generator.iter, local)
            if type(sequence) not in (list, tuple, dict, str):
                raise PolicyError("python_for_requires_data_sequence", generator.iter)
            # Iterate a snapshot, as in the existing for interpreter.
            for item in list(sequence):
                self.tick()
                local[generator.target.id] = item
                if not all(self.expr(condition, local) for condition in generator.ifs):
                    continue
                if index + 1 < len(node.generators):
                    visit(index + 1)
                else:
                    if len(result) >= MAX_ITEMS:
                        raise PolicyError("python_container_limit", node)
                    value = self.expr(node.elt, local)
                    bounded(value, depth=1, count=count)
                    result.append(value)
        visit(0)
        return bounded(result)

    def binary(self, operation, a, b):
        bounded(a)
        bounded(b)
        if type(operation) is ast.Mult:
            seq, repeat = (a, b) if type(b) is int else (b, a)
            if type(seq) in (str, list, tuple) and type(repeat) is int:
                limit = MAX_TEXT if type(seq) is str else MAX_ITEMS
                if len(seq) * max(0, repeat) > limit:
                    raise PolicyError("python_allocation_limit")
        if type(operation) is ast.Add and type(a) in (str, list, tuple) and type(b) is type(a):
            if len(a) + len(b) > (MAX_TEXT if type(a) is str else MAX_ITEMS):
                raise PolicyError("python_allocation_limit")
        # Python %-formatting can allocate huge strings; this subset permits
        # numeric remainder only. No reflection, format or arbitrary methods.
        if type(operation) is ast.Mod and type(a) not in (int, float):
            raise PolicyError("python_numeric_remainder_only")
        fn = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
        return bounded(fn[type(operation)](a, b))

    def expr(self, node, env):
        self.tick()
        if node is None:
            return None
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in env:
                raise PolicyError("python_undefined_local")
            return env[node.id]
        if isinstance(node, (ast.List, ast.Tuple)):
            return bounded([self.expr(x, env) for x in node.elts])
        if isinstance(node, ast.ListComp):
            return self.comprehension(node, env)
        if isinstance(node, ast.Dict):
            return bounded({self.expr(k, env): self.expr(v, env) for k, v in zip(node.keys, node.values)})
        if isinstance(node, ast.Subscript):
            target, key = self.expr(node.value, env), self.expr(node.slice, env)
            if self.read_audit is not None:
                self.read_audit.read(target, key, node.lineno, 'subscript')
            return bounded(target[key])
        if isinstance(node, ast.Slice):
            return slice(self.expr(node.lower, env), self.expr(node.upper, env), self.expr(node.step, env))
        if isinstance(node, ast.BinOp):
            return self.binary(node.op, self.expr(node.left, env), self.expr(node.right, env))
        if isinstance(node, ast.UnaryOp):
            fn = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos}
            return bounded(fn[type(node.op)](self.expr(node.operand, env)))
        if isinstance(node, ast.BoolOp):
            value = None
            for item in node.values:
                value = self.expr(item, env)
                if (isinstance(node.op, ast.And) and not value) or (isinstance(node.op, ast.Or) and value):
                    break
            return value
        if isinstance(node, ast.IfExp):
            return self.expr(node.body if self.expr(node.test, env) else node.orelse, env)
        if isinstance(node, ast.Compare):
            left = self.expr(node.left, env)
            ops = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                   ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                   ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
                   ast.Is: operator.is_, ast.IsNot: operator.is_not}
            for operation, right in zip(node.ops, node.comparators):
                right = self.expr(right, env)
                if not ops[type(operation)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call):
            args = [self.expr(x, env) for x in node.args]
            if isinstance(node.func, ast.Name):
                keywords = {k.arg: self.key_callback(k.value, env)
                            if k.arg == "key" and node.func.id in ("sorted", "min", "max")
                            else self.expr(k.value, env) for k in node.keywords}
                return self.call(node.func.id, args, keywords)
            target = self.expr(node.func.value, env)
            if node.func.attr == "get" and type(target) is dict and 1 <= len(args) <= 2:
                if self.read_audit is not None:
                    self.read_audit.read(target, args[0], node.lineno, 'get')
                return target.get(*args)
            if node.func.attr == "append" and type(target) is list and len(args) == 1:
                # No self-referential data; copy on append prevents cycles.
                from copy import deepcopy
                if len(target) >= MAX_ITEMS:
                    raise PolicyError("python_container_limit")
                target.append(deepcopy(bounded(args[0])))
                bounded(target)
                return None
            raise PolicyError("python_method_contract")
        raise PolicyError("python_expression_unsupported")

    def block(self, nodes, env):
        for node in nodes:
            self.tick()
            if isinstance(node, ast.Return):
                raise _Return(self.expr(node.value, env))
            if isinstance(node, ast.Assign):
                env[node.targets[0].id] = bounded(self.expr(node.value, env))
            elif isinstance(node, ast.AugAssign):
                env[node.target.id] = self.binary(node.op, env[node.target.id], self.expr(node.value, env))
            elif isinstance(node, ast.Expr):
                self.expr(node.value, env)
            elif isinstance(node, ast.If):
                self.block(node.body if self.expr(node.test, env) else node.orelse, env)
            elif isinstance(node, ast.For):
                sequence = self.expr(node.iter, env)
                if type(sequence) not in (list, tuple, dict, str):
                    raise PolicyError("python_for_requires_data_sequence")
                for item in list(sequence):
                    self.tick()
                    env[node.target.id] = item
                    try:
                        self.block(node.body, env)
                    except _Break:
                        break
                    except _Continue:
                        continue
            elif isinstance(node, ast.Break):
                raise _Break()
            elif isinstance(node, ast.Continue):
                raise _Continue()
            elif not isinstance(node, ast.Pass):
                raise PolicyError("python_statement_unsupported")
