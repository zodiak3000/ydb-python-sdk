"""
Experimental
Work in progress, breaking changes are possible.
"""

from __future__ import absolute_import, unicode_literals

import ydb
from ydb.dbapi.errors import NotSupportedError
from ydb.sqlalchemy.types import UInt32, UInt64


try:
    from sqlalchemy.engine.default import DefaultDialect
    from sqlalchemy.sql.compiler import IdentifierPreparer, GenericTypeCompiler, SQLCompiler
    from sqlalchemy import Table
    from sqlalchemy.sql.elements import ClauseList
    from sqlalchemy.sql import functions
    import sqlalchemy as sa
    from sqlalchemy import exc
    from sqlalchemy.util.compat import inspect_getfullargspec
    from sqlalchemy.sql import literal_column

    class YqlIdentifierPreparer(IdentifierPreparer):
        def __init__(self, dialect):
            super(YqlIdentifierPreparer, self).__init__(
                dialect,
                initial_quote='`',
                final_quote='`',
            )

    class YqlTypeCompiler(GenericTypeCompiler):
        def visit_VARCHAR(self, type_, **kw):
            return "STRING"

        def visit_unicode(self, type_, **kw):
            return "UTF8"

        def visit_NVARCHAR(self, type_, **kw):
            return "UTF8"

        def visit_TEXT(self, type_, **kw):
            return "UTF8"

        def visit_FLOAT(self, type_, **kw):
            return "DOUBLE"

        def visit_BOOLEAN(self, type_, **kw):
            return "BOOL"

        def visit_uint32(self, type_, **kw):
            return "UInt32"

        def visit_uint64(self, type_, **kw):
            return "UInt64"

        def visit_uint8(self, type_, **kw):
            return "UInt8"

    class ParametrizedFunction(functions.Function):
        __visit_name__ = 'parametrized_function'

        def __init__(self, name, params, *args, **kwargs):
            super(ParametrizedFunction, self).__init__(
                name, *args, **kwargs)
            self._func_name = name
            self._func_params = params
            self.params_expr = ClauseList(
                operator=functions.operators.comma_op,
                group_contents=True,
                *params
            ).self_group()

    class YqlCompiler(SQLCompiler):

        def visit_lambda(self, lambda_, **kw):
            func = lambda_.func
            spec = inspect_getfullargspec(func)

            if spec.varargs:
                raise exc.CompileError('Lambdas with *args are not supported')

            try:
                keywords = spec.keywords
            except AttributeError:
                keywords = spec.varkw

            if keywords:
                raise exc.CompileError('Lambdas with **kwargs are not supported')

            text = '(' + ', '.join('$' + arg for arg in spec.args) + ')' + ' -> '

            args = [literal_column('$' + arg) for arg in spec.args]
            text += "{ RETURN " + self.process(func(*args), **kw) + " ;}"

            return text

        def visit_parametrized_function(self, func, **kwargs):
            name = func.name
            name_parts = []
            for name in name.split('::'):
                fname = (
                    self.preparer.quote(name)
                    if self.preparer._requires_quotes_illegal_chars(name)
                    or isinstance(name, sa.sql.elements.quoted_name)
                    else name
                )

                name_parts.append(fname)

            name = '::'.join(name_parts)
            params = func.params_expr._compiler_dispatch(self, **kwargs)
            args = self.function_argspec(func, **kwargs)
            return "%(name)s%(params)s%(args)s" % dict(
                name=name, params=params, args=args)

        def visit_function(self, func, add_to_result_map=None, **kwargs):
            # Copypaste of `sa.sql.compiler.SQLCompiler.visit_function` with
            # `::` as namespace separator instead of `.`
            if add_to_result_map is not None:
                add_to_result_map(func.name, func.name, (), func.type)

            disp = getattr(self, "visit_%s_func" % func.name.lower(), None)
            if disp:
                return disp(func, **kwargs)
            else:
                name = sa.sql.compiler.FUNCTIONS.get(func.__class__, None)
                if name:
                    if func._has_args:
                        name += "%(expr)s"
                else:
                    name = func.name
                    name = (
                        self.preparer.quote(name)
                        if self.preparer._requires_quotes_illegal_chars(name)
                        or isinstance(name, sa.sql.elements.quoted_name)
                        else name
                    )
                    name = name + "%(expr)s"
                return "::".join(
                    [
                        (
                            self.preparer.quote(tok)
                            if self.preparer._requires_quotes_illegal_chars(tok)
                            or isinstance(name, sa.sql.elements.quoted_name)
                            else tok
                        )
                        for tok in func.packagenames
                    ]
                    + [name]
                ) % {"expr": self.function_argspec(func, **kwargs)}

    COLUMN_TYPES = {
        ydb.PrimitiveType.Int8: sa.INTEGER,
        ydb.PrimitiveType.Int16: sa.INTEGER,
        ydb.PrimitiveType.Int32: sa.INTEGER,
        ydb.PrimitiveType.Int64: sa.INTEGER,
        ydb.PrimitiveType.Uint8: sa.INTEGER,
        ydb.PrimitiveType.Uint16: sa.INTEGER,
        ydb.PrimitiveType.Uint32: UInt32,
        ydb.PrimitiveType.Uint64: UInt64,
        ydb.PrimitiveType.Float: sa.FLOAT,
        ydb.PrimitiveType.Double: sa.FLOAT,
        ydb.PrimitiveType.String: sa.TEXT,
        ydb.PrimitiveType.Utf8: sa.TEXT,
        ydb.PrimitiveType.Json: sa.JSON,
        ydb.DecimalType: sa.DECIMAL,
        ydb.PrimitiveType.Yson: sa.TEXT,
        ydb.PrimitiveType.Date: sa.DATE,
        ydb.PrimitiveType.Datetime: sa.DATETIME,
        ydb.PrimitiveType.Timestamp: sa.DATETIME,
        ydb.PrimitiveType.Interval: sa.INTEGER,
        ydb.PrimitiveType.Bool: sa.BOOLEAN,
    }

    def _get_column_type(t):
        if isinstance(t.item, ydb.DecimalType):
            return sa.DECIMAL(precision=t.item.precision, scale=t.item.scale)

        return COLUMN_TYPES[t.item]

    class YqlDialect(DefaultDialect):
        name = 'yql'
        supports_alter = False
        max_identifier_length = 63
        supports_sane_rowcount = False

        supports_native_enum = False
        supports_native_boolean = True
        supports_smallserial = False

        supports_sequences = False
        sequences_optional = True
        preexecute_autoincrement_sequences = True
        postfetch_lastrowid = False

        supports_default_values = False
        supports_empty_insert = False
        supports_multivalues_insert = True
        default_paramstyle = 'qmark'

        isolation_level = None

        preparer = YqlIdentifierPreparer
        statement_compiler = YqlCompiler
        type_compiler = YqlTypeCompiler

        def __init(self, **kwargs):
            super(DefaultDialect, self).__init__(**kwargs)

        @staticmethod
        def dbapi():
            import ydb.dbapi
            return ydb.dbapi

        def get_columns(self, connection, table_name, schema=None, **kw):
            if schema is not None:
                raise NotSupportedError

            if isinstance(table_name, Table):
                qt = table_name.name
            else:
                qt = table_name

            columns = connection.raw_connection().describe(qt)
            as_compatible = []
            for column in columns:
                as_compatible.append(
                    {
                        'name': column.name, 'type': _get_column_type(column.type),
                        'nullable': True,
                    }
                )

            return as_compatible

        def has_table(self, connection, table_name, schema=None):
            if schema is not None:
                raise NotSupportedError

            quote = self.identifier_preparer.quote_identifier
            qtable = quote(table_name)

            # TODO: use `get_columns` instead.
            statement = 'SELECT * FROM ' + qtable
            try:
                connection.execute(statement)
                return True
            except Exception:
                return False

except ImportError:
    class YqlDialect(object):
        def __init__(self):
            raise RuntimeError('could not import sqlalchemy')


def register_dialect(
        name='yql',
        module=__name__,
        cls='YqlDialect',
):
    import sqlalchemy as sa
    return sa.dialects.registry.register(name, module, cls)