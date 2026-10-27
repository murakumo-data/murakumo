# commerce

An example Murakumo project. It covers the TPC-H order system, NYC taxi trips and Open-Meteo
weather.

- `murakumo.yaml` declares the three sources and where their dictionaries are.
- `model/entities/<source>/` was written by `murakumo import`. Two hand edits sit on top and
  survive re-imports: range tests on `lineitem.l_discount` and `l_extendedprice`, and
  watermarks on the weather tables.
- `model/entities/marts/` was written by hand:
  - `dim_customer` keeps every version of a customer (SCD2) and joins the nation name.
  - `dim_part` is a part dimension.
  - `fct_order_line` is an incremental fact with a two-column grain and a calculated net amount.
- `generated/` is the output of `murakumo generate`. Do not edit it.

```bash
cd ../../packages/python
uv run murakumo import -p ../../examples/commerce     # refresh from the dictionaries
uv run murakumo generate -p ../../examples/commerce
uv run murakumo verify -p ../../examples/commerce
```
