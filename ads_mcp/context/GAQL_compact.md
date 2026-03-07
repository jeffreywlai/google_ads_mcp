# Google Ads Query Language (GAQL)

## Grammar

```
Query            -> SelectClause FromClause WhereClause? OrderByClause?
                    LimitClause? ParametersClause?
SelectClause     -> SELECT FieldName (, FieldName)*
FromClause       -> FROM ResourceName
WhereClause      -> WHERE Condition (AND Condition)*
OrderByClause    -> ORDER BY Ordering (, Ordering)*
LimitClause      -> LIMIT PositiveInteger
ParametersClause -> PARAMETERS Literal = Value (, Literal = Value)*

Condition        -> FieldName Operator Value
Operator         -> = | != | > | >= | < | <= | IN | NOT IN |
                    LIKE | NOT LIKE | CONTAINS ANY | CONTAINS ALL |
                    CONTAINS NONE | IS NULL | IS NOT NULL | DURING |
                    BETWEEN | REGEXP_MATCH | NOT REGEXP_MATCH
Value            -> Literal | LiteralList | Number | NumberList | String |
                    StringList | Function
Ordering         -> FieldName (ASC | DESC)?

FieldName        -> [a-z] ([a-zA-Z0-9._])*
ResourceName     -> [a-z] ([a-zA-Z_])*

StringList       -> ( String (, String)* )
LiteralList      -> ( Literal (, Literal)* )
NumberList       -> ( Number (, Number)* )

PositiveInteger  -> [1-9] ([0-9])*
Number           -> -? [0-9]+ (. [0-9] [0-9]*)?
String           -> (' Char* ') | (" Char* ")
Literal          -> [a-zA-Z0-9_]*

Function         -> LAST_14_DAYS | LAST_30_DAYS | LAST_7_DAYS |
                    LAST_BUSINESS_WEEK | LAST_MONTH | LAST_WEEK_MON_SUN |
                    LAST_WEEK_SUN_SAT | THIS_MONTH | THIS_WEEK_MON_TODAY |
                    THIS_WEEK_SUN_TODAY | TODAY | YESTERDAY
```

## Rules

- `SELECT` and `FROM` are required. `WHERE`, `ORDER BY`, `LIMIT`, `PARAMETERS` are optional.
- Only one resource in `FROM`. Attributed resource fields are implicitly joined.
- `REGEXP_MATCH` uses RE2 syntax.
- `LIKE` escaping: surround `[`, `]`, `%`, `_` in square brackets (e.g., `'[[]Earth[_]to[_]Mars[]]%'`). Only works on string fields, not arrays.
- `AND` separates conditions, not values within a condition.
- Segments in `WHERE` must also be in `SELECT`, except core date segments: `segments.date`, `segments.week`, `segments.month`, `segments.quarter`, `segments.year`.
- If any core date segment is selected, at least one must filter with a finite range in `WHERE`.
- Non-selectable fields (`Selectable=false`) and repeated fields (`isRepeated=true`) cannot be in `SELECT`.
- `resource_name` of the main resource is always returned even if not selected.
- Metrics and segments can be selected without resource fields.
- `resource_name` fields can be used to filter or order (e.g., `WHERE campaign.resource_name = 'customers/123/campaigns/456'`).
- `ORDER BY` defaults to `ASC` when direction is not specified.
- Not all segments and metrics are compatible with each other or with the `FROM` resource.
- Case-sensitivity matters when filtering with operators.
- `PARAMETERS include_drafts=true` returns draft entities.

## Example

```
SELECT
    campaign.id,
    campaign.name,
    metrics.clicks,
    segments.device
FROM campaign
WHERE segments.date DURING LAST_30_DAYS
    AND metrics.impressions > 0
ORDER BY metrics.clicks DESC
LIMIT 50
```
