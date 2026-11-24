from sqlfluff.core.plugin import hookimpl
from sqlfluff.core.rules import BaseRule


@hookimpl
def get_rules() -> list[type[BaseRule]]:
    from trawl.schema.sqlfluff.rules import Rule_Trawl_CT01

    return [Rule_Trawl_CT01]
