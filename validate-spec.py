#!/usr/bin/env python3
"""Validate openapi.yaml against the live v5 agent catalog.

Checks:
  1. The spec parses as YAML and declares OpenAPI 3.0.x.
  2. info, servers and components are present.
  3. The spec's path set matches the catalog exactly: all 199 paid endpoints
     plus the free /health probe (200 paths total).
  4. Every paid operation carries x-payment-info with the right scheme,
     network, asset, amount (USDC atomic units) and payTo; /health has none.
  5. Every path parameter is declared (in: path, required: true).
  6. operationIds are unique; prices in the spec match the catalog.

Usage:
    python3 validate-spec.py                       # validate openapi.yaml vs live catalog
    python3 validate-spec.py --spec spec.yaml --input catalog.json

Exits 0 when all checks pass, 1 with a list of failures otherwise.
Requires PyYAML (pip install pyyaml). Standard library otherwise.
"""

import argparse
import json
import re
import sys
import urllib.request
from decimal import Decimal

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("PyYAML is required: pip install pyyaml")

CATALOG_URL = "https://manteclaw-x402.fly.dev/.well-known/agent-catalog.json"
USDC_ASSET = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
NETWORK = "eip155:8453"
USDC_DECIMALS = 6


def load_catalog(args):
    if args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            return json.load(fh), args.input
    req = urllib.request.Request(
        CATALOG_URL, headers={"User-Agent": "x402-openapi-validator/1.0"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp), CATALOG_URL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", default="openapi.yaml",
                        help="spec to validate (default: openapi.yaml)")
    parser.add_argument("--input", help="local catalog JSON file (default: fetch live)")
    args = parser.parse_args()

    errors = []
    with open(args.spec, "r", encoding="utf-8") as fh:
        try:
            spec = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            print(f"FAIL: {args.spec} is not valid YAML: {exc}")
            return 1

    catalog, source = load_catalog(args)
    assets = {a["endpoint"]: a for a in catalog["assets"]}
    payee = catalog["wallet_address"]

    # 1. OpenAPI version
    version = str(spec.get("openapi", ""))
    if not re.match(r"^3\.0\.\d+$", version):
        errors.append(f"openapi version is {version!r}, expected 3.0.x")

    # 2. info / servers / components
    for key in ("info", "servers", "paths", "components"):
        if key not in spec:
            errors.append(f"missing top-level key: {key}")
    if spec.get("info", {}).get("title") != "Manteclaw x402 API Service":
        errors.append("info.title is not 'Manteclaw x402 API Service'")
    servers = [s.get("url") for s in spec.get("servers", [])]
    if catalog["base_url"] not in servers:
        errors.append(f"servers does not include the catalog base_url "
                      f"{catalog['base_url']}")

    # 3. Path set matches the catalog exactly
    spec_paths = set(spec.get("paths", {}))
    catalog_paths = set(assets)
    for missing in sorted(catalog_paths - spec_paths):
        errors.append(f"missing path in spec: {missing}")
    for extra in sorted(spec_paths - catalog_paths):
        errors.append(f"extra path in spec, not in catalog: {extra}")

    # 4-6. Per-operation checks
    seen_operation_ids = {}
    paid_in_spec = 0
    for endpoint, asset in assets.items():
        path_item = spec.get("paths", {}).get(endpoint)
        if not path_item:
            continue
        op = path_item.get("get")
        if op is None:
            errors.append(f"{endpoint}: no GET operation")
            continue

        op_id = op.get("operationId")
        if not op_id:
            errors.append(f"{endpoint}: missing operationId")
        elif op_id in seen_operation_ids:
            errors.append(f"duplicate operationId {op_id!r}: {endpoint} and "
                          f"{seen_operation_ids[op_id]}")
        else:
            seen_operation_ids[op_id] = endpoint

        price = Decimal(asset["price_usdc"])
        pay_info = op.get("x-payment-info")
        if price > 0:
            paid_in_spec += 1
            if not pay_info:
                errors.append(f"{endpoint}: paid endpoint missing x-payment-info")
            else:
                expected_amount = str(int(price * (10 ** USDC_DECIMALS)))
                checks = {
                    "scheme": "exact",
                    "network": NETWORK,
                    "asset": USDC_ASSET,
                    "amount": expected_amount,
                    "payTo": payee,
                }
                for field, expected in checks.items():
                    if str(pay_info.get(field)) != expected:
                        errors.append(
                            f"{endpoint}: x-payment-info.{field} is "
                            f"{pay_info.get(field)!r}, expected {expected!r}"
                        )
                if pay_info.get("priceUsdc") != asset["price_usdc"]:
                    errors.append(
                        f"{endpoint}: x-payment-info.priceUsdc is "
                        f"{pay_info.get('priceUsdc')!r}, expected "
                        f"{asset['price_usdc']!r}"
                    )
            if "402" not in op.get("responses", {}):
                errors.append(f"{endpoint}: paid endpoint missing a 402 response")
        else:
            if pay_info:
                errors.append(f"{endpoint}: free endpoint must not have "
                              f"x-payment-info")
            if "402" in op.get("responses", {}):
                errors.append(f"{endpoint}: free endpoint must not document a "
                              f"402 response")

        # 5. Path parameters declared
        declared = {
            p["name"]
            for p in op.get("parameters", [])
            if p.get("in") == "path"
        }
        for name in re.findall(r"\{(\w+)\}", endpoint):
            if name not in declared:
                errors.append(f"{endpoint}: path parameter {{{name}}} not declared")
            else:
                param = next(
                    p for p in op["parameters"]
                    if p["name"] == name and p.get("in") == "path"
                )
                if param.get("required") is not True:
                    errors.append(f"{endpoint}: parameter {name} is not "
                                  f"required: true")

    paid_in_catalog = sum(
        1 for a in assets.values() if Decimal(a["price_usdc"]) > 0
    )
    if paid_in_spec != paid_in_catalog:
        errors.append(f"paid endpoints in spec: {paid_in_spec}, in catalog: "
                      f"{paid_in_catalog}")

    print(f"catalog source: {source}")
    print(f"spec: {args.spec}")
    print(f"paths: {len(spec_paths)} (catalog: {len(catalog_paths)}), "
          f"paid: {paid_in_spec} (catalog: {paid_in_catalog})")
    if errors:
        print(f"FAIL: {len(errors)} problem(s)")
        for err in errors:
            print(f"  - {err}")
        return 1
    print("PASS: spec is valid and matches the live catalog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
